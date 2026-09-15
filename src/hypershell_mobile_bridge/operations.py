from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any, Callable


class ValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PortalCommand:
    operation: str
    method: str
    params: dict[str, Any]
    mutating: bool


_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
_ACTION_RE = re.compile(r"^[A-Za-z0-9_.]+$")


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError("params must be an object")
    return value


def _unknown(params: dict[str, Any], allowed: set[str]) -> None:
    extra = set(params) - allowed
    if extra:
        raise ValidationError(f"unknown params: {', '.join(sorted(extra))}")


def _bool(params: dict[str, Any], key: str, default: bool | None = None) -> bool:
    if key not in params:
        if default is None:
            raise ValidationError(f"{key} is required")
        return default
    value = params[key]
    if not isinstance(value, bool):
        raise ValidationError(f"{key} must be boolean")
    return value


def _int(params: dict[str, Any], key: str, minimum: int, maximum: int, default: int | None = None) -> int:
    if key not in params:
        if default is None:
            raise ValidationError(f"{key} is required")
        return default
    value = params[key]
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValidationError(f"{key} must be an integer between {minimum} and {maximum}")
    return value


def _str(params: dict[str, Any], key: str, max_length: int, optional: bool = False) -> str | None:
    if key not in params:
        if optional:
            return None
        raise ValidationError(f"{key} is required")
    value = params[key]
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValidationError(f"{key} must be a non-empty string up to {max_length} characters")
    return value


def _package(params: dict[str, Any], key: str = "package", optional: bool = False) -> str | None:
    value = _str(params, key, 255, optional=optional)
    if value is not None and not _PACKAGE_RE.fullmatch(value):
        raise ValidationError(f"{key} is not a valid Android package name")
    return value


def _safe_file_path(path: str, allowed_prefix: str) -> str:
    if not path.startswith(allowed_prefix):
        raise ValidationError("file path is outside the allowed Hypershell directory")
    suffix = path[len(allowed_prefix):]
    if not suffix or ".." in suffix.split("/") or "\x00" in suffix:
        raise ValidationError("file path must name an object below the allowed Hypershell directory")
    return path


def _no_params(operation: str, method: str, params: dict[str, Any], mutating: bool) -> PortalCommand:
    _unknown(params, set())
    return PortalCommand(operation, method, {}, mutating)


def build_command(operation: str, raw_params: Any, *, allowed_file_prefix: str, max_upload_bytes: int) -> PortalCommand:
    params = _mapping(raw_params)

    if operation == "state":
        _unknown(params, {"filter"})
        return PortalCommand(operation, "state", {"filter": _bool(params, "filter", True)}, False)
    if operation == "screenshot":
        _unknown(params, {"hide_overlay"})
        return PortalCommand(operation, "screenshot", {"hideOverlay": _bool(params, "hide_overlay", True)}, False)
    if operation == "packages":
        return _no_params(operation, "packages", params, False)
    if operation == "version":
        return _no_params(operation, "version", params, False)
    if operation == "time":
        return _no_params(operation, "time", params, False)
    if operation == "tap":
        _unknown(params, {"x", "y"})
        return PortalCommand(operation, "tap", {"x": _int(params, "x", 0, 20_000), "y": _int(params, "y", 0, 20_000)}, True)
    if operation == "swipe":
        _unknown(params, {"start_x", "start_y", "end_x", "end_y", "duration_ms"})
        mapped = {
            "startX": _int(params, "start_x", 0, 20_000),
            "startY": _int(params, "start_y", 0, 20_000),
            "endX": _int(params, "end_x", 0, 20_000),
            "endY": _int(params, "end_y", 0, 20_000),
            "duration": _int(params, "duration_ms", 1, 10_000, 300),
        }
        return PortalCommand(operation, "swipe", mapped, True)
    if operation == "global_action":
        _unknown(params, {"action"})
        return PortalCommand(operation, "global", {"action": _int(params, "action", 1, 100)}, True)
    if operation == "keyboard_input":
        _unknown(params, {"text", "clear"})
        text = _str(params, "text", 4096)
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        return PortalCommand(operation, "keyboard/input", {"base64_text": encoded, "clear": _bool(params, "clear", True)}, True)
    if operation == "keyboard_clear":
        return _no_params(operation, "keyboard/clear", params, True)
    if operation == "keyboard_key":
        _unknown(params, {"key_code"})
        return PortalCommand(operation, "keyboard/key", {"key_code": _int(params, "key_code", 0, 400)}, True)
    if operation == "app_launch":
        _unknown(params, {"package", "activity", "stop_before_launch"})
        mapped: dict[str, Any] = {
            "package": _package(params),
            "stopBeforeLaunch": _bool(params, "stop_before_launch", False),
        }
        activity = _str(params, "activity", 512, optional=True)
        if activity is not None:
            mapped["activity"] = activity
        return PortalCommand(operation, "app", mapped, True)
    if operation == "app_deep_link":
        _unknown(params, {"deep_link", "display_id", "package", "action"})
        mapped = {
            "deepLink": _str(params, "deep_link", 2048),
            "displayId": _int(params, "display_id", 0, 32, 0),
        }
        package = _package(params, optional=True)
        if package is not None:
            mapped["package"] = package
        action = _str(params, "action", 255, optional=True)
        if action is not None:
            if not _ACTION_RE.fullmatch(action):
                raise ValidationError("action contains invalid characters")
            mapped["action"] = action
        return PortalCommand(operation, "app/deep-link", mapped, True)
    if operation == "app_stop":
        _unknown(params, {"package"})
        return PortalCommand(operation, "app/stop", {"package": _package(params)}, True)
    if operation in {"files_list", "files_download", "files_delete"}:
        _unknown(params, {"path"})
        path = _safe_file_path(_str(params, "path", 1024), allowed_file_prefix)
        method = {"files_list": "files/list", "files_download": "files/download", "files_delete": "files/delete"}[operation]
        return PortalCommand(operation, method, {"path": path}, operation == "files_delete")
    if operation == "files_upload":
        _unknown(params, {"path", "data_base64"})
        path = _safe_file_path(_str(params, "path", 1024), allowed_file_prefix)
        data = _str(params, "data_base64", max_upload_bytes * 2)
        try:
            decoded = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValidationError("data_base64 must be valid base64") from exc
        if len(decoded) > max_upload_bytes:
            raise ValidationError("upload exceeds maximum allowed size")
        return PortalCommand(operation, "files/upload", {"path": path, "data": data}, True)
    if operation == "keep_awake_set":
        _unknown(params, {"enabled"})
        return PortalCommand(operation, "screen/keepAwake/set", {"enabled": _bool(params, "enabled")}, True)
    if operation == "keep_awake_status":
        return _no_params(operation, "screen/keepAwake/status", params, False)

    raise ValidationError("operation is not allowed")
