from __future__ import annotations

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from hypershell_mobile_bridge.bridge import Bridge, SessionState
from hypershell_mobile_bridge.service import build_control_app, build_public_app


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def clients(settings):
    bridge = Bridge(settings)
    control_server = TestServer(build_control_app(bridge, settings))
    public_server = TestServer(build_public_app(bridge, settings))
    control = TestClient(control_server)
    public = TestClient(public_server)
    await control.start_server()
    await public.start_server()
    return bridge, control, public


async def test_control_api_requires_its_own_token(settings) -> None:
    bridge, control, public = await clients(settings)
    try:
        response = await control.post("/v1/sessions", json={"device_id": settings.device_id})
        assert response.status == 401
        response = await control.post("/v1/sessions", headers=auth(settings.control_token), json={"device_id": settings.device_id})
        assert response.status == 201
    finally:
        await control.close(); await public.close(); await bridge.stop()


async def test_reverse_requires_auth_and_live_pending_reservation(settings) -> None:
    bridge, control, public = await clients(settings)
    try:
        response = await public.get("/v1/reverse", headers={"X-Device-ID": settings.device_id, **auth("wrong-token-value-that-is-long-enough")})
        assert response.status == 401
        response = await public.get("/v1/reverse", headers={"X-Device-ID": settings.device_id, **auth(settings.device_token)})
        assert response.status == 409
        reserve = await control.post("/v1/sessions", headers=auth(settings.control_token), json={"device_id": settings.device_id, "request_id": "req-1"})
        assert reserve.status == 201
        ws = await public.ws_connect("/v1/reverse", headers={"X-Device-ID": settings.device_id, **auth(settings.device_token)})
        session_id = (await reserve.json())["session"]["session_id"]
        status = await control.get(f"/v1/sessions/{session_id}", headers=auth(settings.control_token))
        assert (await status.json())["session"]["state"] == SessionState.ACTIVE.value
        second = await public.get(
            "/v1/reverse",
            headers={"X-Device-ID": settings.device_id, **auth(settings.device_token)},
        )
        assert second.status == 409
        await ws.close()
    finally:
        await control.close(); await public.close(); await bridge.stop()


async def test_failed_auth_limiter_never_blocks_valid_device_credentials(settings) -> None:
    bridge, control, public = await clients(settings)
    try:
        wrong = {"X-Device-ID": settings.device_id, **auth("wrong-token-value-that-is-long-enough")}
        for _ in range(settings.failed_auth_limit):
            response = await public.get("/v1/reverse", headers=wrong)
            assert response.status == 401
        response = await public.get("/v1/reverse", headers=wrong)
        assert response.status == 429

        reserve = await control.post(
            "/v1/sessions",
            headers=auth(settings.control_token),
            json={"device_id": settings.device_id, "request_id": "valid-after-rate-limit"},
        )
        assert reserve.status == 201
        ws = await public.ws_connect(
            "/v1/reverse",
            headers={"X-Device-ID": settings.device_id, **auth(settings.device_token)},
        )
        session_id = (await reserve.json())["session"]["session_id"]
        status = await control.get(f"/v1/sessions/{session_id}", headers=auth(settings.control_token))
        assert (await status.json())["session"]["state"] == SessionState.ACTIVE.value
        await ws.close()
    finally:
        await control.close(); await public.close(); await bridge.stop()


async def test_end_to_end_typed_command(settings) -> None:
    bridge, control, public = await clients(settings)
    try:
        reserve = await control.post("/v1/sessions", headers=auth(settings.control_token), json={"device_id": settings.device_id})
        session_id = (await reserve.json())["session"]["session_id"]
        ws = await public.ws_connect("/v1/reverse", headers={"X-Device-ID": settings.device_id, **auth(settings.device_token)})
        command_task = asyncio.create_task(control.post(f"/v1/sessions/{session_id}/commands", headers=auth(settings.control_token), json={"operation": "swipe", "params": {"start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4, "duration_ms": 500}}))
        frame = await ws.receive_json(timeout=1)
        assert frame["method"] == "swipe"
        assert frame["params"] == {"startX": 1, "startY": 2, "endX": 3, "endY": 4, "duration": 500}
        await ws.send_json({"id": frame["id"], "status": "success", "result": "ok"})
        response = await command_task
        assert response.status == 200
        body = await response.json()
        assert body["portal"]["result"] == "ok"
        await ws.close()
    finally:
        await control.close(); await public.close(); await bridge.stop()


async def test_disallowed_portal_method_cannot_be_requested(settings) -> None:
    bridge, control, public = await clients(settings)
    try:
        reserve = await control.post("/v1/sessions", headers=auth(settings.control_token), json={"device_id": settings.device_id})
        session_id = (await reserve.json())["session"]["session_id"]
        response = await control.post(f"/v1/sessions/{session_id}/commands", headers=auth(settings.control_token), json={"operation": "install", "params": {}})
        assert response.status == 400
        assert (await response.json())["error"]["code"] == "validation_error"
    finally:
        await control.close(); await public.close(); await bridge.stop()

async def test_responses_are_no_store(settings) -> None:
    bridge, control, public = await clients(settings)
    try:
        public_health = await public.get("/healthz")
        assert public_health.headers["Cache-Control"] == "no-store"
        unauthorized = await control.get("/healthz")
        assert unauthorized.status == 401
        assert unauthorized.headers["Cache-Control"] == "no-store"
    finally:
        await control.close(); await public.close(); await bridge.stop()
