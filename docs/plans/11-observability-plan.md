# Plan 11 — Observability

> Spec: [`docs/specs/11-observability.md`](../specs/11-observability.md) · spec wins on disagreement.

## Module layout

```
nexus/
├── telemetry.py        # OTel SDK init, span helpers, metrics setup
├── logging.py          # structured JSON logger + redaction wiring
└── (metric/span use lives in each component file)

deploy/
├── grafana/
│   ├── overview.json
│   ├── cost.json
│   ├── crawl.json
│   ├── security.json
│   └── quality.json
└── alerts/
    └── rules.yaml      # alert definitions

tests/unit/
├── test_telemetry.py
└── test_logging.py
```

## Public symbols

```python
# nexus/telemetry.py
def setup_telemetry(service_name: str, service_version: str, metrics_port: int) -> None: ...
def tracer() -> Tracer: ...
def meter() -> Meter: ...

# Standardized counters/histograms/gauges instantiated module-level:
ORCHESTRATOR_REQUESTS_TOTAL: Counter
ORCHESTRATOR_LATENCY_MS: Histogram
ORCHESTRATOR_UNGROUNDED_TOTAL: Counter
SEARCH_LATENCY_MS: Histogram
SEARCH_ERRORS_TOTAL: Counter
SEARCH_PROVIDER_USED_TOTAL: Counter
SEARXNG_ENGINE_TRIPPED_TOTAL: Counter
SEARXNG_ENGINE_DISABLED: Gauge
RERANK_LATENCY_MS: Histogram
CRAWL_LATENCY_MS: Histogram
CRAWL_STATUS_TOTAL: Counter
CRAWL_BYTES_IN: Histogram
CRAWL_BROWSER_POOL_IN_USE: Gauge
LLM_INPUT_TOKENS_TOTAL: Counter
LLM_OUTPUT_TOKENS_TOTAL: Counter
LLM_COST_USD_TOTAL: Counter
LLM_LATENCY_MS: Histogram
LLM_BUDGET_REMAINING_USD: Gauge
CITATIONS_VALID_TOTAL: Counter
CITATIONS_REJECTED_TOTAL: Counter
CITATIONS_ENVELOPE_VIOLATIONS_TOTAL: Counter
CACHE_HIT_TOTAL: Counter
CACHE_MISS_TOTAL: Counter
HTTP_REQUESTS_TOTAL: Counter
HTTP_UNAUTHORIZED_TOTAL: Counter
MCP_TOOL_CALLS_TOTAL: Counter
MCP_UNAUTHORIZED_TOTAL: Counter

# nexus/logging.py
def setup_logging(level: str, json_format: bool = True) -> None: ...
def get_logger(name: str) -> structlog.BoundLogger: ...
```

## External dependencies

| Package | Why |
|---|---|
| `opentelemetry-api`, `opentelemetry-sdk` | Tracing primitives. |
| `opentelemetry-exporter-otlp` | OTLP exporter (off by default; enabled when an OTel collector is configured). |
| `prometheus-client` | Metrics HTTP endpoint. |
| `structlog` | Structured JSON logging. |

## Build order

1. **`logging.py`** — structlog configured to render JSON on stdout. Install `SecretRedactor` (from Plan 05) as a processor in the structlog chain AND on the root stdlib logger. Add `request_id`, `span_id`, `trace_id` context binders. ➜ `test_logging.py`: emits a record, parses stdout, asserts fields present.
2. **`telemetry.py`** — OTel TracerProvider, MeterProvider, Resource{service.name, service.version}. ConsoleSpanExporter in dev; OTLP exporter when `OTEL_EXPORTER_OTLP_ENDPOINT` env set. Prometheus exporter started on `metrics_port` bound to `0.0.0.0` inside the container (Docker bridge only). Each metric defined once at module level so import-order is stable. ➜ `test_telemetry.py`: smoke that `tracer().start_as_current_span(...)` works; metrics endpoint scrape returns Prometheus exposition.
3. **Instrumentation across components** — each component plan already enumerates the spans and metrics it must emit. This plan does NOT re-list them; it provides the helpers and ensures every component imports from `nexus.telemetry`.
4. **Context propagation** — `request_id` flows via `contextvars` from transport entry → orchestrator → all child components. Each log line includes it.
5. **Alert rules** — `deploy/alerts/rules.yaml` (Prometheus alerting format). Includes:
   - `SSRFGuardTriggered`
   - `EnvelopeViolation` (critical)
   - `UnauthorizedSpike`
   - `DailyCostNearCap`
   - `DailyCostCapHit` (critical)
   - `UngroundedAnswerSpike`
   - `CrawlFailureSpike`
   - `SearXNGEngineFlapping`
   These rules are deployment-bound; they reference metric names defined here. The repo does NOT bundle an alertmanager — left to operator.
6. **Dashboards** — five Grafana JSON files in `deploy/grafana/`. Hand-authored (or exported from a running Grafana). Reference the metrics above with the standard `service` + `version` labels.

## Performance budget

Instrumentation overhead is bounded:
- Span creation: < 10 μs.
- Histogram observation: < 1 μs.
- Logger.info with JSON serialization: < 50 μs per record on the target box.
- Aggregate target: < 5% latency added to a baseline-instrumented orchestrator request vs uninstrumented.

Measured by a microbenchmark in `tests/perf/test_telemetry_overhead.py` (optional suite).

## Configuration loading

```python
class TelemetryConfig(BaseSettings):
    service_name: str = "nexus-agentic-search"
    service_version: str = "0.0.0"      # filled in by build via env
    log_level: str = "INFO"
    json_logs: bool = True
    metrics_port: int = 9090
    metrics_bind_host: str = "0.0.0.0"
    otel_endpoint: AnyHttpUrl | None = None   # OTLP exporter; off when None
```

## Test plan (mapping to spec invariants)

| Spec invariant | Test |
|---|---|
| `request_id` propagates through every log line and span | `test_logging::test_request_id_propagation`, `test_telemetry::test_span_carries_request_id` |
| No log line / metric label / span attribute matches secret patterns | covered by Plan 05 `tests/security/test_redaction.py` |
| Metrics endpoint not reachable beyond Docker bridge | manual / `tests/integration/test_metrics_bind.py` |
| < 5% instrumentation overhead | optional `tests/perf/test_telemetry_overhead.py` |
| Logger fail-closed on filter exception | `test_logging::test_filter_failure_emits_placeholder` |

## Risks & mitigations

- **OTel SDK version churn** — pin tight; rev only on update window.
- **Prometheus client memory growth** with high-cardinality labels (e.g., `domain` label on `crawl_domain_budget_remaining`): cap with a configurable allowlist of domains tracked; everything else accumulated under `other`.
- **Metrics endpoint exposure** if someone publishes the port: explicit test that container doesn't publish 9090; documented in Spec 12.

## Done criteria
- [ ] All metrics in Spec 11's required table are wired and observable at `/metrics`.
- [ ] All spans in component specs are emitted with required attributes.
- [ ] Five Grafana dashboards committed; each references only metrics that exist.
- [ ] Alert rules YAML lints (e.g., via `promtool check rules`).
- [ ] `request_id` end-to-end: scrape logs from a test request; every line includes the same `request_id`.
- [ ] `mypy --strict` clean for `nexus/telemetry.py` and `nexus/logging.py`.
