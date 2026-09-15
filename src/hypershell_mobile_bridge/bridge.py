from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import logging
import secrets
import time
from typing import Any
import uuid

from aiohttp import WSCloseCode, web

from .config import Settings
from .operations import PortalCommand

LOG = logging.getLogger("hypershell_mobile_bridge")


class SessionState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    RECONNECTING = "reconnecting"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


class BridgeError(RuntimeError):
    code = "bridge_error"


class Conflict(BridgeError):
    code = "conflict"


class NotFound(BridgeError):
    code = "not_found"


class NotActive(BridgeError):
    code = "not_active"


class TransportLost(BridgeError):
    code = "transport_lost"


class UnknownOutcome(BridgeError):
    code = "unknown_outcome"


class CommandTimeout(BridgeError):
    code = "command_timeout"


@dataclass(slots=True)
class Session:
    session_id: str
    request_id: str
    device_id: str
    created_at: datetime
    created_mono: float
    pending_deadline: float
    state: SessionState = SessionState.PENDING
    websocket: web.WebSocketResponse | None = None
    hard_deadline: float | None = None
    idle_deadline: float | None = None
    reconnect_deadline: float | None = None
    closed_at: float | None = None
    close_reason: str | None = None
    intentional_close: bool = False
    in_flight: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)


class FailedAuthLimiter:
    def __init__(self, limit: int, window_s: int) -> None:
        self.limit = limit
        self.window_s = window_s
        self._events: deque[float] = deque()

    def _trim(self, now: float) -> None:
        while self._events and self._events[0] <= now - self.window_s:
            self._events.popleft()

    def blocked(self, now: float) -> bool:
        self._trim(now)
        return len(self._events) >= self.limit

    def record(self, now: float) -> None:
        self._trim(now)
        self._events.append(now)


