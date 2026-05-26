# Plan 13 — Testing

> Spec: [`docs/specs/13-testing.md`](../specs/13-testing.md) · spec wins on disagreement.

## Files to produce

```
tests/
├── conftest.py                 # shared fixtures, event-loop policy, secret-redaction asserts
├── unit/                       # per-component (one subdir each)
│   ├── search/
│   ├── rerank/
│   ├── crawl/
│   ├── citations/
│   ├── llm/
│   ├── cache/
│   ├── orchestrator/
│   ├── transport/
│   ├── test_config.py
│   ├── test_logging.py
│   ├── test_telemetry.py
│   └── test_specs_have_tests.py   # meta-test
├── integration/
│   ├── test_search_fallback.py
│   ├── test_orchestrator_e2e.py
│   ├── test_crawl_e2e.py
│   ├── test_cache_e2e.py
│   ├── test_image_runtime.py
│   ├── test_compose.py
│   ├── test_egress_firewall.py
│   ├── test_searxng_engines.py
│   └── test_metrics_bind.py
├── security/                   # adversarial; Spec 13 §Adversarial
│   ├── test_ssrf.py
│   ├── test_envelope.py
│   ├── test_citations.py
│   ├── test_redaction.py
│   ├── test_budget.py
│   ├── test_auth.py
│   ├── test_mcp_surface.py
│   ├── test_http_surface.py
│   ├── test_prompt_assembly.py
│   ├── test_cache_secrets.py
│   └── fixtures/
│       ├── dns_server.py       # local UDP DNS server for rebinding tests
│       ├── webserver.py        # local HTTP server returning crafted pages
│       ├── ssrf_redirects.py   # 302 emitter
│       ├── searxng_captcha.py  # fixture mimicking captcha pages
│       └── hostile_pages/      # static html with hidden-CSS instructions, etc.
├── golden/
│   ├── queries.yaml            # 20+ curated queries with assertions
│   ├── conftest.py             # GOLDEN_LIVE gate
│   └── test_golden.py
├── load/
│   ├── test_browser_pool_cap.py
│   ├── test_concurrent_requests.py
│   └── test_memory_stability.py
└── perf/                        # optional; not in default suite
    ├── test_rerank_baseline.py
    └── test_telemetry_overhead.py
```

## Shared fixtures (`tests/conftest.py`)

- `event_loop_policy` for `pytest-asyncio`.
- `tmp_cache_root`: temp dir for diskcache.
- `mock_brave_client`: returns canned `SearchResponse`.
- `mock_searxng_client`: same.
- `local_dns_server`: spins up a UDP DNS server bound to 127.0.0.1 on a random port; tests configure `aiodns` / `socket.getaddrinfo` to use it.
- `local_webserver`: aiohttp app serving `tests/security/fixtures/hostile_pages/`.
- `fake_llm`: `LLMClient` stub returning programmable responses for `synthesis`, `rerank-decision`, `query-expansion`.
- `request_id_factory`: deterministic uuids for snapshot-friendly tests.
- `asserts_no_secret_in_logs`: autouse fixture; captures all log records during the test, fails the test if any record matches a secret pattern after redaction.

## Meta-test (`tests/unit/test_specs_have_tests.py`)

Walks `docs/specs/*.md`. For each spec file, parses out invariant bullet points and failure-mode rows. For each, asserts that a test exists with a corresponding name (loosely: matches a `test_<keyword>` somewhere under `tests/`). This is a fuzzy guard against spec drift; failures point at unmapped invariants.

Implementation:
- Extract text under `## Invariants` and `## Failure modes` headings.
- Generate "expected keywords" from those entries.
- Recursively scan `tests/` for matching `def test_*` names.
- Each unmapped invariant gets a single warning; > N warnings → test failure.

## Adversarial test catalog (from Spec 13)

Already enumerated in the spec; each listed item lives in the corresponding `tests/security/test_*.py`. This plan's role is to ensure they all land.

### Local fixture servers

- `tests/security/fixtures/dns_server.py`: an `asyncio` UDP server returning crafted A/AAAA. Used by SSRF rebinding tests.
- `tests/security/fixtures/webserver.py`: aiohttp app exposing crafted pages — hidden CSS instructions, redirect to RFC1918, oversized body, JS that probes `192.168.0.0/16`, etc.
- `tests/security/fixtures/searxng_captcha.py`: mimics SearXNG returning a `sorry/index` redirect for one of the engines.
- `tests/security/fixtures/hostile_pages/*.html`: static fixtures committed.

## Golden suite (`tests/golden/`)

- `queries.yaml`: 20 queries to start. Each entry: `query`, optional `freshness`, `must_cite_any_of` (list of domains), `must_not_say` (list of strings), `min_citations`.
- `conftest.py`: requires `GOLDEN_LIVE=1`; skips otherwise. Loads API keys from env; aborts if any missing with a clear message.
- `test_golden.py`: invokes the orchestrator end-to-end via HTTP transport against the running container. Asserts assertions per entry. Aggregates pass rate; fails if < 80% OR if regression > 2 queries from `tests/golden/baseline.json`.

The first run after a stable build commits `baseline.json` with the current pass set.

## Load suite (`tests/load/`)

- `test_browser_pool_cap.py`: launches 16 crawl tasks; asserts ≤ 4 contexts active simultaneously; surplus wait ≤ 5s.
- `test_concurrent_requests.py`: 5 concurrent orchestrator requests; asserts no exception, all events delivered, total RSS stable.
- `test_memory_stability.py`: 1000 sequential crawls of small pages; asserts process RSS gain < 200 MB.

## CI / Make targets

Per Plan 12 Makefile:

- `make lint` — ruff + mypy.
- `make test` — unit + integration + security (default; no live LLM, no live web).
- `make test-load` — load suite; assumes a running container.
- `make test-golden` — golden suite; requires API keys + running container.
- `make test-providers` — optional cross-provider smoke (OpenAI + Anthropic + Gemini); skipped unless all keys set.

## Release gate (Spec 13 §Release gate)

Promotion of an image digest requires the checklist in Spec 13. This plan ensures the targets exist and that `tests/SKIPS.md` exists for justified skips.

## Risks & mitigations

- **Flaky fixture DNS server** under heavy CI parallelism: each test using it claims a random port; failures debugged not retried.
- **Golden suite drift** when models or web content change: `must_cite_any_of` uses domain-level matching, not exact-URL, to absorb minor content moves. `must_not_say` is conservative.
- **Live tests cost money**: `GOLDEN_LIVE=1` gate is loud; CI defaults to off.
- **Long load tests slow dev cycle**: load tests are `make test-load`, not `make test`.

## Done criteria
- [ ] Every plan's `tests/` references exist as actual test files.
- [ ] `make test` runs in < 2 minutes locally without network.
- [ ] `test_specs_have_tests.py` passes (no orphan invariants).
- [ ] `tests/security/` adversarial catalog passes 100%.
- [ ] Golden suite baseline committed; subsequent runs compare against it.
- [ ] `tests/SKIPS.md` exists (empty or with justified entries).
