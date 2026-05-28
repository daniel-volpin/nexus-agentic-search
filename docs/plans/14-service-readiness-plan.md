# Service Readiness Implementation Plan

**Goal:** Make the repo runnable as a real service, fix transport contract gaps, and add the minimum integration/runtime scaffolding for a larger production-readiness PR.

## Scope

1. Add a real runtime entrypoint and config bootstrap.
2. Fix HTTP and MCP transport correctness gaps.
3. Add focused integration/security regression tests.
4. Add minimal deployment scaffolding after runtime is green.

## Work Order

### 1. Runtime Bootstrap

**Files**
- Create `nexus/config.py`
- Create `nexus/logging.py`
- Create `nexus/main.py`
- Add `tests/integration/test_service_bootstrap.py`
- Update `README.md`

**Outcome**
- `uv run python -m nexus.main` starts the service instead of failing with `No module named nexus.main`.
- Runtime config is loaded from env vars for HTTP/MCP tokens and bind settings.

**Acceptance**
- `uv run pytest tests/integration/test_service_bootstrap.py -q`
- `uv run python -m nexus.main`

### 2. HTTP Transport Fixes

**Files**
- Update `nexus/http/app.py`
- Add `tests/security/test_transport_contracts.py`

**Outcome**
- `/v1/search` and `/v1/search/stream` handle malformed JSON safely.
- SSE emits events incrementally instead of buffering the full orchestrator run.

**Acceptance**
- `uv run pytest tests/security/test_transport_contracts.py -q`

### 3. MCP Transport Fixes

**Files**
- Update `nexus/mcp/server.py`
- Update `tests/unit/mcp/test_transport.py`

**Outcome**
- Orchestrator failures surface as real MCP tool errors, not successful payloads containing `{"error": ...}`.

**Acceptance**
- `uv run pytest tests/unit/mcp/test_transport.py -q`

### 4. Wire Real Dependencies

**Files**
- Update `nexus/main.py`
- Touch dependency builders only where needed in `nexus/search/`, `nexus/crawl/`, `nexus/llm/`, `nexus/orchestrator/`

**Outcome**
- Entry point builds the actual service graph instead of a placeholder app.
- Health endpoint and transport wiring work through the shared runtime path.

**Acceptance**
- `uv run pytest tests/integration/test_service_bootstrap.py -q`
- `uv run pytest -q`

### 5. Deployment Scaffolding

**Files**
- Add `Dockerfile`
- Add `compose.yaml`
- Add `.dockerignore`
- Add `searxng/settings.yml`
- Add `searxng/limiter.toml`
- Add `deploy/firewall/apply.sh`
- Add `deploy/firewall/remove.sh`
- Add `deploy/scripts/rotate-tokens.sh`

**Outcome**
- Repo contains the first runnable deployment path described by the specs.

**Acceptance**
- `uv run pytest -q`
- `docker compose config`

## PR Shape

Keep this as one larger PR, but land the work in this order:
1. bootstrap
2. transport fixes
3. runtime wiring
4. deployment scaffolding

## Non-Goals

- Broad refactors of working search/rerank/crawl internals
- Golden-query suite
- Full observability rollout
- Final production hardening beyond the minimum runnable path