class Bridge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._sessions: dict[str, Session] = {}
        self._device_session: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self.failed_auth = FailedAuthLimiter(settings.failed_auth_limit, settings.failed_auth_window_s)
        self._sweeper: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._sweeper is None:
            self._sweeper = asyncio.create_task(self._sweep_loop(), name="session-sweeper")

    async def stop(self) -> None:
        task = self._sweeper
        self._sweeper = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        for session_id in list(self._sessions):
            await self.close_session(session_id, "bridge_shutdown")

    async def reserve(self, device_id: str, request_id: str | None = None) -> Session:
        if device_id != self.settings.device_id:
            raise NotFound("device is not configured")
        now = time.monotonic()
        async with self._lock:
            existing_id = self._device_session.get(device_id)
            if existing_id:
                existing = self._sessions.get(existing_id)
                if existing and existing.state not in {SessionState.CLOSED, SessionState.FAILED}:
                    if existing.state is SessionState.PENDING and now > existing.pending_deadline:
                        existing.state = SessionState.CLOSED
                        existing.closed_at = now
                        existing.close_reason = "wake_timeout"
                    else:
                        raise Conflict("device already has an open session")
            session = Session(
                session_id=str(uuid.uuid4()),
                request_id=request_id or str(uuid.uuid4()),
                device_id=device_id,
                created_at=datetime.now(UTC),
                created_mono=now,
                pending_deadline=now + self.settings.pending_ttl_s,
            )
            self._sessions[session.session_id] = session
            self._device_session[device_id] = session.session_id
            LOG.info("session_reserved session_id=%s device_id=%s", session.session_id, device_id)
            return session

    async def get(self, session_id: str) -> Session:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise NotFound("session not found")
            return session

    async def candidate_for_device(self, device_id: str) -> Session:
        now = time.monotonic()
        async with self._lock:
            session_id = self._device_session.get(device_id)
            session = self._sessions.get(session_id or "")
            if session is None:
                raise Conflict("no session is awaiting this device")
            if session.state is SessionState.PENDING:
                if now > session.pending_deadline:
                    session.state = SessionState.CLOSED
                    session.closed_at = now
                    session.close_reason = "wake_timeout"
                    raise Conflict("pending session expired")
                return session
            if session.state is SessionState.RECONNECTING:
                if session.reconnect_deadline is None or now > session.reconnect_deadline:
                    raise Conflict("reconnect window expired")
                if session.idle_deadline is not None and now > session.idle_deadline:
                    raise Conflict("session idle deadline expired")
                if session.hard_deadline is not None and now > session.hard_deadline:
                    raise Conflict("session hard deadline expired")
                return session
            raise Conflict("device is not eligible to connect")

    async def attach(self, session: Session, websocket: web.WebSocketResponse) -> None:
        now = time.monotonic()
        async with self._lock:
            current = self._sessions.get(session.session_id)
            if current is not session:
                raise Conflict("session changed during handshake")
            if session.websocket is not None:
                raise Conflict("session already has an active transport")
            first_connection = session.hard_deadline is None
            session.websocket = websocket
            session.state = SessionState.ACTIVE
            session.reconnect_deadline = None
            if first_connection:
                session.hard_deadline = now + self.settings.hard_ttl_s
                session.idle_deadline = now + self.settings.idle_ttl_s
            session.intentional_close = False
            LOG.info("session_active session_id=%s device_id=%s reconnect=%s", session.session_id, session.device_id, str(not first_connection).lower())

    async def process_message(self, session: Session, payload: Any) -> None:
        if not isinstance(payload, dict):
            raise ValueError("Portal frame must be a JSON object")
        request_id = payload.get("id")
        if request_id is None:
            # Reverse events are intentionally live-only and are not persisted.
            return
        request_key = str(request_id)
        async with self._lock:
            future = session.in_flight.get(request_key)
            if future is None or future.done():
                return
            if session.state is SessionState.ACTIVE:
                session.idle_deadline = time.monotonic() + self.settings.idle_ttl_s
            future.set_result(payload)

    async def disconnected(self, session: Session, websocket: web.WebSocketResponse) -> None:
        now = time.monotonic()
        async with self._lock:
            if session.websocket is not websocket:
                return
            session.websocket = None
            if session.intentional_close or session.state in {SessionState.CLOSING, SessionState.CLOSED, SessionState.FAILED}:
                session.state = SessionState.CLOSED
                session.closed_at = now
                session.close_reason = session.close_reason or "closed"
            elif (session.idle_deadline is not None and now >= session.idle_deadline) or (
                session.hard_deadline is not None and now >= session.hard_deadline
            ):
                session.state = SessionState.CLOSED
                session.closed_at = now
                session.close_reason = "deadline_expired"
            else:
                session.state = SessionState.RECONNECTING
                deadlines = [now + self.settings.reconnect_ttl_s]
                if session.idle_deadline is not None:
                    deadlines.append(session.idle_deadline)
                if session.hard_deadline is not None:
                    deadlines.append(session.hard_deadline)
                session.reconnect_deadline = min(deadlines)
                session.close_reason = "transport_lost"
            waiters = list(session.in_flight.values())
            session.in_flight.clear()
        for future in waiters:
            if not future.done():
                future.set_exception(TransportLost("Portal transport disconnected"))
        LOG.info("session_transport_closed session_id=%s state=%s", session.session_id, session.state.value)

    async def execute(self, session_id: str, command: PortalCommand) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        now = time.monotonic()
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise NotFound("session not found")
            if session.state is not SessionState.ACTIVE or session.websocket is None:
                raise NotActive("session is not active")
            if request_id in session.in_flight:
                raise Conflict("duplicate request id")
            websocket = session.websocket
            session.in_flight[request_id] = future
            session.idle_deadline = now + self.settings.idle_ttl_s
        sent = False
        try:
            await websocket.send_json({"id": request_id, "method": command.method, "params": command.params})
            sent = True
            response = await asyncio.wait_for(future, timeout=self.settings.command_timeout_s)
        except asyncio.TimeoutError as exc:
            if command.mutating and sent:
                raise UnknownOutcome("command response timed out after transmission") from exc
            raise CommandTimeout("command response timed out") from exc
        except TransportLost as exc:
            if command.mutating and sent:
                raise UnknownOutcome("transport was lost after a mutating command was sent") from exc
            raise
        except (ConnectionError, RuntimeError) as exc:
            if command.mutating and sent:
                raise UnknownOutcome("transport failed after a mutating command was sent") from exc
            raise TransportLost("Portal transport failed") from exc
        finally:
            async with self._lock:
                session = self._sessions.get(session_id)
                if session is not None:
                    session.in_flight.pop(request_id, None)
        if response.get("status") == "success":
            return {"status": "success", "result": response.get("result")}
        return {"status": "error", "error": response.get("error", "Portal command failed")}

    async def close_session(self, session_id: str, reason: str = "session_complete") -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise NotFound("session not found")
            if session.state in {SessionState.CLOSED, SessionState.FAILED}:
                return
            session.intentional_close = True
            session.state = SessionState.CLOSING
            session.close_reason = reason
            websocket = session.websocket
            session.websocket = None
            waiters = list(session.in_flight.values())
            session.in_flight.clear()
        for future in waiters:
            if not future.done():
                future.set_exception(TransportLost("session closed"))
        if websocket is not None and not websocket.closed:
            await websocket.close(code=WSCloseCode.OK, message=reason.encode("utf-8")[:120])
        async with self._lock:
            session.state = SessionState.CLOSED
            session.closed_at = time.monotonic()
        LOG.info("session_closed session_id=%s reason=%s", session_id, reason)

    async def status(self, session: Session) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "session_id": session.session_id,
            "request_id": session.request_id,
            "device_id": session.device_id,
            "state": session.state.value,
            "created_at": session.created_at.isoformat(),
            "pending_remaining_s": max(0, round(session.pending_deadline - now, 3)) if session.state is SessionState.PENDING else None,
            "idle_remaining_s": max(0, round(session.idle_deadline - now, 3)) if session.idle_deadline is not None and session.state in {SessionState.ACTIVE, SessionState.RECONNECTING} else None,
            "hard_remaining_s": max(0, round(session.hard_deadline - now, 3)) if session.hard_deadline is not None and session.state in {SessionState.ACTIVE, SessionState.RECONNECTING} else None,
            "reconnect_remaining_s": max(0, round(session.reconnect_deadline - now, 3)) if session.reconnect_deadline is not None and session.state is SessionState.RECONNECTING else None,
            "close_reason": session.close_reason,
        }

    async def _sweep_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1)
                await self.sweep_once()
        except asyncio.CancelledError:
            raise

    async def sweep_once(self) -> None:
        now = time.monotonic()
        close: list[tuple[str, str]] = []
        purge: list[str] = []
        async with self._lock:
            for session in self._sessions.values():
                if session.state is SessionState.PENDING and now >= session.pending_deadline:
                    close.append((session.session_id, "wake_timeout"))
                elif session.state is SessionState.ACTIVE:
                    if session.hard_deadline is not None and now >= session.hard_deadline:
                        close.append((session.session_id, "hard_ttl"))
                    elif session.idle_deadline is not None and now >= session.idle_deadline:
                        close.append((session.session_id, "idle_ttl"))
                elif session.state is SessionState.RECONNECTING:
                    if session.hard_deadline is not None and now >= session.hard_deadline:
                        close.append((session.session_id, "hard_ttl"))
                    elif session.idle_deadline is not None and now >= session.idle_deadline:
                        close.append((session.session_id, "idle_ttl"))
                    elif session.reconnect_deadline is not None and now >= session.reconnect_deadline:
                        close.append((session.session_id, "reconnect_exhausted"))
                elif session.state in {SessionState.CLOSED, SessionState.FAILED} and session.closed_at is not None:
                    if now - session.closed_at >= self.settings.closed_retention_s:
                        purge.append(session.session_id)
            for session_id in purge:
                session = self._sessions.pop(session_id, None)
                if session and self._device_session.get(session.device_id) == session_id:
                    self._device_session.pop(session.device_id, None)
        for session_id, reason in close:
            try:
                await self.close_session(session_id, reason)
            except NotFound:
                pass

    def valid_device_auth(self, device_id: str, token: str) -> bool:
        return secrets.compare_digest(device_id, self.settings.device_id) and secrets.compare_digest(token, self.settings.device_token)
