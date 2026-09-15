# Repository Agent Guide

This repository owns the generic Hypershell Mobile Bridge application. Deployment-specific hostnames, device IDs, credentials, secrets and private routing belong outside this product source.

1. Keep the Android control surface explicitly allowlisted; never add a generic Portal method passthrough.
2. Never persist screenshot/UI-tree bodies, typed text, clipboard data, file bodies, bearer tokens or raw authorization headers.
3. Treat mutating command transport loss as an unknown outcome; re-observe before retry rather than replaying automatically.
4. Keep public reverse-WSS and private control listeners separate. The control listener is loopback-only in the accepted OCI topology and still requires its own bearer token.
5. Add tests for every session/auth/reconnect or operation-contract change.
6. Run `scripts/verify.sh` against the exact candidate when `uv` is available; on Docker-only hosts use `scripts/verify-container.sh`, which runs the same frozen checks in the digest-pinned builder.
7. Do not initialize Git before the governed first-release transition.
