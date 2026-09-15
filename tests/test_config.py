from __future__ import annotations

import pytest

from hypershell_mobile_bridge.config import Settings


def test_settings_require_strong_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HMB_DEVICE_ID", "device")
    monkeypatch.setenv("HMB_DEVICE_TOKEN", "short")
    monkeypatch.setenv("HMB_CONTROL_TOKEN", "c" * 48)
    with pytest.raises(ValueError, match="HMB_DEVICE_TOKEN"):
        Settings.from_env()


def test_settings_normalize_file_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HMB_DEVICE_ID", "device")
    monkeypatch.setenv("HMB_DEVICE_TOKEN", "d" * 48)
    monkeypatch.setenv("HMB_CONTROL_TOKEN", "c" * 48)
    monkeypatch.setenv("HMB_ALLOWED_FILE_PREFIX", "/sdcard/Download/Hypershell")
    assert Settings.from_env().allowed_file_prefix == "/sdcard/Download/Hypershell/"
