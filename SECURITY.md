# Security

The Mobile Bridge crosses an authenticated remote-control boundary and is intentionally narrow.

- TLS is terminated by the deployment edge; plaintext public exposure is not supported.
- Device authentication requires both the configured `X-Device-ID` and a high-entropy per-device bearer token.
- A valid credential alone cannot create a session: the server must already have a live pending reservation.
- The private control API has an independent high-entropy bearer token and is intended to bind only to loopback.
- Concurrent sessions for one device are rejected.
- State-changing commands are never automatically replayed after ambiguous transport loss.
- Command parameters are validated and the supported method set is allowlisted.
- File operations are path-confined and upload size is bounded.
- Sensitive command/result bodies and authentication material must never be emitted to logs.
- Session state is memory-only and intentionally disappears on process restart.
