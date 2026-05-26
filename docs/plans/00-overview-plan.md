# Plan 00 — Build Order & Project Layout

> Spec: [`docs/specs/00-overview.md`](../specs/00-overview.md) · spec wins on disagreement.

## Project layout

```
.
├── pyproject.toml                # pinned deps, project metadata
├── poetry.lock | uv.lock         # lockfile (pick one tool at impl)
├── compose.yaml                  # services: nexus-search, searxng
├── Dockerfile                    # multi-stage
├── Makefile                      # test, lint, build, run, golden, load
├── .env.example                  # documentation only
├── .gitignore                    # excludes secrets/, *.env, .venv, __pycache__
├── README.md                     # 1 page: how to run, links to docs/
├── docs/                         # already exists
│   ├── specs/
│   └── plans/
├── searxng/
│   ├── settings.yml              # engine allowlist (google, duckduckgo only)
│   └── limiter.toml              # SearXNG's own rate limiter
├── secrets/
│   ├── nexus.env.example
│   └── searxng.env.example
├── deploy/
│   ├── firewall/apply.sh         # iptables script (Spec 12)
│   ├── scripts/rotate-tokens.sh
│   ├── grafana/*.json
│   └── alerts/*.yaml
├── nexus/                        # source root
│   ├── __init__.py
│   ├── main.py                   # entrypoint: starts HTTP + MCP transports
│   ├── config.py                 # pydantic-settings, env loading, validation
│   ├── logging.py                # structured JSON + redaction filter
│   ├── telemetry.py              # OTel SDK + prometheus exporter setup
│   ├── orchestrator/
│   │   ├── pipeline.py           # search→rerank→crawl→synth→validate
│   │   ├── events.py             # AnswerEvent ADT
│   │   ├── prompts.py            # synthesis system + user prompt assembly
│   │   └── budget.py             # token/time/$ enforcement
│   ├── search/
│   │   ├── client.py             # SearchClient Protocol; routing logic
│   │   ├── brave.py
│   │   ├── searxng.py            # client + per-engine circuit breaker
│   │   ├── canonical.py          # URL canonicalization
│   │   └── types.py
│   ├── rerank/
│   │   ├── bge.py
│   │   ├── dedup.py
│   │   └── diversity.py
│   ├── crawl/
│   │   ├── ssrf.py               # the guard (MUST land early)
│   │   ├── crawler.py            # Crawl4AI wrapper
│   │   ├── browser_pool.py
│   │   ├── robots.py
│   │   ├── rate_limit.py
│   │   ├── extract.py            # markdown extraction + byte offsets
│   │   ├── envelope.py           # untrusted-source wrap (canonical impl)
│   │   └── types.py
│   ├── citations/
│   │   ├── validator.py
│   │   └── normalize.py
│   ├── llm/
│   │   ├── client.py             # LLMClient Protocol
│   │   ├── litellm_adapter.py
│   │   ├── roles.py              # role→model config loader
│   │   ├── cost.py               # cost meter, daily counter
│   │   ├── redact.py             # logger filter
│   │   └── types.py
│   ├── cache/
│   │   ├── namespaces.py
│   │   └── diskcache_backend.py
│   ├── transport/
│   │   ├── http.py               # FastAPI app
│   │   ├── mcp.py                # FastMCP v2 app
│   │   └── auth.py               # shared bearer middleware
│   └── security/
│       ├── selftest.py           # egress firewall startup check
│       └── envelope.py           # re-export from crawl/envelope
└── tests/
    ├── conftest.py               # shared fixtures
    ├── unit/<component>/
    ├── integration/
    ├── security/                 # adversarial; Spec 13 catalog
    ├── golden/                   # Spec 13 golden suite (GOLDEN_LIVE=1 gated)
    └── load/
```

## Build order (component → component)

Order is chosen so each step produces a runnable, testable artifact, and so security-critical components land before anything that depends on them.

