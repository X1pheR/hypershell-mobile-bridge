from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from aiohttp import ClientSession, ClientTimeout, WSMsgType


def _required(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


async def _json(response) -> dict[str, Any]:
    body = await response.json()
    if response.status >= 400:
        raise RuntimeError(f"HTTP {response.status}: {body}")
    return body


async def run(control_url: str, reverse_url: str) -> None:
    device_id = _required("HMB_DEVICE_ID")
    device_token = _required("HMB_DEVICE_TOKEN")
    control_token = _required("HMB_CONTROL_TOKEN")
    control_headers = {"Authorization": f"Bearer {control_token}"}
    portal_headers = {"Authorization": f"Bearer {device_token}", "X-Device-ID": device_id}
    timeout = ClientTimeout(total=15)
    async with ClientSession(timeout=timeout) as client:
        async with client.post(
            f"{control_url.rstrip('/')}/v1/sessions",
            headers=control_headers,
            json={"device_id": device_id, "request_id": "server-smoke"},
        ) as response:
            reserved = await _json(response)
        session_id = reserved["session"]["session_id"]
        try:
            async with client.ws_connect(reverse_url, headers=portal_headers, heartbeat=10) as portal:
                command_call = asyncio.create_task(
                    client.post(
                        f"{control_url.rstrip('/')}/v1/sessions/{session_id}/commands",
                        headers=control_headers,
                        json={"operation": "version", "params": {}},
                    )
                )
                message = await portal.receive(timeout=5)
                if message.type is not WSMsgType.TEXT:
                    raise RuntimeError(f"unexpected WebSocket message type: {message.type}")
                frame = json.loads(message.data)
                if frame.get("method") != "version" or not frame.get("id"):
                    raise RuntimeError(f"unexpected command frame: {frame}")
                await portal.send_json({"id": frame["id"], "status": "success", "result": {"version": "server-smoke"}})
                response = await command_call
                result = await _json(response)
                if result.get("portal", {}).get("result", {}).get("version") != "server-smoke":
                    raise RuntimeError(f"unexpected command result: {result}")
        finally:
            async with client.delete(
                f"{control_url.rstrip('/')}/v1/sessions/{session_id}",
                headers=control_headers,
            ) as response:
                if response.status not in {200, 404}:
                    raise RuntimeError(f"cleanup failed: HTTP {response.status}")
    print("server_smoke=passed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Server-only Hypershell Mobile Bridge smoke test")
    parser.add_argument("--control-url", default="http://127.0.0.1:8766")
    parser.add_argument("--reverse-url", default="ws://127.0.0.1:8765/v1/reverse")
    args = parser.parse_args()
    asyncio.run(run(args.control_url, args.reverse_url))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"server_smoke=failed error={type(exc).__name__}: {exc}", file=sys.stderr)
        raise
