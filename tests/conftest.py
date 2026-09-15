from __future__ import annotations

import pytest

from hypershell_mobile_bridge.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        device_id="device-123",
        device_token="d" * 48,
        control_token="c" * 48,
        pending_ttl_s=1,
        idle_ttl_s=2,
        hard_ttl_s=4,
        reconnect_ttl_s=1,
        heartbeat_s=30,
        command_timeout_s=1,
        closed_retention_s=30,
        failed_auth_limit=3,
        failed_auth_window_s=60,
        allowed_file_prefix="/sdcard/Download/Hypershell/",
        max_upload_bytes=1024,
    )
