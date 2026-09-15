# OCI Deployment Handoff

This document is an application-side handoff, **not** canonical infrastructure desired state. The canonical OCI Compose, Caddy, secret-delivery and recovery changes belong in `hypershell-infrastructure` after its current infrastructure execution unit is clear.

## Verified placement facts

- Accepted site: OCI VPS.
- Proposed public machine hostname: `mobile.oci.hypershell.eu`.
- Public DNS already resolves through `oci.hypershell.eu` to the OCI public endpoint; the internal OCI view resolves to the OCI private/site address.
- OCI Caddy runs `network_mode: host`, so it can reverse-proxy to a Bridge listener on `127.0.0.1` without publishing a Docker port.
- Ports `8765` and `8766` were observed free on OCI before deployment.
- Public TLS currently has no route/certificate for `mobile.oci.hypershell.eu`; this is expected until the Caddy owner is changed and activated.

## Runtime topology

Use a dedicated `compose/oci-vps/mobile-control/` stack after the first governed application release. Do not vendor application source into `hypershell-infrastructure`.

Expected service shape:

```yaml
services:
  mobile-bridge:
    image: ghcr.io/x1pher/hypershell-mobile-bridge:v0.1.0
    container_name: mobile-bridge
    hostname: mobile-bridge
    restart: unless-stopped
    network_mode: host
    env_file:
      - ../.runtime-secrets/mobile-control/mobile-bridge.env
    environment:
      HMB_PUBLIC_HOST: 127.0.0.1
      HMB_PUBLIC_PORT: "8765"
      HMB_CONTROL_HOST: 127.0.0.1
      HMB_CONTROL_PORT: "8766"
    read_only: true
    tmpfs:
      - /tmp:size=16m,mode=1777,uid=65532,gid=65532
    mem_limit: 128m
    memswap_limit: 128m
    cpus: 0.25
    pids_limit: 64
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    healthcheck:
      test: ["CMD", "/usr/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 5s
    labels:
      - eu.hypershell.architecture.name=Hypershell Mobile Bridge
      - eu.hypershell.architecture.type=mobile-control-bridge
      - eu.hypershell.architecture.site=oci-vps
      - dockhand.changelog.url=https://github.com/X1pheR/hypershell-mobile-bridge/releases
```

The final released image should be accepted by immutable release digest/provenance according to current release policy; the version tag above names the intended first release and is not permission to publish it.

## Public Caddy route

Only the reverse WebSocket path is public. Do not import TinyAuth and do not proxy `/healthz` or the control API.

```caddyfile
mobile.oci.hypershell.eu {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }

    @reverse path /v1/reverse
    handle @reverse {
        reverse_proxy 127.0.0.1:8765
    }

    handle {
        respond 404
    }

    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "no-referrer"
        -Server
        -X-Powered-By
    }

    log {
        output stdout
        format json
    }
}
```

Caddy handles WebSocket upgrade automatically. `Authorization` and `X-Device-ID` must reach the Bridge unchanged. Caddy access logging records request metadata, not bearer header values under the current default JSON logger; do not add header logging for this route.

## Secret contract

The runtime env file is owned by Secrets Delivery Manager and must never be committed. It needs:

- `HMB_DEVICE_ID` — exact Portal `X-Device-ID`; not knowable until Portal exists on the real device unless upstream later exposes deterministic provisioning.
- `HMB_DEVICE_TOKEN` — random high-entropy per-device bearer token, >=32 characters.
- `HMB_CONTROL_TOKEN` — independent random high-entropy control-plane bearer token, >=32 characters.

Recommended path: `.runtime-secrets/mobile-control/mobile-bridge.env`, mode `0600`, owner/group matching the OCI stack-management convention. A later Reach mobile-control tool should receive the same logical control token through its own least-privilege secret output rather than scraping the Compose env file.

## Server-only acceptance before phone setup

After the released image, secret output, Compose service and Caddy route exist, run the included simulator from the same image with **temporary test identity/credentials** before provisioning the real device:

```text
python -m hypershell_mobile_bridge.smoke \
  --control-url http://127.0.0.1:8766 \
  --reverse-url wss://mobile.oci.hypershell.eu/v1/reverse
```

Expected result: `server_smoke=passed`. This proves reserve/admission, TLS/Caddy WebSocket forwarding, header preservation, command correlation, response forwarding and intentional close without Android involvement.

Negative checks:

1. Public `/healthz`, `/v1/sessions` and arbitrary paths return 404 at Caddy.
2. `/v1/reverse` without/wrong bearer returns 401.
3. Correct device credential without a pending session returns 409.
4. Control port is listening only on OCI loopback and is unreachable over public or VCN interfaces.
5. One existing protected OCI route still enforces its TinyAuth gate after Caddy force-recreate.
6. Container remains non-root, read-only, capability-free and healthy.

## Activation boundaries

`INF-15` is complete and the canonical infrastructure source is now the released Git repository at `/srv/hypershell/repos/github/X1pheR/hypershell-infrastructure/`; that earlier concurrency blocker is retired. The Mobile Bridge deployment still must not be added as pretend production desired state using a future/local-only image identity. First complete the governed Bridge first-release transition so OCI Compose can reference an accepted stable application version and retain its resolved digest as provenance.

The real runtime secret contract also depends on the Portal-provided `X-Device-ID`; do not fabricate a production device identity merely to deploy early. P0a/P0b and Android setup remain separate real-device gates. It is valid to prepare/review the infrastructure delta before those gates, but activation and public-WSS acceptance require the accepted Bridge release plus the real secret/device provisioning inputs. No temporary Caddy/admin/runtime drift should be used to bypass these boundaries.
