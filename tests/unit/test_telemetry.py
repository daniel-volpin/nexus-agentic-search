"""Tests for telemetry primitives (Spec 11)."""

from __future__ import annotations

import asyncio
import socket
from contextlib import closing

import pytest
from opentelemetry import trace
from prometheus_client import REGISTRY

from nexus import telemetry

# ---------- fixtures ----------


@pytest.fixture(autouse=True)
def _reset_request_id() -> None:
    yield
    telemetry.request_id_var.set(None)


def _free_tcp_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------- request id ----------


def test_request_id_var_default_is_none() -> None:
    assert telemetry.get_request_id() is None


def test_new_request_id_returns_32_char_hex() -> None:
    rid = telemetry.new_request_id()
    assert len(rid) == 32
    assert all(c in "0123456789abcdef" for c in rid)


def test_new_request_id_unique() -> None:
    ids = {telemetry.new_request_id() for _ in range(100)}
    assert len(ids) == 100


def test_bind_request_id_with_value() -> None:
    bound = telemetry.bind_request_id("abc123")
    assert bound == "abc123"
    assert telemetry.get_request_id() == "abc123"


def test_bind_request_id_generates_when_none() -> None:
    bound = telemetry.bind_request_id()
    assert len(bound) == 32
    assert telemetry.get_request_id() == bound


async def test_request_id_is_per_context() -> None:
    """Different asyncio tasks see independent request_id values."""
    telemetry.bind_request_id("outer")

    async def child(value: str) -> str | None:
        telemetry.bind_request_id(value)
        await asyncio.sleep(0)
        return telemetry.get_request_id()

    results = await asyncio.gather(child("a"), child("b"), child("c"))
    assert set(results) == {"a", "b", "c"}
    # Outer task's value is preserved.
    assert telemetry.get_request_id() == "outer"


# ---------- tracer ----------


def test_get_tracer_returns_tracer() -> None:
    t = telemetry.get_tracer("nexus.test")
    assert isinstance(t, trace.Tracer)


def test_setup_telemetry_replaces_tracer_provider() -> None:
    telemetry.setup_telemetry(service_name="nexus-test", service_version="0.0.0-test")
    provider_after = trace.get_tracer_provider()
    # The SDK TracerProvider has a `get_tracer` callable and a resource.
    assert hasattr(provider_after, "get_tracer")


def test_setup_telemetry_is_idempotent() -> None:
    telemetry.setup_telemetry(service_name="nexus-test", service_version="v1")
    telemetry.setup_telemetry(service_name="nexus-test", service_version="v2")
    # Second call must not raise.


def test_span_can_record_attributes_after_setup() -> None:
    telemetry.setup_telemetry(service_name="nexus-test", service_version="0.0.0")
    tracer = telemetry.get_tracer("nexus.test")
    with tracer.start_as_current_span("unit") as span:
        span.set_attribute("hello", "world")
        # Span context is valid after setup_telemetry.
        assert span.get_span_context().trace_id != 0


# ---------- metrics ----------


def test_standard_metric_instruments_are_defined() -> None:
    # Spot-check from each component family — Spec 11 §Metrics table.
    for name in (
        "ORCHESTRATOR_REQUESTS_TOTAL",
        "ORCHESTRATOR_LATENCY_MS",
        "ORCHESTRATOR_UNGROUNDED_TOTAL",
        "ORCHESTRATOR_PAGES_OK",
        "ORCHESTRATOR_PAGES_FAILED",
        "LLM_ERRORS_TOTAL",
        "SEARCH_LATENCY_MS",
        "SEARCH_ERRORS_TOTAL",
        "SEARCH_PROVIDER_USED_TOTAL",
        "SEARXNG_ENGINE_TRIPPED_TOTAL",
        "SEARXNG_ENGINE_DISABLED",
        "RERANK_LATENCY_MS",
        "CRAWL_LATENCY_MS",
        "CRAWL_STATUS_TOTAL",
        "CRAWL_BYTES_IN",
        "CRAWL_BROWSER_POOL_IN_USE",
        "LLM_INPUT_TOKENS_TOTAL",
        "LLM_OUTPUT_TOKENS_TOTAL",
        "LLM_COST_USD_TOTAL",
        "LLM_LATENCY_MS",
        "LLM_BUDGET_REMAINING_USD",
        "CITATIONS_VALID_TOTAL",
        "CITATIONS_REJECTED_TOTAL",
        "CITATIONS_ENVELOPE_VIOLATIONS_TOTAL",
        "CACHE_HIT_TOTAL",
        "CACHE_MISS_TOTAL",
        "CACHE_ERRORS_TOTAL",
        "HTTP_REQUESTS_TOTAL",
        "HTTP_UNAUTHORIZED_TOTAL",
        "MCP_TOOL_CALLS_TOTAL",
        "MCP_UNAUTHORIZED_TOTAL",
    ):
        assert hasattr(telemetry, name), f"missing metric {name}"


def test_counter_increments_and_appears_in_scrape() -> None:
    telemetry.ORCHESTRATOR_REQUESTS_TOTAL.labels(final_stage="answer").inc()
    body, content_type = telemetry.render_metrics()
    assert b"orchestrator_requests_total" in body
    assert content_type.startswith("text/plain")


