"""Load / concurrency tests (Spec 13 §Load).

Exercises the orchestrator pipeline under concurrent fan-out with
fakes in place of network providers. Asserts the async machinery does
not deadlock, drop events, or raise under parallelism. These run in
the default suite (no network, no real models).

Browser-pool and memory-ceiling load tests (Plan 13) are deferred
until the real Crawl4AI path lands — they need an actual browser
pool to exercise.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from nexus.crawl import CrawlClient
from nexus.crawl.ssrf import SSRFGuard
from nexus.llm import (
    LiteLLMClient,
    LLMConfig,
    LLMRoleConfig,
    Message,
    ProviderResponse,
)
from nexus.orchestrator.service import Orchestrator
from nexus.search import SearchRequest, SearchResponse

pytestmark = pytest.mark.load


class _EmptySearch:
    async def search(self, req: SearchRequest) -> SearchResponse:
        # Tiny await so the event loop actually interleaves tasks.
        await asyncio.sleep(0)
        return SearchResponse(results=[], provider="fake", query_sent=req.query, latency_ms=0)


@dataclass
class _Resp:
    text: str = "no sources"
    finish_reason: str = "stop"
    input_tokens: int = 1
    output_tokens: int = 1
    cost_usd: float = 0.0
    model: str = "openai/gpt-4o-2024-11-20"


class _FakeBackend:
    pricing_table_version = "test-pricing"

    async def acompletion(self, **_kwargs: object) -> ProviderResponse | AsyncIterator[dict]:
        r = _Resp()
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


def _orchestrator() -> Orchestrator:
    config = LLMConfig(
        roles={
            "synthesis": LLMRoleConfig(
                primary="openai/gpt-4o-2024-11-20",
                fallback=[],
                max_input_tokens=32000,
                max_output_tokens=2000,
            )
        },
        daily_usd_budget=1000.0,  # high cap; not the thing under test
        soft_budget_fraction=0.8,
        pricing_table_version="test-pricing",
    )
    return Orchestrator(
        search_client=_EmptySearch(),
        crawl_client=CrawlClient(ssrf_guard=SSRFGuard()),
        llm_client=LiteLLMClient(config=config, backend=_FakeBackend()),
    )


async def _drain(orch: Orchestrator, query: str) -> list[str]:
    return [event.stage async for event in orch.search(SearchRequest(query=query))]


async def test_many_concurrent_requests_all_complete() -> None:
    """50 concurrent pipelines all run to a terminal stage without
    raising or dropping events."""
    orch = _orchestrator()
    results = await asyncio.gather(*[_drain(orch, f"query number {i}") for i in range(50)])
    assert len(results) == 50
    for stages in results:
        assert stages[0] == "accepted"
        assert stages[-1] in {"answer", "error"}


async def test_concurrent_requests_are_isolated() -> None:
    """Each request emits its own independent stage stream; one slow
    request does not swallow another's events."""
    orch = _orchestrator()
    results = await asyncio.gather(*[_drain(orch, f"q{i}") for i in range(20)])
    # Every result must contain the full happy-path prefix.
    for stages in results:
        assert "accepted" in stages
        assert "searched" in stages


async def test_repeated_sequential_requests_do_not_leak_state() -> None:
    """1000 sequential lightweight requests complete; a crude guard
    against unbounded accumulation in the orchestrator (full RSS
    profiling is a deferred perf test)."""
    orch = _orchestrator()
    for i in range(1000):
        stages = await _drain(orch, f"q{i}")
        assert stages[-1] in {"answer", "error"}