1. **Scaffolding** — `pyproject.toml`, `.gitignore`, `Makefile`, `nexus/__init__.py`, `nexus/main.py` stub, `nexus/config.py`, `nexus/logging.py` (with redaction filter from day one).
2. **Security primitives** — `nexus/crawl/ssrf.py`, `nexus/crawl/envelope.py`, `nexus/llm/redact.py`. Land WITH tests (Plan 10 catalog).
3. **Search** — `nexus/search/canonical.py`, `nexus/search/brave.py`, `nexus/search/searxng.py` (with circuit breaker), `nexus/search/client.py` (routing). (Plan 01)
4. **Rerank** — bge loader + scorer + dedup + diversity. (Plan 02)
5. **Crawl** — robots, rate limit, browser pool, Crawl4AI wrapper, extractor. All routed through SSRF guard from step 2. (Plan 03)
6. **Citations** — validator, normalization, byte-offset roundtrip. (Plan 04)
7. **LLM gateway** — `LLMClient` interface, LiteLLM adapter, role loader, cost meter. (Plan 05)
8. **Cache** — diskcache namespaces; wire into search + crawl + rerank read paths. (Plan 09)
9. **Orchestrator** — pipeline, events, prompts, budget. (Plan 06)
10. **Transports** — HTTP first (Plan 08), MCP second (Plan 07). Shared auth middleware lands once.
11. **Observability** — OTel spans across all components (instrument as you go in 3–10), Prometheus exporter wired in main.py, dashboards in `deploy/grafana/`. (Plan 11)
12. **Deployment** — Dockerfile, compose.yaml, searxng settings, firewall script, healthchecks. (Plan 12)
13. **Tests** — every prior step lands its unit + security tests with the component. Golden + load suite assembled at the end. (Plan 13)

## Milestones

| M | Deliverable | Acceptance |
|---|---|---|
| M1 | Repo scaffold + redaction logging | `make lint` clean; logger redacts test fixtures. |
| M2 | Security primitives | All SSRF + envelope unit tests pass (Spec 13 catalog). |
| M3 | End-to-end happy path on a single hardcoded query | `python -m nexus.main` returns an answer with ≥ 1 citation on `"what is openssl"`. |
| M4 | Full pipeline with caching + budgets | `make test` green; cost meter records non-zero $ per query. |
| M5 | MCP + HTTP transports | Adjacent container fixture connects via Docker DNS, gets streaming answers from both transports. |
| M6 | Containerized + firewalled | `compose up`; `egress_selftest` passes; `make test-load` green. |
| M7 | Golden suite ≥ 80% | `GOLDEN_LIVE=1 make test-golden` ≥ 80%; release gate (Spec 13) ready. |

## External-system contract surface

| External | Where | Interface |
|---|---|---|
| Brave Search API | `nexus/search/brave.py` | HTTPS REST |
| SearXNG sidecar | `nexus/search/searxng.py` | HTTP JSON on `http://searxng:8080` |
| OpenAI / Anthropic / Gemini / Ollama | `nexus/llm/litellm_adapter.py` | LiteLLM |
| Robots.txt / target webpages | `nexus/crawl/crawler.py` (via SSRF guard) | HTTPS |
| Adjacent chat-agent container | `nexus/transport/{http,mcp}.py` | HTTP / streamable HTTP MCP |

## Risks specific to ordering

- If crawl ships before SSRF + envelope land, dev iteration leaks attack surface even in tests. Step 2 lands first by mandate.
- If LLM gateway ships before cost meter, dev iteration can rack up real $ on personal keys. Cost meter is part of step 7, not deferred.
- If transports ship before auth middleware, even Docker-only deploys allow any caller on the bridge. Auth lands with the first transport.

## Done criteria for Plan 00 itself
- [ ] All paths above are created or have a `# placeholder` file committed in their place so import paths are stable.
- [ ] Each subsequent plan references this layout and does not introduce a new top-level directory.
