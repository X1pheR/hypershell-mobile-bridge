from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace

import pytest

from hypershell_mobile_bridge.bridge import Bridge, CommandTimeout, Conflict, NotFound, SessionState, TransportLost, UnknownOutcome
from hypershell_mobile_bridge.operations import PortalCommand


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self.close_code = None
        self.close_message = None

    async def send_json(self, payload: dict) -> None:
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(payload)

    async def close(self, *, code: int, message: bytes) -> None:
        self.closed = True
        self.close_code = code
        self.close_message = message


async def active_bridge(settings):
    bridge = Bridge(settings)
    session = await bridge.reserve(settings.device_id, "req-1")
    ws = FakeWebSocket()
    await bridge.attach(session, ws)  # type: ignore[arg-type]
    return bridge, session, ws


async def test_reserve_enforces_one_open_session(settings) -> None:
    bridge = Bridge(settings)
    first = await bridge.reserve(settings.device_id)
    with pytest.raises(Conflict):
        await bridge.reserve(settings.device_id)
    await bridge.close_session(first.session_id)
    second = await bridge.reserve(settings.device_id)
    assert second.session_id != first.session_id


async def test_command_response_is_correlated_and_not_retained(settings) -> None:
    bridge, session, ws = await active_bridge(settings)
    task = asyncio.create_task(bridge.execute(session.session_id, PortalCommand("state", "state", {"filter": True}, False)))
    await asyncio.sleep(0)
    request_id = ws.sent[0]["id"]
    marker = "transient-marker-7f0d"
    await bridge.process_message(session, {"id": request_id, "status": "success", "result": {"text": marker}})
    result = await task
    assert marker in str(result)
    assert session.in_flight == {}
    assert marker not in repr(session)


async def test_mutation_transport_loss_reports_unknown_outcome(settings) -> None:
    bridge, session, ws = await active_bridge(settings)
    task = asyncio.create_task(bridge.execute(session.session_id, PortalCommand("tap", "tap", {"x": 1, "y": 2}, True)))
    await asyncio.sleep(0)
    await bridge.disconnected(session, ws)  # type: ignore[arg-type]
    with pytest.raises(UnknownOutcome):
        await task
    assert session.state is SessionState.RECONNECTING


async def test_read_transport_loss_is_not_unknown_outcome(settings) -> None:
    bridge, session, ws = await active_bridge(settings)
    task = asyncio.create_task(bridge.execute(session.session_id, PortalCommand("state", "state", {}, False)))
    await asyncio.sleep(0)
    await bridge.disconnected(session, ws)  # type: ignore[arg-type]
    with pytest.raises(TransportLost):
        await task


async def test_reconnect_does_not_reset_idle_deadline(settings) -> None:
    bridge, session, ws = await active_bridge(settings)
    original_idle = session.idle_deadline
    await bridge.disconnected(session, ws)  # type: ignore[arg-type]
    replacement = FakeWebSocket()
    candidate = await bridge.candidate_for_device(settings.device_id)
    await bridge.attach(candidate, replacement)  # type: ignore[arg-type]
    assert session.idle_deadline == original_idle


async def test_intentional_close_clears_transport(settings) -> None:
    bridge, session, ws = await active_bridge(settings)
    await bridge.close_session(session.session_id, "session_complete")
    assert session.state is SessionState.CLOSED
    assert session.websocket is None
    assert ws.closed is True
    assert ws.close_message == b"session_complete"


async def test_logs_never_contain_command_payload_or_tokens(settings, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    bridge, session, ws = await active_bridge(settings)
    secret_text = "do-not-log-this-unique-marker"
    task = asyncio.create_task(bridge.execute(session.session_id, PortalCommand("keyboard_input", "keyboard/input", {"base64_text": secret_text}, True)))
    await asyncio.sleep(0)
    request_id = ws.sent[0]["id"]
    await bridge.process_message(session, {"id": request_id, "status": "success", "result": "ok"})
    await task
    logs = caplog.text
    assert secret_text not in logs
    assert settings.device_token not in logs
    assert settings.control_token not in logs


async def test_pending_wake_timeout_is_closed_by_sweeper(settings) -> None:
    bridge = Bridge(settings)
    session = await bridge.reserve(settings.device_id)
    session.pending_deadline = time.monotonic() - 1
    await bridge.sweep_once()
    assert session.state is SessionState.CLOSED
    assert session.close_reason == "wake_timeout"


async def test_active_idle_and_hard_ttl_close_transport(settings) -> None:
    bridge, session, ws = await active_bridge(settings)
    session.idle_deadline = time.monotonic() - 1
    session.hard_deadline = time.monotonic() + 100
    await bridge.sweep_once()
    assert session.state is SessionState.CLOSED
    assert session.close_reason == "idle_ttl"
    assert ws.closed is True

    bridge2, session2, ws2 = await active_bridge(settings)
    session2.idle_deadline = time.monotonic() + 100
    session2.hard_deadline = time.monotonic() - 1
    await bridge2.sweep_once()
    assert session2.state is SessionState.CLOSED
    assert session2.close_reason == "hard_ttl"
    assert ws2.closed is True


async def test_reconnect_budget_exhaustion_closes_session(settings) -> None:
    bridge, session, ws = await active_bridge(settings)
    await bridge.disconnected(session, ws)  # type: ignore[arg-type]
    session.idle_deadline = time.monotonic() + 100
    session.hard_deadline = time.monotonic() + 100
    session.reconnect_deadline = time.monotonic() - 1
    await bridge.sweep_once()
    assert session.state is SessionState.CLOSED
    assert session.close_reason == "reconnect_exhausted"


async def test_closed_session_retention_purges_and_releases_device(settings) -> None:
    bridge = Bridge(settings)
    session = await bridge.reserve(settings.device_id)
    await bridge.close_session(session.session_id)
    session.closed_at = time.monotonic() - settings.closed_retention_s - 1
    await bridge.sweep_once()
    with pytest.raises(NotFound):
        await bridge.get(session.session_id)
    replacement = await bridge.reserve(settings.device_id)
    assert replacement.session_id != session.session_id


async def test_command_timeout_distinguishes_read_from_mutation(settings) -> None:
    fast = replace(settings, command_timeout_s=0.01)
    bridge, session, _ = await active_bridge(fast)
    with pytest.raises(CommandTimeout):
        await bridge.execute(session.session_id, PortalCommand("state", "state", {}, False))

    bridge2, session2, _ = await active_bridge(fast)
    with pytest.raises(UnknownOutcome):
        await bridge2.execute(session2.session_id, PortalCommand("tap", "tap", {"x": 1, "y": 2}, True))
