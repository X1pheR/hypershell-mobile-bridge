# Hypershell Mobile Bridge

Small server-side bridge for on-demand Android computer-use through Mobilerun Portal reverse WebSocket sessions.

## Boundaries

- Portal initiates the outbound reverse WebSocket; the phone exposes no inbound listener.
- Home Assistant Companion remains the telemetry and low-power wake plane.
- The public listener accepts only authenticated reverse WebSocket sessions for a pre-reserved device session.
- The control listener is a separate loopback-only HTTP API with its own bearer token.
- Sessions and command correlation are memory-only.
- Screenshot/UI-tree/file payloads are returned to the authorized caller but are never logged or stored by the Bridge.
- There is no generic Portal-method proxy. Only the operations in `operations.py` are available.

## Default lifecycle

- pending admission TTL: 90 s
- active idle TTL: 120 s
- hard session lifetime: 15 min
- reconnect budget: 120 s
- active WebSocket heartbeat: 30 s

A heartbeat never extends the idle TTL. Only actual control commands/responses do.

## Environment

Required secrets/configuration:

- `HMB_DEVICE_ID`
- `HMB_DEVICE_TOKEN` (>=32 chars)
- `HMB_CONTROL_TOKEN` (>=32 chars)

Listeners default to `127.0.0.1:8765` (reverse WebSocket) and `127.0.0.1:8766` (private control API). See `config.py` for bounded tunables.

File operations are confined by default to `/sdcard/Download/Hypershell/` and uploads are capped at 1 MiB.

The container uses digest-pinned Chainguard Python build/runtime images. The runtime is shell-less/package-managerless and runs as the image default non-root user.

## Control API

All control requests require `Authorization: Bearer <HMB_CONTROL_TOKEN>`.

- `POST /v1/sessions` — reserve the configured device
- `GET /v1/sessions/{id}` — read control-plane state only
- `DELETE /v1/sessions/{id}` — intentional close
- `POST /v1/sessions/{id}/commands` — typed allowlisted operation

The phone connects to `GET /v1/reverse` with its per-device bearer and `X-Device-ID` only while a pending/reconnectable session exists.

## Verification

When `uv` is installed locally:

```sh
./scripts/verify.sh
```

On a Docker host without local Python tooling, use the digest-pinned builder instead:

```sh
./scripts/verify-container.sh
```

Both routes compile the source/tests and run the same frozen 26-test suite; the container route mounts the candidate read-only and does not broaden the production Docker build context.
