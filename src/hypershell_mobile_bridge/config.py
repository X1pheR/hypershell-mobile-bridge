from __future__ import annotations

from dataclasses import dataclass
import os


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _secret_env(name: str) -> str:
    value = os.getenv(name, "")
    if len(value) < 32:
        raise ValueError(f"{name} must contain at least 32 characters")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    device_id: str
    device_token: str
    control_token: str
    public_host: str = "127.0.0.1"
    public_port: int = 8765
    control_host: str = "127.0.0.1"
    control_port: int = 8766
    pending_ttl_s: int = 90
    idle_ttl_s: int = 120
    hard_ttl_s: int = 900
    reconnect_ttl_s: int = 120
    heartbeat_s: int = 30
    command_timeout_s: int = 30
    closed_retention_s: int = 300
    failed_auth_limit: int = 20
    failed_auth_window_s: int = 60
    allowed_file_prefix: str = "/sdcard/Download/Hypershell/"
    max_upload_bytes: int = 1_048_576

    @classmethod
    def from_env(cls) -> "Settings":
        device_id = os.getenv("HMB_DEVICE_ID", "").strip()
        if not device_id or len(device_id) > 128:
            raise ValueError("HMB_DEVICE_ID must be 1..128 characters")
        prefix = os.getenv("HMB_ALLOWED_FILE_PREFIX", "/sdcard/Download/Hypershell/")
        if not prefix.startswith("/") or ".." in prefix.split("/"):
            raise ValueError("HMB_ALLOWED_FILE_PREFIX must be an absolute normalized path")
        if not prefix.endswith("/"):
            prefix += "/"
        return cls(
            device_id=device_id,
            device_token=_secret_env("HMB_DEVICE_TOKEN"),
            control_token=_secret_env("HMB_CONTROL_TOKEN"),
            public_host=os.getenv("HMB_PUBLIC_HOST", "127.0.0.1"),
            public_port=_int_env("HMB_PUBLIC_PORT", 8765, 1, 65535),
            control_host=os.getenv("HMB_CONTROL_HOST", "127.0.0.1"),
            control_port=_int_env("HMB_CONTROL_PORT", 8766, 1, 65535),
            pending_ttl_s=_int_env("HMB_PENDING_TTL_S", 90, 5, 600),
            idle_ttl_s=_int_env("HMB_IDLE_TTL_S", 120, 10, 3600),
            hard_ttl_s=_int_env("HMB_HARD_TTL_S", 900, 60, 7200),
            reconnect_ttl_s=_int_env("HMB_RECONNECT_TTL_S", 120, 5, 600),
            heartbeat_s=_int_env("HMB_HEARTBEAT_S", 30, 5, 300),
            command_timeout_s=_int_env("HMB_COMMAND_TIMEOUT_S", 30, 1, 120),
            closed_retention_s=_int_env("HMB_CLOSED_RETENTION_S", 300, 30, 3600),
            failed_auth_limit=_int_env("HMB_FAILED_AUTH_LIMIT", 20, 1, 1000),
            failed_auth_window_s=_int_env("HMB_FAILED_AUTH_WINDOW_S", 60, 1, 3600),
            allowed_file_prefix=prefix,
            max_upload_bytes=_int_env("HMB_MAX_UPLOAD_BYTES", 1_048_576, 1024, 8_388_608),
        )
