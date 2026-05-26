# Spec 12 — Deployment

## Purpose
Describe the container, network, secret, and update model for running this service on a home server.

## Bounded context

**Does**
- Single container image, multi-stage Docker build.
- Docker Compose for service definition.
- User-defined Docker bridge network for adjacent-container communication.
- Host-level egress firewall rules for SSRF defense-in-depth.
- Secret-injection from a host-mounted `.env` file.
- Pinned image digests, manual update cadence with a regression-test gate.

**Does NOT**
- Provide a Kubernetes manifest (out of scope — home deploy).
- Operate a load balancer, reverse proxy, or TLS terminator (deferred until LAN exposure).
- Use Docker secrets / Swarm secrets / Vault.
- Auto-update.

## Container image

- Base: `python:3.12-slim-bookworm@sha256:<digest>` (pin updated monthly).
- Multi-stage:
  1. **builder**: install Poetry/uv, resolve and compile deps.
  2. **runtime**: copy resolved deps + app source; install Playwright Chromium via `playwright install chromium --with-deps`.
- Non-root user `nexus` (uid 10001, gid 10001).
- Working dir `/app`.
- Entry: `python -m nexus.main`.
- Healthcheck: `curl -fsS http://localhost:8185/v1/health || exit 1`, interval 30s.
- All Python deps pinned to exact versions in `pyproject.toml` (`==`, not `>=`).
- All apt packages pinned where possible; otherwise documented in a `THIRD_PARTY.md`.

### Pinned dependencies (initial set; revisit at impl)

| Package | Version constraint |
|---|---|
| `fastapi` | `==<latest stable>` |
| `uvicorn[standard]` | `==<latest stable>` |
| `fastmcp` | `==<latest stable>` |
| `mcp` | `==<latest stable compatible with fastmcp>` |
| `litellm` | `==<latest stable>` |
| `crawl4ai` | `==<latest stable>` |
| `playwright` | `==<crawl4ai-compatible>` |
| `httpx` | `==<latest stable>` |
| `pydantic` | `==<latest stable v2>` |
| `pydantic-settings` | `==<matching>` |
| `sentence-transformers` | `==<latest stable>` (or `FlagEmbedding` for bge-reranker) |
| `diskcache` | `==<latest stable>` |
| `orjson` | `==<latest stable>` |
| `opentelemetry-sdk` | `==<latest stable>` |
| `prometheus-client` | `==<latest stable>` |
| `tenacity` | `==<latest stable>` |

Exact versions selected at implementation time and committed to lockfile; never floating.

## Docker Compose

`compose.yaml` (sketch — exact form in plan doc):

