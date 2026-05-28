"""Integration: the full service assembles and serves end-to-end with
fakes in place of real providers (Spec 13 §Integration).

These tests wire the *real* orchestrator + HTTP transport + cache +
citations engine together, substituting only the outermost
dependencies (search provider, LLM backend) with fakes. They prove
the pieces compose — the contract surfaces line up — without any
network access.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from nexus.cache import setup_cache, shutdown_cache
from nexus.crawl import CrawlClient
from nexus.crawl.ssrf import SSRFGuard
from nexus.http import HTTPConfig, create_app
from nexus.llm import (
    LiteLLMClient,
    LLMConfig,
    LLMRoleConfig,
    Message,
    ProviderResponse,
)
from nexus.orchestrator.service import Orchestrator
from nexus.search import SearchRequest, SearchResponse

_TOKEN = "integration-token-of-sufficient-length-xx"


# ---------- fakes for the outermost dependencies ----------


class _EmptySearchClient:
    async def search(self, req: SearchRequest) -> SearchResponse:
        return SearchResponse(results=[], provider="fake", query_sent=req.query, latency_ms=0)


@dataclass
class _FakeLLMResp:
    text: str = "No sources were available."
    finish_reason: str = "stop"
    input_tokens: int = 1
    output_tokens: int = 1
    cost_usd: float = 0.0
    tool_calls: list[dict] | None = None
    model: str = "openai/gpt-4o-2024-11-20"


class _FakeLLMBackend:
    pricing_table_version = "test-pricing"

    async def acompletion(self, **_kwargs: object) -> ProviderResponse | AsyncIterator[dict]:
        r = _FakeLLMResp()
        return ProviderResponse(
            text=r.text,
            finish_reason=r.finish_reason,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cost_usd=r.cost_usd,
            tool_calls=[],
            model=r.model,
        )

    def token_counter(self, *, model: str, messages: list[Message]) -> int:
        return 1


def _llm_config() -> LLMConfig:
    return LLMConfig(
        roles={
            "synthesis": LLMRoleConfig(
                primary="openai/gpt-4o-2024-11-20",
                fallback=[],
                max_input_tokens=32000,
                max_output_tokens=2000,
            )
        },
        daily_usd_budget=10.0,
        soft_budget_fraction=0.8,
        pricing_table_version="test-pricing",
    )


@pytest.fixture
def http_client(tmp_path) -> TestClient:
    setup_cache(tmp_path / "cache", total_size_gb=0.01)
    orchestrator = Orchestrator(
        search_client=_EmptySearchClient(),
        crawl_client=CrawlClient(ssrf_guard=SSRFGuard()),
        llm_client=LiteLLMClient(config=_llm_config(), backend=_FakeLLMBackend()),
    )
    app = create_app(
        orchestrator=orchestrator,
        llm_config_roles=dict(_llm_config().roles),
        config=HTTPConfig(token=_TOKEN),
    )
    try:
        yield TestClient(app)
    finally:
        shutdown_cache()


# ---------- the pieces compose ----------


def test_health_is_served(http_client: TestClient) -> None:
    resp = http_client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_search_with_no_results_returns_ungrounded_answer(
    http_client: TestClient,
) -> None:
    """End-to-end: empty search → orchestrator runs the full pipeline →
    HTTP transport returns a 200 with an ungrounded answer. Proves
    search → rerank → crawl → synth → citations → transport all line
    up without raising."""
    resp = http_client.post(
        "/v1/search",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        json={"query": "anything"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ungrounded"] is True
    assert body["citations"] == []
    assert body["documents"] == []


def test_search_requires_auth(http_client: TestClient) -> None:
    resp = http_client.post("/v1/search", json={"query": "x"})
    assert resp.status_code == 401


def test_stream_emits_full_stage_sequence(http_client: TestClient) -> None:
    """SSE stream surfaces the staged pipeline and terminates on an
    `answer` event."""
    with http_client.stream(
        "POST",
        "/v1/search/stream",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        json={"query": "anything"},
    ) as resp:
        body = b"".join(resp.iter_bytes()).decode("utf-8")
    assert resp.status_code == 200
    assert "event: accepted" in body
    assert "event: searched" in body
    assert "event: answer" in body


def test_invalid_query_returns_422(http_client: TestClient) -> None:
    resp = http_client.post(
        "/v1/search",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        json={"query": "   "},
    )
    assert resp.status_code == 422
