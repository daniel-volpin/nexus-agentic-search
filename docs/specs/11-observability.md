# Spec 11 — Observability

## Purpose
Make the service legible at runtime: every component emits structured traces, metrics, and logs so latency, cost, quality, and security can be reasoned about without code reading.

## Bounded context

**Does**
- OpenTelemetry traces (spans + events).
- Prometheus-style metrics exposed over a local-only endpoint.
- Structured JSON logs to stdout.
- A small set of dashboards as code (Grafana JSON in `deploy/grafana/`).

**Does NOT**
- Emit telemetry to any third-party hosted service (LangSmith, Datadog, etc.) by default. Local-only.
- Persist long-term metrics — retention is whatever the local Prometheus retains.
- Implement alerting routing (alert RULES live here; routing is deployment concern).

## Trace structure

Root span on every request: `orchestrator.search`. All component work hangs under it.

```
orchestrator.search
├── search.brave
├── rerank.bge
├── crawl.fetch              (× N, one per page, in parallel)
│     ├── crawl.robots       (cached usually)
│     └── crawl.extract
├── llm.query-expansion      (optional)
├── llm.synthesis
├── citations.validate
└── cache.read / cache.write (× many)
```

Span naming: `<component>.<operation>` lowercase, dot-separated.

## Mandatory span attributes

Every span:
- `service.name = "nexus-agentic-search"`
- `service.version = <git_sha_short>`
- `request_id` (uuid4 per orchestrator request)

Component-specific attributes are defined in each component's spec; this document just states they must be present.

## Span event vs span attribute

- One-off facts → attribute on the span.
- Stage transitions inside a long-running span (e.g., `crawl.fetch` going through `dns_resolved`, `tcp_connected`, `render_started`, `extract_started`) → span events with timestamps.

## Metrics

Exposed via `/metrics` on a SEPARATE port (default 9090), bound to the Docker bridge only, no auth (metrics endpoint MUST NOT be exposed beyond the container — see Spec 12).

Standard labels on every metric: `service`, `version`. Component-specific labels per spec.

Required metrics (consolidated from component specs):

| Metric | Type | Labels |
|---|---|---|
| `orchestrator_requests_total` | counter | `final_stage` |
| `orchestrator_latency_ms` | histogram | — |
| `orchestrator_ungrounded_total` | counter | — |
| `search_latency_ms` | histogram | `provider` |
| `search_errors_total` | counter | `provider`, `reason` |
| `search_provider_used_total` | counter | `provider` (`brave`/`searxng`) |
| `searxng_engine_tripped_total` | counter | `engine` (`google`/`duckduckgo`) |
| `searxng_engine_disabled` | gauge | `engine` (1 = currently tripped, 0 = available) |
| `rerank_latency_ms` | histogram | — |
| `crawl_latency_ms` | histogram | `render_js` |
| `crawl_status_total` | counter | `status` |
| `crawl_bytes_in` | histogram | — |
| `crawl_domain_budget_remaining` | gauge | `domain` |
| `crawl_browser_pool_in_use` | gauge | — |
| `llm_input_tokens_total` | counter | `role`, `model` |
| `llm_output_tokens_total` | counter | `role`, `model` |
| `llm_cost_usd_total` | counter | `role` |
| `llm_latency_ms` | histogram | `role` |
| `llm_budget_remaining_usd` | gauge | `role` |
| `citations_valid_total` | counter | — |
| `citations_rejected_total` | counter | `reason` |
| `citations_envelope_violations_total` | counter | — |
| `cache_hit_total` | counter | `namespace` |
| `cache_miss_total` | counter | `namespace` |
| `cache_errors_total` | counter | `namespace`, `reason` |
| `http_requests_total` | counter | `route`, `status` |
| `http_unauthorized_total` | counter | — |
| `mcp_tool_calls_total` | counter | `tool`, `outcome` |
| `mcp_unauthorized_total` | counter | — |

## Logs

- Format: JSON object per line. UTC ISO-8601 timestamp. Fields: `ts`, `level`, `msg`, `request_id`, `span_id`, `trace_id`, plus structured key-values.
- Levels:
  - `DEBUG` — payload hashes, internal state transitions. OFF in production by default.
  - `INFO` — one line per orchestrator request (start + end), one per security-relevant event (SSRF reject, auth failure, envelope violation).
  - `WARN` — degraded responses, fallback used, cache write failures, schema drift.
  - `ERROR` — failures the caller saw.
- All log lines pass through the secret-redaction filter (Spec 10).
- Stack traces emitted on ERROR include the env-scrub described in Spec 10.

## Alert rules (declarative, lives in `deploy/alerts/`)

| Alert | Condition | Action |
|---|---|---|
| `SSRFGuardTriggered` | `crawl_status_total{status="blocked_by_ssrf_guard"}` rate > 0 over 5m | log + (manual review) |
| `EnvelopeViolation` | `citations_envelope_violations_total` rate > 0 over 5m | critical — possible prompt-injection success |
| `UnauthorizedSpike` | `http_unauthorized_total + mcp_unauthorized_total` rate > 5/min | alert |
| `DailyCostNearCap` | `llm_cost_usd_total` daily sum > 80% of budget | warn |
| `DailyCostCapHit` | `llm_cost_usd_total` daily sum >= budget | critical |
| `UngroundedAnswerSpike` | `orchestrator_ungrounded_total` rate > 10% of total | investigate quality |
| `CrawlFailureSpike` | crawl errors > 30% of requests over 10m | investigate |
| `SearXNGEngineFlapping` | `searxng_engine_tripped_total` for a given engine ≥ 3 over 24h | investigate ban risk; consider removing engine |

Alert routing (email, push, etc.) is configured at deploy time, not in code.

## Dashboards

Required dashboards (Grafana JSON, version-controlled):

- **Overview**: request rate, p50/p95/p99 latency, ungrounded rate, daily $ cost.
- **Cost**: per-role token + $ over time, budget remaining.
- **Crawl**: per-domain success/failure, per-domain budget, browser pool utilization.
- **Security**: SSRF rejects, envelope violations, unauthorized requests, redaction-filter triggers.
- **Quality**: citation valid/rejected, rejection reasons over time, golden-query regression status (manual update).

## Invariants

- `request_id` propagates through every log line and span attribute for a given request.
- No log line, span attribute, or metric label contains a value matching the secret-redaction patterns.
- Metrics endpoint is unreachable from outside the container's Docker bridge.
- Telemetry overhead is bounded: instrumentation MUST NOT add more than 5% latency overhead to the orchestrator path under load.

## Failure modes

| Failure | Behavior |
|---|---|
| OpenTelemetry exporter unavailable | In-process traces buffered to a bounded ring (1000 spans); on overflow, drop with a single warning per minute. |
| Prometheus scrape fails | No-op; metrics are pull-model. |
| Log filter exception | Fail-closed: emit a `[REDACTION_FILTER_FAILED]` placeholder, drop the original payload. |

## Out of scope / deferred

- Distributed tracing across containers (single-container service; trace ends at this service's boundary).
- Profile-guided optimization tracing.
- Log shipping to external systems.

## Open questions

- Whether the metrics endpoint should still be exposed (read-only) over the same Docker bridge so an adjacent monitoring container can scrape it without host-network reach. Lean yes; default bind to bridge.
- Whether to emit OpenTelemetry logs (vs structured stdout) — defer until a log collector is chosen.
