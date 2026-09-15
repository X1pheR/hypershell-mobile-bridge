# Hypershell Mobile Bridge

Hypershell Mobile Bridge is a small self-hosted control-plane bridge for **on-demand Android computer use through Mobilerun Portal's reverse WebSocket protocol**.

It is designed for cases where an Android device should remain normally idle and low-power, then establish an authenticated outbound control session only when an automation or agent actually needs it. The phone does not expose an inbound public listener.

## Why this maintained project exists

Mobilerun Portal already provides the difficult Android-side primitives: Accessibility-based UI observation and interaction, screenshots, text input, application actions and reverse WebSocket connectivity. Hypershell needed a different **server-side session model** around those primitives:

- on-demand session reservation instead of a permanently connected control plane;
- a short authenticated admission window before a phone may connect;
- bounded idle, hard-session and reconnect lifetimes;
- separate device and controller credentials;
- typed, allowlisted control operations instead of a generic method proxy;
- explicit `unknown_outcome` handling when a mutating command loses its response;
- no persistence of screenshot, UI-tree, typed-text or file payloads;
- deterministic cleanup after intentional close, timeout or reconnect exhaustion.

That makes this repository a **maintained companion/downstream integration for Mobilerun Portal**, not a replacement for Portal and not a fork of its Android source tree. Portal remains the Android computer-use implementation and protocol origin. If Hypershell later maintains Android-side Portal changes, those belong in a separate downstream repository with the applicable upstream AGPL-3.0 provenance and source-availability obligations.

The project is published so the bridge can also be useful outside Hypershell: deployments may provide their own wake mechanism and controller integration as long as they preserve the authentication and lifecycle contract.

## Architecture

```text
Agent / automation
       |
       | private authenticated control API
       v
Hypershell Mobile Bridge
       ^
       | outbound authenticated WSS
       |
Mobilerun Portal on Android
```

In the Hypershell deployment, Home Assistant Companion is the low-power wake and phone-telemetry plane. That integration is **deployment-specific**; the Bridge itself does not require Home Assistant.

## Security model

The Bridge deliberately narrows the remote-control surface:

- Portal initiates the outbound reverse WebSocket; the phone exposes no inbound listener.
- A correct device credential is not sufficient by itself: a live pending session reservation must already exist.
- Device authentication binds a high-entropy bearer token to the configured `X-Device-ID`.
- The private control API uses an independent high-entropy bearer token.
- The public reverse-WSS and private control listeners remain separate.
- Only typed operations in `operations.py` are exposed; there is no arbitrary Portal-method passthrough.
- State-changing commands are never automatically replayed after ambiguous transport loss.
- Screenshot, UI-tree, typed-text and file bodies remain in process memory and are never written to normal logs or Bridge storage.
- Session state is intentionally memory-only and fails closed across a process restart.

See [SECURITY.md](SECURITY.md) for the security boundary and reporting guidance.

## Session defaults

| Setting | Default |
|---|---:|
| Pending admission TTL | 90 s |
| Active idle TTL | 120 s |
| Hard session lifetime | 15 min |
| Reconnect budget | 120 s |
| Active WebSocket heartbeat | 30 s |

Heartbeats never extend the idle TTL. Only real control activity does.

## Configuration

Required environment values:

- `HMB_DEVICE_ID`
- `HMB_DEVICE_TOKEN` — at least 32 characters
- `HMB_CONTROL_TOKEN` — at least 32 characters

Listeners default to:

- `127.0.0.1:8765` — reverse WebSocket/public-machine upstream
- `127.0.0.1:8766` — private control API

File operations are confined by default to `/sdcard/Download/Hypershell/`; uploads are capped at 1 MiB.

The container uses digest-pinned Chainguard Python build/runtime images. The runtime is non-root, shell-less and package-managerless.

## Control API

All control requests require `Authorization: Bearer <HMB_CONTROL_TOKEN>`.

- `POST /v1/sessions` — reserve the configured device
- `GET /v1/sessions/{id}` — read control-plane state
- `DELETE /v1/sessions/{id}` — intentional close
- `POST /v1/sessions/{id}/commands` — execute one typed allowlisted operation

The Android device connects to `GET /v1/reverse` with its device bearer and `X-Device-ID` only while a pending or reconnectable session exists.

## Container image

Release images are published to:

```text
ghcr.io/x1pher/hypershell-mobile-bridge
```

For reproducible deployments, prefer a released version and verify the resolved digest rather than tracking a moving tag.

## Verification

With `uv` available locally:

```sh
./scripts/verify.sh
```

On a Docker host without local Python tooling:

```sh
./scripts/verify-container.sh
```

Both routes compile the source/tests and run the same frozen test suite. The container verifier mounts the candidate read-only and does not broaden the production build context.

## Relationship to Mobilerun Portal

Mobilerun Portal is an independent upstream project and is not vendored here. This repository implements the server-side protocol/session boundary needed by Hypershell around Portal's documented reverse connection.

- Portal upstream: https://github.com/droidrun/mobilerun-portal
- Portal reverse-connection documentation: https://github.com/droidrun/mobilerun-portal/blob/main/docs/reverse-connection.md

Mobilerun Portal's own license and obligations apply to Portal. Hypershell Mobile Bridge is independently licensed under MIT; see [LICENSE](LICENSE).

## Project status

`v0.1.0` is the first server-side release. Android-side Hypershell Portal modifications, real-device wake/deep-idle acceptance and higher-level agent integration are separate work streams and should not be inferred from the existence of this Bridge release.
