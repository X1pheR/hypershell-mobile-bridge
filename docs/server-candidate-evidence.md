# Server Candidate Evidence

This evidence covers the server-only WP2 candidate before any Android installation or real-device admission. It does not constitute a production release or OCI deployment acceptance.

## Contract baseline

- Mobilerun Portal upstream contract pinned/re-observed at commit `d4cb7d6657385488239812e776df584f890e32fd` (v0.7.25).
- Reverse authentication uses bearer plus `X-Device-ID`; a live pending/reconnectable Bridge session is additionally required.
- Reverified command fields include `startX/startY/endX/endY/duration`, `base64_text`, `key_code`, `files/upload {path,data}`, `hideOverlay`, app/deep-link fields and `screen/keepAwake/*`.

## Source verification

- Docker-native verifier uses the digest-pinned Chainguard Python 3.14.7 builder.
- Compile + full regression suite: 26/26 passing.
- Coverage includes admission/authentication, one-session/one-transport enforcement, command correlation, transient payload handling, no generic Portal method proxy, file confinement/upload bounds, transport-loss unknown-outcome semantics, wake/idle/hard/reconnect TTL cleanup, closed-session retention and read-vs-mutation command timeout behavior.

## Runtime acceptance

The rebuilt candidate passed the server-only Portal simulator while constrained to the proposed OCI runtime envelope:

- UID/GID `65532:65532`;
- read-only root filesystem;
- writable `/tmp` only through a 16 MiB tmpfs;
- `cap_drop: ALL`, effective capabilities `0`;
- `no-new-privileges`, observed `NoNewPrivs: 1`;
- 128 MiB memory and swap limit;
- 0.25 CPU;
- PID limit 64;
- host networking with both listeners bound only to `127.0.0.1`.

Server-only end-to-end result: `server_smoke=passed`.

Observed local negative-path results:

| Check | Result |
|---|---:|
| arbitrary public path | 404 |
| reverse without bearer | 401 |
| reverse with wrong bearer | 401 |
| valid device credential without pending session | 409 |
| control health without control bearer | 401 |
| control health with valid control bearer | 200 |
| public listener | `127.0.0.1:8765` only |
| control listener | `127.0.0.1:8766` only |

The reverse failed-auth limiter deliberately never blocks a correctly authenticated device, preventing a trivial external invalid-auth lockout. A concurrent second reverse transport is rejected before HTTP 101 WebSocket upgrade.

## Security scan

Pinned cached Trivy 0.74.0 scan of the rebuilt candidate reports zero HIGH or CRITICAL vulnerabilities for both the Wolfi runtime and detected Python packages. Source secret scanning is also part of the candidate pass; no real runtime credential is stored in this tree.

## Remaining gates

- The product remains pre-first-release and non-Git. Git/GitHub/publication requires the governed first-release transition.
- OCI desired state must use the final accepted immutable release reference/provenance, not the local `candidate` image.
- Real runtime secret delivery cannot be accepted until a real Portal device identity exists; secret values never enter maintained source.
- Public Caddy/WSS acceptance remains an infrastructure deployment check after those prerequisites.
- No Android, Home Assistant Companion sensor/debugging or Portal installation changes are authorized by this evidence.