```yaml
name: nexus

networks:
  agentic-net:
    driver: bridge
    internal: false      # crawler needs internet egress

volumes:
  nexus-cache:           # diskcache, ephemeral
  nexus-models:          # downloaded bge-reranker weights, mounted read-only after first download

services:
  nexus-search:
    image: nexus-agentic-search@sha256:<digest>
    container_name: nexus-search
    networks: [agentic-net]
    # NO host port published — adjacent containers reach via "nexus-search:8185"
    environment:
      - BIND_HOST=0.0.0.0
      - BIND_PORT=8185
      - METRICS_PORT=9090
    env_file:
      - ./secrets/nexus.env       # host-side, gitignored, 0600
    volumes:
      - nexus-cache:/var/lib/nexus/cache
      - nexus-models:/var/lib/nexus/models
    read_only: true
    tmpfs:
      - /tmp:size=512m
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    mem_limit: 4g
    cpus: "2.0"
    pids_limit: 512
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8185/v1/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

`BIND_HOST=0.0.0.0` is safe here because the container only joins `agentic-net`. No published port means no host or LAN reachability.

## Network egress firewall (host layer)

Even with the app-layer SSRF guard, the host MUST drop egress from the container to private ranges. This is defense in depth.

Approach for Docker on Linux (iptables example):

```
# Identify the container's IP/subnet on agentic-net, then:
iptables -I DOCKER-USER -s <agentic-net-subnet> -d 10.0.0.0/8     -j DROP
iptables -I DOCKER-USER -s <agentic-net-subnet> -d 172.16.0.0/12  -j DROP
iptables -I DOCKER-USER -s <agentic-net-subnet> -d 192.168.0.0/16 -j DROP
iptables -I DOCKER-USER -s <agentic-net-subnet> -d 169.254.0.0/16 -j DROP
iptables -I DOCKER-USER -s <agentic-net-subnet> -d 127.0.0.0/8    -j DROP
iptables -I DOCKER-USER -s <agentic-net-subnet> -d 100.64.0.0/10  -j DROP
# Plus IPv6 equivalents via ip6tables.
```

This script is committed at `deploy/firewall/apply.sh`. It is idempotent. It runs at host boot via systemd one-shot or at compose `up` via a wrapper script. Removal is also scripted.

**Note:** the adjacent chat-agent container also joins `agentic-net`, and the rules MUST allow traffic between containers on the bridge (Docker handles this in DOCKER chain; the rules above target egress beyond the bridge, not bridge-internal traffic).

## Secrets

`secrets/nexus.env` (host-side, gitignored, `chmod 0600`):

```
NEXUS_HTTP_TOKEN=<random 32 bytes base64>
NEXUS_MCP_TOKEN=<same value or distinct>
BRAVE_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GEMINI_API_KEY=...
DAILY_USD_BUDGET=10.00
ENABLE_QUERY_EXPANSION=false
```

Loading rules:
- `.env` is mounted by `env_file:`, not built into the image.
- The image MUST NOT contain any `.env` file.
- Tokens are generated by `deploy/scripts/rotate-tokens.sh` (writes to `secrets/nexus.env` and restarts the container).

## LAN exposure (deferred)

Activating LAN reachability later requires:
1. Add `ports: ["192.168.2.62:8185:8185"]` (or behind a reverse proxy).
2. Front with a TLS terminator (Caddy in an adjacent container is the simple path).
3. Confirm bearer auth is enabled (it is — mandatory in Spec 07/08).
4. Tighten per-IP rate limits.
5. Update firewall: still drop egress to private ranges; do NOT drop ingress on the published port.

Until then, the service is reachable ONLY from containers on `agentic-net`.

## Update cadence

- **Monthly**: pull latest minor versions of pinned deps, rebuild image, run the golden-query regression suite (Spec 13). On pass, retag image with new sha256 digest and roll. On fail, revert and file an issue.
- **Security-critical**: out-of-band update path for CVE-affected deps. Same regression gate.
- **Image digest** in `compose.yaml` is updated by the rollout script; do not edit by hand.
- **Rollback**: previous image digest retained for 30 days; `compose.yaml.prev` snapshotted before every update.

## Backup / persistence policy

- `nexus-cache` is ephemeral. No backup.
- `nexus-models` is ephemeral; weights re-downloaded after wipe via a startup task.
- Golden queries + config live in git; restoration via `git pull` + rebuild.

## Invariants

- The image runs as non-root.
- The image is read-only at runtime (`read_only: true`); writes go to `nexus-cache`, `nexus-models`, or `/tmp` (tmpfs).
- The container has no published host port unless LAN exposure is explicitly enabled.
- The container's egress to RFC1918/link-local/CGNAT is blocked at the host firewall.
- No secret is baked into the image.
- The bearer tokens have ≥ 192 bits of entropy.
- Healthcheck must pass before traffic is accepted.

## Failure modes

| Failure | Behavior |
|---|---|
| Image pull fails | Compose abort; previous container keeps running. |
| Healthcheck fails 3× | Container marked unhealthy; restart policy kicks in. |
| Firewall script not run | Container starts but egress firewall is missing — startup self-check logs CRITICAL warning detected by attempting an outbound to `10.255.255.1:81` and asserting it fails fast (refused/timeout). |
| Disk full on cache volume | Cache writes silently skipped (Spec 09); requests continue. |
| Disk full on models volume | Startup model load fails → service starts in degraded mode (rerank disabled); orchestrator falls back to provider-rank. |

## Telemetry contract

Spans emitted by the deployment layer:
- `startup.config_load` — config validation, includes which env vars were missing.
- `startup.model_load` — bge-reranker weight load.
- `startup.egress_selftest` — self-test of firewall.

Metrics
- `service_start_time_seconds` gauge.
- `service_ready` gauge (0/1).

## Out of scope / deferred

- Kubernetes manifests.
- Reverse proxy / TLS terminator.
- Centralized log collection.
- Backup automation.

## Open questions

- Whether to use Podman vs Docker — both work; pick at impl. Compose syntax is compatible enough for the basics.
- Whether to additionally drop all egress except provider-API hostnames (deny-default outbound) — strongest, but operationally fragile. Lean: drop-private-ranges only for v1.