def test_histogram_observes() -> None:
    # Must not raise; the bucket count is exercised.
    telemetry.ORCHESTRATOR_LATENCY_MS.observe(123.4)
    body, _ = telemetry.render_metrics()
    assert b"orchestrator_latency_ms" in body


def test_gauge_sets() -> None:
    telemetry.SEARXNG_ENGINE_DISABLED.labels(engine="google").set(1)
    telemetry.SEARXNG_ENGINE_DISABLED.labels(engine="google").set(0)
    body, _ = telemetry.render_metrics()
    assert b"searxng_engine_disabled" in body


def test_render_metrics_returns_prometheus_format() -> None:
    body, content_type = telemetry.render_metrics()
    assert b"# HELP" in body or b"# TYPE" in body
    # Prometheus exposition uses text/plain with version + charset.
    assert "text/plain" in content_type


# ---------- metrics HTTP server ----------


def test_metrics_http_server_starts_and_serves() -> None:
    import urllib.request

    port = _free_tcp_port()
    telemetry.setup_telemetry(
        service_name="nexus-test",
        service_version="0.0.0",
        metrics_port=port,
        metrics_bind_host="127.0.0.1",
    )
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as resp:
            payload = resp.read()
        assert b"orchestrator_requests_total" in payload
    finally:
        # No public shutdown API on prometheus_client.start_http_server;
        # the daemon thread dies with the test process.
        pass


def test_setup_telemetry_does_not_double_start_metrics_server() -> None:
    """Second setup_telemetry call with metrics_port must not bind a
    second listener — would raise OSError on port reuse."""
    port = _free_tcp_port()
    telemetry.setup_telemetry(
        service_name="nexus-test",
        service_version="0.0.0",
        metrics_port=port,
        metrics_bind_host="127.0.0.1",
    )
    # Second call: same module state means metrics server is treated as
    # already-started. Must not raise.
    telemetry.setup_telemetry(
        service_name="nexus-test",
        service_version="0.0.0",
        metrics_port=port,
        metrics_bind_host="127.0.0.1",
    )


# ---------- PrometheusTelemetrySink ----------


def test_sink_forwards_labelled_counter_to_instrument() -> None:
    sink = telemetry.PrometheusTelemetrySink()
    labels = {"role": "synthesis", "reason": "sink_unit_test"}
    before = REGISTRY.get_sample_value("llm_errors_total", labels) or 0.0
    sink.increment_counter("llm_errors_total", 1, labels)
    after = REGISTRY.get_sample_value("llm_errors_total", labels)
    assert after == before + 1


def test_sink_forwards_unlabelled_counter() -> None:
    sink = telemetry.PrometheusTelemetrySink()
    before = REGISTRY.get_sample_value("orchestrator_ungrounded_total") or 0.0
    sink.increment_counter("orchestrator_ungrounded_total", 1, {})
    after = REGISTRY.get_sample_value("orchestrator_ungrounded_total")
    assert after == before + 1


def test_sink_forwards_histogram() -> None:
    sink = telemetry.PrometheusTelemetrySink()
    before = REGISTRY.get_sample_value("orchestrator_pages_ok_sum") or 0.0
    sink.observe_histogram("orchestrator_pages_ok", 3, {})
    after = REGISTRY.get_sample_value("orchestrator_pages_ok_sum")
    assert after == before + 3


def test_sink_forwards_gauge() -> None:
    sink = telemetry.PrometheusTelemetrySink()
    sink.set_gauge("llm_budget_remaining_usd", 4.2, {"role": "sink_unit_test"})
    value = REGISTRY.get_sample_value("llm_budget_remaining_usd", {"role": "sink_unit_test"})
    assert value == 4.2


def test_sink_unmapped_name_is_silent_noop() -> None:
    sink = telemetry.PrometheusTelemetrySink()
    # Unknown names must be dropped, never raised — telemetry is off the
    # critical path.
    sink.increment_counter("does_not_exist", 1, {"x": "y"})
    sink.observe_histogram("does_not_exist", 1, {})
    sink.set_gauge("does_not_exist", 1, {})


def test_sink_swallows_label_mismatch() -> None:
    sink = telemetry.PrometheusTelemetrySink()
    # Wrong label name for a real instrument would raise inside
    # prometheus_client; the sink must swallow it.
    sink.increment_counter("llm_errors_total", 1, {"wrong_label": "x"})


def test_sink_record_span_drops_non_primitive_attributes() -> None:
    telemetry.setup_telemetry(service_name="nexus-test", service_version="0.0.0")
    sink = telemetry.PrometheusTelemetrySink()
    # None and arbitrary objects are dropped; primitives are kept. Must
    # not raise.
    sink.record_span(
        "orchestrator.search",
        {"final_stage": "answer", "freshness": None, "obj": object(), "cost_usd": 0.0},
    )


def test_sink_conforms_to_llm_telemetry_sink_protocol() -> None:
    from nexus.llm.telemetry import LLMTelemetrySink

    # Structural conformance is enforced by mypy at this assignment; the
    # runtime assertions document the surface.
    sink: LLMTelemetrySink = telemetry.PrometheusTelemetrySink()
    assert all(
        hasattr(sink, m)
        for m in ("record_span", "increment_counter", "observe_histogram", "set_gauge")
    )
