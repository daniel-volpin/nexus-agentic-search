"""Tracing + metrics primitives.

OpenTelemetry is used for tracing; ``prometheus_client`` is used for
metrics exposed over an HTTP scrape endpoint. The two coexist cleanly
and each is a better fit for its job at our scale.

Bootstrapping
-------------
:func:`setup_telemetry` configures the SDK and optionally starts the
metrics HTTP server. It is idempotent — calling twice replaces the
providers. Tests rely on this.

Until :func:`setup_telemetry` runs, the SDK uses its default
no-op providers and span calls are cheap. Modules can safely call
:func:`get_tracer` at import time.

Every span carries ``service.name``, ``service.version`` (from the SDK
Resource) and ``request_id`` (added by :func:`bind_request_id` via
:data:`request_id_var`).

Metrics
-------
Standard counters/histograms/gauges live as module-level instruments,
so any component can ``from nexus.telemetry import ORCHESTRATOR_LATENCY_MS``
and observe without further setup.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Final

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

logger = logging.getLogger(__name__)

# ---------- request id ----------

request_id_var: ContextVar[str | None] = ContextVar("nexus_request_id", default=None)


def new_request_id() -> str:
    """Generate a fresh request id (uuid4 hex, 32 chars)."""
    return uuid.uuid4().hex


def bind_request_id(value: str | None = None) -> str:
    """Set the request id for the current context.

    Returns the value actually bound. If ``value`` is ``None`` a fresh
    id is generated. Use the return value when you need to propagate
    the id to a response header or downstream call.
    """
    bound = value or new_request_id()
    request_id_var.set(bound)
    return bound


def get_request_id() -> str | None:
    return request_id_var.get()


# ---------- tracer ----------


def get_tracer(name: str) -> trace.Tracer:
    """Return a tracer for ``name`` (typically ``__name__``).

    Until :func:`setup_telemetry` runs this returns a no-op tracer; calls
    succeed and cost ~nothing.
    """
    return trace.get_tracer(name)


# ---------- standard metric instruments ----------
#
# Defined once at module import. Callers do not instantiate; they import
# and use. Bucket boundaries are a starting profile — revisit after
# measuring real traffic.

_LATENCY_BUCKETS_MS: Final[tuple[float, ...]] = (
    1,
    5,
    10,
    25,
    50,
    100,
    250,
    500,
    1000,
    2500,
    5000,
    10000,
    30000,
    60000,
)
_TOKEN_BUCKETS: Final[tuple[float, ...]] = (100, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000)
_BYTES_BUCKETS: Final[tuple[float, ...]] = (1024, 10240, 102400, 524288, 1048576, 4194304, 16777216)
_COUNT_BUCKETS: Final[tuple[float, ...]] = (0, 1, 2, 3, 5, 8, 13)

# Orchestrator
ORCHESTRATOR_REQUESTS_TOTAL = Counter(
    "orchestrator_requests_total",
    "Total orchestrator requests, labelled by terminal stage.",
    ["final_stage"],
)
ORCHESTRATOR_LATENCY_MS = Histogram(
    "orchestrator_latency_ms",
    "End-to-end orchestrator latency in milliseconds.",
    buckets=_LATENCY_BUCKETS_MS,
)
ORCHESTRATOR_UNGROUNDED_TOTAL = Counter(
    "orchestrator_ungrounded_total",
    "Requests that produced an answer with zero validated citations.",
)
ORCHESTRATOR_PAGES_OK = Histogram(
    "orchestrator_pages_ok",
    "Pages successfully crawled per request.",
    buckets=_COUNT_BUCKETS,
)
ORCHESTRATOR_PAGES_FAILED = Histogram(
    "orchestrator_pages_failed",
    "Pages that failed to crawl per request.",
    buckets=_COUNT_BUCKETS,
)

# Search
SEARCH_LATENCY_MS = Histogram(
    "search_latency_ms",
    "Search provider call latency in milliseconds.",
    ["provider"],
    buckets=_LATENCY_BUCKETS_MS,
)
SEARCH_ERRORS_TOTAL = Counter(
    "search_errors_total",
    "Search provider failures, by reason.",
    ["provider", "reason"],
)
SEARCH_PROVIDER_USED_TOTAL = Counter(
    "search_provider_used_total",
    "Which search provider(s) served a request.",
    ["provider"],
)
SEARXNG_ENGINE_TRIPPED_TOTAL = Counter(
    "searxng_engine_tripped_total",
    "SearXNG engine circuit-breaker trips.",
    ["engine"],
)
SEARXNG_ENGINE_DISABLED = Gauge(
    "searxng_engine_disabled",
    "1 if a SearXNG engine is currently tripped/disabled, else 0.",
    ["engine"],
)

# Rerank
RERANK_LATENCY_MS = Histogram(
    "rerank_latency_ms",
    "Rerank scoring latency in milliseconds.",
    buckets=_LATENCY_BUCKETS_MS,
)

# Crawl
CRAWL_LATENCY_MS = Histogram(
    "crawl_latency_ms",
    "Single-URL crawl latency in milliseconds.",
    ["render_js"],
    buckets=_LATENCY_BUCKETS_MS,
)
CRAWL_STATUS_TOTAL = Counter(
    "crawl_status_total",
    "Crawl outcomes by status (ok / blocked_by_ssrf_guard / timeout / …).",
    ["status"],
)
CRAWL_BYTES_IN = Histogram(
    "crawl_bytes_in",
    "Bytes received per crawl.",
    buckets=_BYTES_BUCKETS,
)
CRAWL_BROWSER_POOL_IN_USE = Gauge(
    "crawl_browser_pool_in_use",
    "Currently-active browser contexts.",
)

# LLM
LLM_INPUT_TOKENS_TOTAL = Counter(
    "llm_input_tokens_total",
    "LLM input tokens consumed.",
    ["role", "model"],
)
LLM_OUTPUT_TOKENS_TOTAL = Counter(
    "llm_output_tokens_total",
    "LLM output tokens produced.",
    ["role", "model"],
)
LLM_COST_USD_TOTAL = Counter(
    "llm_cost_usd_total",
    "LLM spend in USD.",
    ["role"],
)
LLM_LATENCY_MS = Histogram(
    "llm_latency_ms",
    "LLM call latency in milliseconds.",
    ["role"],
    buckets=_LATENCY_BUCKETS_MS,
)
LLM_BUDGET_REMAINING_USD = Gauge(
    "llm_budget_remaining_usd",
    "Remaining USD budget for the day, per role.",
    ["role"],
)
LLM_INPUT_TOKEN_DISTRIBUTION = Histogram(
    "llm_input_token_distribution",
    "Distribution of per-call input token counts.",
    ["role"],
    buckets=_TOKEN_BUCKETS,
)
LLM_ERRORS_TOTAL = Counter(
    "llm_errors_total",
    "LLM call failures, by role and reason.",
    ["role", "reason"],
)

# Citations
CITATIONS_VALID_TOTAL = Counter(
    "citations_valid_total",
    "Citations that passed validation.",
)
CITATIONS_REJECTED_TOTAL = Counter(
    "citations_rejected_total",
    "Citations rejected during validation, by reason.",
    ["reason"],
)
CITATIONS_ENVELOPE_VIOLATIONS_TOTAL = Counter(
    "citations_envelope_violations_total",
    "Citations rejected because the quote appeared outside any untrusted-source envelope.",
)

# Cache
CACHE_HIT_TOTAL = Counter("cache_hit_total", "Cache hits.", ["namespace"])
CACHE_MISS_TOTAL = Counter("cache_miss_total", "Cache misses.", ["namespace"])
CACHE_ERRORS_TOTAL = Counter(
    "cache_errors_total",
    "Cache backend errors converted to misses or dropped writes.",
    ["namespace", "reason"],
)

# HTTP transport
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "HTTP requests by route and status code.",
    ["route", "status"],
)
HTTP_UNAUTHORIZED_TOTAL = Counter(
    "http_unauthorized_total",
    "401 responses on the HTTP surface.",
)

# MCP transport
MCP_TOOL_CALLS_TOTAL = Counter(
    "mcp_tool_calls_total",
    "MCP tool invocations by tool name and outcome.",
    ["tool", "outcome"],
)
MCP_UNAUTHORIZED_TOTAL = Counter(
    "mcp_unauthorized_total",
    "Rejected MCP connections lacking valid bearer auth.",
)


# ---------- telemetry sink ----------
#
# The orchestrator and LLM client emit through the abstract
# ``LLMTelemetrySink`` interface (string-named counters/histograms/gauges)
# so they stay testable with an in-memory double. This sink is the
# production adapter that forwards those calls to the module-level
# Prometheus instruments and the OTel tracer. Names are mapped explicitly
# so a typo at a call site is a silent no-op (logged at debug), never a
# crash — telemetry must not fail the request path.

_COUNTERS: Final[dict[str, Counter]] = {
    "orchestrator_requests_total": ORCHESTRATOR_REQUESTS_TOTAL,
    "orchestrator_ungrounded_total": ORCHESTRATOR_UNGROUNDED_TOTAL,
    "llm_input_tokens_total": LLM_INPUT_TOKENS_TOTAL,
    "llm_output_tokens_total": LLM_OUTPUT_TOKENS_TOTAL,
    "llm_cost_usd_total": LLM_COST_USD_TOTAL,
    "llm_errors_total": LLM_ERRORS_TOTAL,
}
_HISTOGRAMS: Final[dict[str, Histogram]] = {
    "orchestrator_latency_ms": ORCHESTRATOR_LATENCY_MS,
    "orchestrator_pages_ok": ORCHESTRATOR_PAGES_OK,
    "orchestrator_pages_failed": ORCHESTRATOR_PAGES_FAILED,
    "llm_latency_ms": LLM_LATENCY_MS,
}
_GAUGES: Final[dict[str, Gauge]] = {
    "llm_budget_remaining_usd": LLM_BUDGET_REMAINING_USD,
}


class PrometheusTelemetrySink:
    """Production telemetry sink: forwards abstract sink calls to the
    Prometheus instruments above and the OTel tracer.

    Conforms structurally to ``nexus.llm.telemetry.LLMTelemetrySink``.
    Every method swallows its own errors (same best-effort contract as
    the cache): a metrics failure must never break a user request.
    """

    def record_span(self, name: str, attributes: dict[str, object]) -> None:
        try:
            span = get_tracer("nexus").start_span(name)
            for key, value in attributes.items():
                # OTel attributes must be primitives; drop None / others.
                if isinstance(value, (str, bool, int, float)):
                    span.set_attribute(key, value)
            span.end()
        except Exception:  # telemetry must not raise on the request path
            logger.debug("telemetry_span_failed", extra={"name": name}, exc_info=True)

    def increment_counter(
        self, name: str, value: int | float = 1, labels: dict[str, str] | None = None
    ) -> None:
        counter = _COUNTERS.get(name)
        if counter is None:
            logger.debug("telemetry_unmapped_counter", extra={"name": name})
            return
        try:
            (counter.labels(**labels) if labels else counter).inc(value)
        except Exception:  # telemetry must not raise on the request path
            logger.debug("telemetry_counter_failed", extra={"name": name}, exc_info=True)

    def observe_histogram(
        self, name: str, value: int | float, labels: dict[str, str] | None = None
    ) -> None:
        histogram = _HISTOGRAMS.get(name)
        if histogram is None:
            logger.debug("telemetry_unmapped_histogram", extra={"name": name})
            return
        try:
            (histogram.labels(**labels) if labels else histogram).observe(value)
        except Exception:  # telemetry must not raise on the request path
            logger.debug("telemetry_histogram_failed", extra={"name": name}, exc_info=True)

    def set_gauge(
        self, name: str, value: int | float, labels: dict[str, str] | None = None
    ) -> None:
        gauge = _GAUGES.get(name)
        if gauge is None:
            logger.debug("telemetry_unmapped_gauge", extra={"name": name})
            return
        try:
            (gauge.labels(**labels) if labels else gauge).set(value)
        except Exception:  # telemetry must not raise on the request path
            logger.debug("telemetry_gauge_failed", extra={"name": name}, exc_info=True)


# ---------- lifecycle ----------

_metrics_server_started = False


def setup_telemetry(
    *,
    service_name: str = "nexus-agentic-search",
    service_version: str = "0.0.0",
    metrics_port: int | None = None,
    metrics_bind_host: str = "0.0.0.0",  # noqa: S104  # bind in container; firewall enforces
) -> None:
    """Initialise the OTel SDK and optionally start the metrics server.

    Calling without ``metrics_port`` configures tracing only — useful in
    tests. Idempotent: the second call replaces the tracer provider but
    does NOT start a second metrics HTTP listener.
    """
    resource = Resource.create(
        {
            SERVICE_NAME: service_name,
            SERVICE_VERSION: service_version,
        }
    )
    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    if metrics_port is not None:
        _start_metrics_server_once(metrics_bind_host, metrics_port)


def _start_metrics_server_once(host: str, port: int) -> None:
    global _metrics_server_started
    if _metrics_server_started:
        logger.debug("metrics_server_already_started")
        return
    start_http_server(port, addr=host)
    _metrics_server_started = True


def render_metrics(registry: CollectorRegistry = REGISTRY) -> tuple[bytes, str]:
    """Return ``(body, content_type)`` for a manual Prometheus scrape.

    The HTTP server set up by :func:`setup_telemetry` already serves
    ``/metrics`` itself; this helper exists for in-process inspection
    in tests and for adjacent processes (e.g. health endpoints) that
    want to embed a snapshot.
    """
    return generate_latest(registry), CONTENT_TYPE_LATEST
