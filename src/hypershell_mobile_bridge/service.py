from __future__ import annotations

import asyncio
import json
import logging
import secrets
import signal
from typing import Any

from aiohttp import WSMsgType, web

from .bridge import Bridge, BridgeError, CommandTimeout, Conflict, NotActive, NotFound, TransportLost, UnknownOutcome
from .config import Settings
from .operations import ValidationError, build_command

LOG = logging.getLogger("hypershell_mobile_bridge")
BRIDGE_KEY = web.AppKey("bridge", Bridge)
SETTINGS_KEY = web.AppKey("settings", Settings)


def _bearer(request: web.Request) -> str:
    value = request.headers.get("Authorization", "")
    if not value.startswith("Bearer "):
        return ""
    return value[7:]


def _json_error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"status": "error", "error": {"code": code, "message": message}}, status=status)


@web.middleware
async def no_store_middleware(request: web.Request, handler):
    response = await handler(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _bridge_error(exc: BridgeError) -> web.Response:
    status = 404 if isinstance(exc, NotFound) else 409 if isinstance(exc, (Conflict, NotActive, UnknownOutcome)) else 504 if isinstance(exc, CommandTimeout) else 503 if isinstance(exc, TransportLost) else 500
    return _json_error(status, exc.code, str(exc))


def _control_authorized(request: web.Request) -> bool:
    settings: Settings = request.app[SETTINGS_KEY]
    return secrets.compare_digest(_bearer(request), settings.control_token)


async def public_health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def reverse_ws(request: web.Request) -> web.StreamResponse:
    bridge: Bridge = request.app[BRIDGE_KEY]
    settings: Settings = request.app[SETTINGS_KEY]
    now = asyncio.get_running_loop().time()
    device_id = request.headers.get("X-Device-ID", "")
    token = _bearer(request)
    if not bridge.valid_device_auth(device_id, token):
        if bridge.failed_auth.blocked(now):
            return _json_error(429, "rate_limited", "too many failed authentication attempts")
        bridge.failed_auth.record(now)
        LOG.warning("reverse_auth_rejected")
        return _json_error(401, "unauthorized", "authentication failed")
    try:
        session = await bridge.candidate_for_device(device_id)
    except BridgeError as exc:
        return _bridge_error(exc)
    websocket = web.WebSocketResponse(heartbeat=settings.heartbeat_s, autoping=True, max_msg_size=16 * 1024 * 1024)
    try:
        # Claim the one allowed transport before sending HTTP 101. Two handshakes can
        # observe the same pending session, but only one may upgrade to WebSocket.
        await bridge.attach(session, websocket)
    except BridgeError as exc:
        return _bridge_error(exc)
    try:
        await websocket.prepare(request)
        async for message in websocket:
            if message.type is WSMsgType.TEXT:
                try:
                    payload = json.loads(message.data)
                    await bridge.process_message(session, payload)
                except (json.JSONDecodeError, ValueError):
                    LOG.warning("reverse_protocol_error session_id=%s", session.session_id)
                    await websocket.close(code=1003, message=b"invalid_json_frame")
                    break
            elif message.type in {WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED}:
                break
    finally:
        await bridge.disconnected(session, websocket)
    return websocket


async def control_health(request: web.Request) -> web.Response:
    if not _control_authorized(request):
        return _json_error(401, "unauthorized", "authentication failed")
    return web.json_response({"status": "ok"})


async def reserve_session(request: web.Request) -> web.Response:
    if not _control_authorized(request):
        return _json_error(401, "unauthorized", "authentication failed")
    bridge: Bridge = request.app[BRIDGE_KEY]
    settings: Settings = request.app[SETTINGS_KEY]
    try:
        body = await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest):
        return _json_error(400, "invalid_json", "request body must be JSON")
    if not isinstance(body, dict) or set(body) - {"device_id", "request_id"}:
        return _json_error(400, "invalid_request", "body may contain only device_id and request_id")
    device_id = body.get("device_id")
    request_id = body.get("request_id")
    if device_id != settings.device_id:
        return _json_error(404, "device_not_found", "device is not configured")
    if request_id is not None and (not isinstance(request_id, str) or not request_id or len(request_id) > 128):
        return _json_error(400, "invalid_request", "request_id must be a non-empty string up to 128 characters")
    try:
        session = await bridge.reserve(device_id, request_id)
    except BridgeError as exc:
        return _bridge_error(exc)
    return web.json_response({"status": "success", "session": await bridge.status(session)}, status=201)


