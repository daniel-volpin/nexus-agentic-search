# Plan 12 — Deployment

> Spec: [`docs/specs/12-deployment.md`](../specs/12-deployment.md) · spec wins on disagreement.

## Files to produce

```
Dockerfile
compose.yaml
.dockerignore
.env.example                            # documentation only
secrets/
├── nexus.env.example                   # template; real file gitignored
└── searxng.env.example
searxng/
├── settings.yml                        # engine allowlist (Spec 12)
└── limiter.toml                        # SearXNG-side rate limiter config
deploy/
├── firewall/
│   ├── apply.sh                        # iptables script (Spec 12)
│   ├── remove.sh
│   └── README.md                       # how to install + verify
├── scripts/
│   ├── rotate-tokens.sh
│   ├── build-and-pin.sh                # build image, capture digest, update compose
│   ├── rollback.sh                     # revert compose to previous digest
│   └── golden-run.sh                   # GOLDEN_LIVE=1 make test-golden
Makefile                                # test, lint, build, run, golden, load
.gitignore                              # excludes secrets/*.env, .venv, dist, etc.
```

## Build order

1. **`.gitignore`** — first commit. Excludes `secrets/*.env` (NOT `*.env.example`), `.venv/`, `__pycache__/`, `dist/`, `*.pyc`, `nexus/__pycache__`, IDE files. Ensures secrets cannot land in git by accident.
2. **`Dockerfile`** — multi-stage.
   - Builder stage: `python:3.12-slim-bookworm@sha256:<digest>`. Install `uv` (or Poetry). Resolve dependencies from `pyproject.toml` + lockfile into a venv at `/opt/venv`.
   - Runtime stage: same base. Create user `nexus` (uid 10001). Copy `/opt/venv`. Install Playwright Chromium with `playwright install chromium --with-deps` (this stage needs apt access; runs as root, then drops). Copy app source. `USER nexus`. `WORKDIR /app`. `ENTRYPOINT ["python", "-m", "nexus.main"]`.
   - `HEALTHCHECK CMD curl -fsS http://localhost:8186/v1/health || exit 1` (HTTP port, per Plan 08).
   - Labels: `org.opencontainers.image.source`, `.version`, `.revision`.
3. **`compose.yaml`** — verbatim per Spec 12 (with the SearXNG service block). Volumes `nexus-cache`, `nexus-models`. `agentic-net` user-defined bridge. No published ports. Bind-mount `searxng/settings.yml` read-only.
4. **`searxng/settings.yml`** — verbatim per Spec 12 (engine allowlist: only `google` and `duckduckgo` enabled; all others explicitly `disabled: true`). `safe_search: 0`, `formats: [json]`.
5. **`searxng/limiter.toml`** — SearXNG's own rate limiter config (defense in depth with our client-side QPS cap). Conservative defaults:
   ```toml
   [botdetection.ip_limit]
   filter_link_local = true
   link_token = false
   ```
6. **`.env.example`** — every env var name documented with comments, no real values. Mirror `nexus/config.py` settings.
7. **Secrets templates** — `secrets/nexus.env.example` lists each required key. `secrets/searxng.env.example` contains `SEARXNG_SECRET_KEY=<generated>`.
8. **`deploy/firewall/apply.sh`** — idempotent iptables script.
   - Resolves the agentic-net subnet from `docker network inspect agentic-net`.
   - Inserts DROP rules to RFC1918 / link-local / CGNAT / loopback / IPv6 ULA on the DOCKER-USER chain.
   - Resolves `www.google.com` and `html.duckduckgo.com` to current IPs; inserts ACCEPT rules for those from the SearXNG container's IP; DROPs everything else from the SearXNG container.
   - Re-runs the hostname resolution every 6h via a systemd timer or cron entry (provided in README).
   - Marks every rule with `-m comment --comment "nexus-firewall:<role>"` so `remove.sh` can clean them up by tag.
9. **`deploy/firewall/remove.sh`** — removes all rules tagged `nexus-firewall:*`.
10. **`deploy/scripts/rotate-tokens.sh`** — generates 32 random bytes via `openssl rand -base64 24`, writes `NEXUS_HTTP_TOKEN`, `NEXUS_MCP_TOKEN`, and `SEARXNG_SECRET_KEY` to `secrets/*.env` with mode 0600. Optionally `docker compose restart` if `--restart` passed.
11. **`deploy/scripts/build-and-pin.sh`** — `docker build`, capture digest from `docker inspect`, update `compose.yaml` image digest pin in place (using `sed` with a marker comment), commit `compose.yaml`.
12. **`Makefile`**:
    ```
    .PHONY: lint test test-unit test-security test-load test-golden build run stop logs
    lint:           ruff check . && mypy --strict nexus/
    test:           pytest -q tests/unit tests/integration tests/security
    test-load:      pytest -q tests/load
    test-golden:    GOLDEN_LIVE=1 pytest -q tests/golden
    build:          ./deploy/scripts/build-and-pin.sh
    run:            docker compose up -d
    stop:           docker compose down
    logs:           docker compose logs -f nexus-search
    selftest:       docker exec nexus-search python -m nexus.security.selftest
    firewall-apply: ./deploy/firewall/apply.sh
    firewall-remove:./deploy/firewall/remove.sh
    rotate-tokens:  ./deploy/scripts/rotate-tokens.sh
    ```

## Test plan (mapping to spec invariants)

| Spec invariant | Test |
|---|---|
| Image runs as non-root | `tests/integration/test_image_runtime.py::test_user_id_nonzero` (`docker exec id -u`) |
| Image read-only | `tests/integration/test_image_runtime.py::test_root_fs_readonly` |
| No published host port | `tests/integration/test_compose.py::test_no_published_ports` (`docker compose port` returns nothing) |
| No secret in image | `tests/integration/test_image_runtime.py::test_no_secrets_in_env` |
| Egress firewall blocks RFC1918 | `tests/integration/test_egress_firewall.py` runs the selftest probe |
| SearXNG engine allowlist enforced | `tests/integration/test_searxng_engines.py` queries SearXNG for engine list, asserts only google + duckduckgo enabled |
| Bearer tokens ≥ 192 bits | `tests/unit/test_config.py::test_token_length` (validates loaded config) |

## Risks & mitigations

- **Firewall script not run** on host → service runs without host-level defense. Mitigation: `nexus/security/selftest.py` detects and logs CRITICAL; alert fires; documented in README.
- **Playwright Chromium update** in a new base image breaks Crawl4AI. Mitigation: monthly update window includes golden suite regression.
- **Compose v2 vs v1 syntax drift**: target compose v2 explicitly.
- **`apt install --with-deps`** for Playwright pulls a large set; image bloat. Mitigation: documented; acceptable for self-hosted.
- **`docker network inspect`** to derive subnet for the firewall script is fragile if the network is recreated with a different CIDR. Mitigation: pin CIDR in compose (`networks.agentic-net.ipam.config.subnet`); firewall script reads from compose, not from runtime.

## Done criteria
- [ ] `docker compose up -d` brings up both services healthy.
- [ ] Adjacent test container in the same compose joins `agentic-net`, calls `http://nexus-search:8186/v1/health` → 200.
- [ ] `make firewall-apply` succeeds idempotently.
- [ ] `make selftest` passes inside the deployed container.
- [ ] No file in the built image contains a real API key (`docker run --rm <image> sh -c "env | grep -iE 'sk-|AIza'"` empty).
- [ ] Image scan (`trivy image`) returns no HIGH/CRITICAL.
- [ ] Rollback drill: `deploy/scripts/rollback.sh` reverts to prior digest, services come back healthy.
