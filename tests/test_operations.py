from __future__ import annotations

import base64
import pytest

from hypershell_mobile_bridge.operations import ValidationError, build_command

PREFIX = "/sdcard/Download/Hypershell/"


def command(operation: str, params=None):
    return build_command(operation, params, allowed_file_prefix=PREFIX, max_upload_bytes=32)


def test_swipe_maps_to_upstream_camel_case() -> None:
    result = command("swipe", {"start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4, "duration_ms": 500})
    assert result.method == "swipe"
    assert result.params == {"startX": 1, "startY": 2, "endX": 3, "endY": 4, "duration": 500}
    assert result.mutating is True


def test_keyboard_text_is_encoded_without_logging_contract() -> None:
    result = command("keyboard_input", {"text": "secret-ish text", "clear": True})
    assert result.params["base64_text"] == base64.b64encode(b"secret-ish text").decode()
    assert "text" not in result.params


def test_disallows_generic_method_proxy() -> None:
    with pytest.raises(ValidationError, match="not allowed"):
        command("install", {"urls": ["https://example.invalid/app.apk"]})


def test_files_are_confined_to_hypershell_directory() -> None:
    ok = command("files_download", {"path": PREFIX + "proof.txt"})
    assert ok.method == "files/download"
    with pytest.raises(ValidationError, match="outside"):
        command("files_download", {"path": "/sdcard/DCIM/photo.jpg"})
    with pytest.raises(ValidationError):
        command("files_delete", {"path": PREFIX + "../escape"})


def test_upload_is_bounded_and_base64_validated() -> None:
    payload = base64.b64encode(b"hello").decode()
    result = command("files_upload", {"path": PREFIX + "proof.txt", "data_base64": payload})
    assert result.params["data"] == payload
    with pytest.raises(ValidationError, match="valid base64"):
        command("files_upload", {"path": PREFIX + "proof.txt", "data_base64": "%%%"})
    large = base64.b64encode(b"x" * 33).decode()
    with pytest.raises(ValidationError, match="maximum"):
        command("files_upload", {"path": PREFIX + "proof.txt", "data_base64": large})


def test_deep_link_and_package_validation() -> None:
    result = command("app_deep_link", {"deep_link": "myapp://safe/path", "package": "com.example.app"})
    assert result.method == "app/deep-link"
    with pytest.raises(ValidationError, match="package"):
        command("app_launch", {"package": "not a package"})