async def session_status(request: web.Request) -> web.Response:
    if not _control_authorized(request):
        return _json_error(401, "unauthorized", "authentication failed")
    bridge: Bridge = request.app[BRIDGE_KEY]
    try:
        session = await bridge.get(request.match_info["session_id"])
    except BridgeError as exc:
        return _bridge_error(exc)
    return web.json_response({"status": "success", "session": await bridge.status(session)})


async def close_session(request: web.Request) -> web.Response:
    if not _control_authorized(request):
        return _json_error(401, "unauthorized", "authentication failed")
    bridge: Bridge = request.app[BRIDGE_KEY]
    try:
        await bridge.close_session(request.match_info["session_id"], "session_complete")
        session = await bridge.get(request.match_info["session_id"])
    except BridgeError as exc:
        return _bridge_error(exc)
    return web.json_response({"status": "success", "session": await bridge.status(session)})


async def execute_command(request: web.Request) -> web.Response:
    if not _control_authorized(request):
        return _json_error(401, "unauthorized", "authentication failed")
    bridge: Bridge = request.app[BRIDGE_KEY]
    settings: Settings = request.app[SETTINGS_KEY]
    try:
        body = await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest):
        return _json_error(400, "invalid_json", "request body must be JSON")
    if not isinstance(body, dict) or set(body) - {"operation", "params"}:
        return _json_error(400, "invalid_request", "body may contain only operation and params")
    operation = body.get("operation")
    if not isinstance(operation, str):
        return _json_error(400, "invalid_request", "operation must be a string")
    try:
        command = build_command(operation, body.get("params"), allowed_file_prefix=settings.allowed_file_prefix, max_upload_bytes=settings.max_upload_bytes)
        result = await bridge.execute(request.match_info["session_id"], command)
    except ValidationError as exc:
        return _json_error(400, "validation_error", str(exc))
    except BridgeError as exc:
        return _bridge_error(exc)
    # The result is returned to the authorized caller but is never logged or persisted.
    return web.json_response({"status": "success", "operation": operation, "portal": result})


def build_public_app(bridge: Bridge, settings: Settings) -> web.Application:
    app = web.Application(client_max_size=64 * 1024, middlewares=[no_store_middleware])
    app[BRIDGE_KEY] = bridge
    app[SETTINGS_KEY] = settings
    app.router.add_get("/healthz", public_health)
    app.router.add_get("/v1/reverse", reverse_ws)
    return app


def build_control_app(bridge: Bridge, settings: Settings) -> web.Application:
    app = web.Application(client_max_size=max(settings.max_upload_bytes * 2 + 4096, 128 * 1024), middlewares=[no_store_middleware])
    app[BRIDGE_KEY] = bridge
    app[SETTINGS_KEY] = settings
    app.router.add_get("/healthz", control_health)
    app.router.add_post("/v1/sessions", reserve_session)
    app.router.add_get("/v1/sessions/{session_id}", session_status)
    app.router.add_delete("/v1/sessions/{session_id}", close_session)
    app.router.add_post("/v1/sessions/{session_id}/commands", execute_command)
    return app


async def serve(settings: Settings) -> None:
    bridge = Bridge(settings)
    await bridge.start()
    public_runner = web.AppRunner(build_public_app(bridge, settings), access_log=None)
    control_runner = web.AppRunner(build_control_app(bridge, settings), access_log=None)
    await public_runner.setup()
    await control_runner.setup()
    public_site = web.TCPSite(public_runner, settings.public_host, settings.public_port)
    control_site = web.TCPSite(control_runner, settings.control_host, settings.control_port)
    await public_site.start()
    await control_site.start()
    LOG.info("bridge_started public=%s:%s control=%s:%s", settings.public_host, settings.public_port, settings.control_host, settings.control_port)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    await stop_event.wait()
    await bridge.stop()
    await control_runner.cleanup()
    await public_runner.cleanup()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
