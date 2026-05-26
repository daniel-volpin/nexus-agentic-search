from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json

import pytest

from nexus.crawl.types import Document
from nexus.citations import RawCitation
from nexus.llm import BudgetExceeded, CompletionResult, InMemoryTelemetrySink, LLMUnavailable
from nexus.orchestrator import Orchestrator, OrchestratorConfig
from nexus.search.types import Result, SearchRequest, SearchResponse, SearchUnavailable


class FakeSearchClient:
    def __init__(self, response: SearchResponse | Exception) -> None:
        self.response = response

    async def search(self, req: SearchRequest) -> SearchResponse:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeCrawler:
    def __init__(self, documents: dict[str, Document]) -> None:
        self.documents = documents

    def fetch(self, req) -> Document:
        return self.documents[req.url]


class SlowCrawler(FakeCrawler):
    def __init__(self, documents: dict[str, Document], *, delay_s: float) -> None:
        super().__init__(documents)
        self.delay_s = delay_s
        self.calls = 0
        self.cancelled = False

    def fetch(self, req) -> Document:
        import time

        self.calls += 1
        try:
            time.sleep(self.delay_s)
            return self.documents[req.url]
        except BaseException:
            self.cancelled = True
            raise


class FakeLLMClient:
    def __init__(self, completion: CompletionResult | Exception, *, token_count: int = 100) -> None:
        self.completion = completion
        self.token_count_value = token_count
        self.calls: list[dict] = []

    async def complete(self, role: str, messages: list[dict], max_output_tokens: int, temperature: float = 0.0, tools=None) -> CompletionResult:
        self.calls.append(
            {
                "role": role,
                "messages": messages,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
                "tools": tools,
            }
        )
        if isinstance(self.completion, Exception):
            raise self.completion
        return self.completion

    def count_tokens(self, role: str, messages: list[dict]) -> int:
        return self.token_count_value


def make_result(url: str, rank: int) -> Result:
    return Result(url=url, title=f"title {rank}", snippet=f"snippet {rank}", engine="brave", rank=rank)


def make_document(url: str, *, markdown: str, content_hash: str, status: str = "ok") -> Document:
    return Document(
        url=url,
        requested_url=url,
        content_hash=content_hash,
        markdown=markdown if status == "ok" else "",
        enveloped_markdown=f'<untrusted_source url="{url}" sha256="{content_hash}">{markdown}</untrusted_source>' if status == "ok" else "",
        content_type="text/markdown" if status == "ok" else "",
        fetched_at=datetime.now(timezone.utc),
        status=status,
        http_status=200 if status == "ok" else None,
        bytes_in=len(markdown.encode("utf-8")) if status == "ok" else 0,
        render_ms=10,
        extraction_ms=5,
        redirect_chain=[url],
    )


async def collect_events(orchestrator: Orchestrator, request: SearchRequest):
    items = []
    async for event in orchestrator.search(request):
        items.append(event)
    return items


def test_orchestrator_emits_happy_path_events_in_order() -> None:
    search = FakeSearchClient(
        SearchResponse(
            results=[make_result("https://example.com/a", 0)],
            provider="brave",
            query_sent="python",
            latency_ms=20,
        )
    )
    crawler = FakeCrawler(
        {
            "https://example.com/a": make_document(
                "https://example.com/a",
                markdown="Python is a programming language used widely in automation.",
                content_hash="doc-1",
            )
        }
    )
    payload = {
        "answer_text": "Python is widely used in automation.[^claim-1]",
        "citations": [
            {
                "url": "https://example.com/a",
                "content_hash": "doc-1",
                "quote": "Python is a programming language used widely in automation.",
                "claim_id": "claim-1",
            }
        ],
    }
    llm = FakeLLMClient(
        CompletionResult(
            text=json.dumps(payload),
            finish_reason="stop",
            input_tokens=100,
            output_tokens=25,
            cost_usd=0.2,
            tool_calls=[],
            model_id="openai/gpt-4o-2024-11-20",
            role="synthesis",
            fallback_used=False,
            model_drift=False,
        )
    )
    telemetry = InMemoryTelemetrySink()
    orchestrator = Orchestrator(
        search_client=search,
        crawl_client=crawler,
        llm_client=llm,
        config=OrchestratorConfig(),
        telemetry=telemetry,
    )

    events = asyncio.run(collect_events(orchestrator, SearchRequest(query="python")))

    assert [event.stage for event in events] == [
        "accepted",
        "expanded",
        "searched",
        "ranked",
        "page_ready",
        "synthesized",
        "validated",
        "answer",
    ]
    assert events[-1].payload["ungrounded"] is False
    assert events[-1].payload["citations"][0].content_hash == "doc-1"
    assert events[-1].payload["documents"] == [{"url": "https://example.com/a", "content_hash": "doc-1"}]
    assert llm.calls[0]["tools"] is None
    assert telemetry.spans[0][0] == "orchestrator.search"


def test_orchestrator_skips_page_ready_for_failed_crawls() -> None:
    search = FakeSearchClient(
        SearchResponse(
            results=[make_result("https://example.com/a", 0), make_result("https://example.com/b", 1)],
            provider="brave",
            query_sent="python",
            latency_ms=20,
        )
    )
    crawler = FakeCrawler(
        {
            "https://example.com/a": make_document("https://example.com/a", markdown="Python content with enough detail.", content_hash="doc-1"),
            "https://example.com/b": make_document("https://example.com/b", markdown="blocked", content_hash="doc-2", status="http_4xx"),
        }
    )
    llm = FakeLLMClient(
        CompletionResult(
            text=json.dumps({"answer_text": "Python content.[^claim-1]", "citations": []}),
            finish_reason="stop",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.1,
            tool_calls=[],
            model_id="openai/gpt-4o-2024-11-20",
            role="synthesis",
        )
    )
    orchestrator = Orchestrator(search_client=search, crawl_client=crawler, llm_client=llm, config=OrchestratorConfig())

    events = asyncio.run(collect_events(orchestrator, SearchRequest(query="python")))

    assert [event.stage for event in events].count("page_ready") == 1


def test_orchestrator_returns_ungrounded_answer_when_all_crawls_fail() -> None:
    search = FakeSearchClient(
        SearchResponse(
            results=[make_result("https://example.com/a", 0)],
            provider="brave",
            query_sent="python",
            latency_ms=20,
        )
    )
    crawler = FakeCrawler({"https://example.com/a": make_document("https://example.com/a", markdown="missing", content_hash="doc-1", status="timeout")})
    llm = FakeLLMClient(
        CompletionResult(
            text="",
            finish_reason="stop",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            tool_calls=[],
            model_id="openai/gpt-4o-2024-11-20",
            role="synthesis",
        )
    )
    orchestrator = Orchestrator(search_client=search, crawl_client=crawler, llm_client=llm, config=OrchestratorConfig())

    events = asyncio.run(collect_events(orchestrator, SearchRequest(query="python")))

    answer = events[-1]
    assert answer.stage == "answer"
    assert answer.payload["ungrounded"] is True
    assert answer.payload["answer_text"] == "No sources could be fetched."


def test_orchestrator_maps_search_unavailable_to_error() -> None:
    orchestrator = Orchestrator(
        search_client=FakeSearchClient(SearchUnavailable("down")),
        crawl_client=FakeCrawler({}),
        llm_client=FakeLLMClient(
            CompletionResult(
                text="",
                finish_reason="stop",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                tool_calls=[],
                model_id="openai/gpt-4o-2024-11-20",
                role="synthesis",
            )
        ),
        config=OrchestratorConfig(),
    )

    events = asyncio.run(collect_events(orchestrator, SearchRequest(query="python")))

    assert [event.stage for event in events] == ["accepted", "expanded", "error"]
    assert events[-1].payload["reason"] == "search_unavailable"
    assert events[-1].payload["retriable"] is True


def test_orchestrator_maps_llm_failures_to_error() -> None:
    search = FakeSearchClient(
        SearchResponse(results=[make_result("https://example.com/a", 0)], provider="brave", query_sent="python", latency_ms=20)
    )
    crawler = FakeCrawler(
        {"https://example.com/a": make_document("https://example.com/a", markdown="Python content with enough detail.", content_hash="doc-1")}
    )

    llm_unavailable = Orchestrator(
        search_client=search,
        crawl_client=crawler,
        llm_client=FakeLLMClient(LLMUnavailable("down")),
        config=OrchestratorConfig(),
    )
    budget_exceeded = Orchestrator(
        search_client=search,
        crawl_client=crawler,
        llm_client=FakeLLMClient(BudgetExceeded("cap")),
        config=OrchestratorConfig(),
    )

    unavailable_events = asyncio.run(collect_events(llm_unavailable, SearchRequest(query="python")))
    budget_events = asyncio.run(collect_events(budget_exceeded, SearchRequest(query="python")))

    assert unavailable_events[-1].payload["reason"] == "llm_unavailable"
    assert unavailable_events[-1].payload["retriable"] is True
    assert budget_events[-1].payload["reason"] == "budget_exhausted"
    assert budget_events[-1].payload["retriable"] is False


def test_orchestrator_marks_answer_ungrounded_when_citations_rejected() -> None:
    search = FakeSearchClient(
        SearchResponse(results=[make_result("https://example.com/a", 0)], provider="brave", query_sent="python", latency_ms=20)
    )
    crawler = FakeCrawler(
        {
            "https://example.com/a": make_document(
                "https://example.com/a",
                markdown="Python is a programming language used widely in automation.",
                content_hash="doc-1",
            )
        }
    )
    payload = {
        "answer_text": "Unsupported claim.[^claim-1]",
        "citations": [
            {
                "url": "https://example.com/a",
                "content_hash": "doc-1",
                "quote": "This quote does not exist",
                "claim_id": "claim-1",
            }
        ],
    }
    llm = FakeLLMClient(
        CompletionResult(
            text=json.dumps(payload),
            finish_reason="stop",
            input_tokens=100,
            output_tokens=25,
            cost_usd=0.2,
            tool_calls=[],
            model_id="openai/gpt-4o-2024-11-20",
            role="synthesis",
        )
    )
    orchestrator = Orchestrator(search_client=search, crawl_client=crawler, llm_client=llm, config=OrchestratorConfig())

    events = asyncio.run(collect_events(orchestrator, SearchRequest(query="python")))

    answer = events[-1]
    assert answer.stage == "answer"
    assert answer.payload["ungrounded"] is True
    assert answer.payload["citations"] == []
    assert answer.payload["rejected_citations"][0].reason == "quote_not_found"


def test_orchestrator_marks_length_finish_as_degraded() -> None:
    search = FakeSearchClient(
        SearchResponse(results=[make_result("https://example.com/a", 0)], provider="brave", query_sent="python", latency_ms=20)
    )
    crawler = FakeCrawler(
        {
            "https://example.com/a": make_document(
                "https://example.com/a",
                markdown="Python is a programming language used widely in automation.",
                content_hash="doc-1",
            )
        }
    )
    payload = {
        "answer_text": "Python is widely used in automation.[^claim-1]",
        "citations": [
            {
                "url": "https://example.com/a",
                "content_hash": "doc-1",
                "quote": "Python is a programming language used widely in automation.",
                "claim_id": "claim-1",
            }
        ],
    }
    llm = FakeLLMClient(
        CompletionResult(
            text=json.dumps(payload),
            finish_reason="length",
            input_tokens=100,
            output_tokens=25,
            cost_usd=0.2,
            tool_calls=[],
            model_id="openai/gpt-4o-2024-11-20",
            role="synthesis",
        )
    )
    orchestrator = Orchestrator(search_client=search, crawl_client=crawler, llm_client=llm, config=OrchestratorConfig())

    events = asyncio.run(collect_events(orchestrator, SearchRequest(query="python")))

    assert events[-1].stage == "answer"
    assert events[-1].payload["degraded"] is True


def test_orchestrator_times_out_before_any_crawl_completes() -> None:
    search = FakeSearchClient(
        SearchResponse(results=[make_result("https://example.com/a", 0)], provider="brave", query_sent="python", latency_ms=20)
    )
    crawler = SlowCrawler(
        {"https://example.com/a": make_document("https://example.com/a", markdown="Python content with enough detail.", content_hash="doc-1")},
        delay_s=0.05,
    )
    llm = FakeLLMClient(
        CompletionResult(
            text="",
            finish_reason="stop",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            tool_calls=[],
            model_id="openai/gpt-4o-2024-11-20",
            role="synthesis",
        )
    )
    orchestrator = Orchestrator(
        search_client=search,
        crawl_client=crawler,
        llm_client=llm,
        config=OrchestratorConfig(wall_clock_s=0.01),
    )

    events = asyncio.run(collect_events(orchestrator, SearchRequest(query="python")))

    assert events[-1].stage == "error"
    assert events[-1].payload["reason"] == "timeout"
    assert events[-1].payload["retriable"] is True


def test_orchestrator_propagates_cancellation() -> None:
    search = FakeSearchClient(
        SearchResponse(results=[make_result("https://example.com/a", 0)], provider="brave", query_sent="python", latency_ms=20)
    )
    crawler = SlowCrawler(
        {"https://example.com/a": make_document("https://example.com/a", markdown="Python content with enough detail.", content_hash="doc-1")},
        delay_s=0.1,
    )
    llm = FakeLLMClient(
        CompletionResult(
            text="",
            finish_reason="stop",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            tool_calls=[],
            model_id="openai/gpt-4o-2024-11-20",
            role="synthesis",
        )
    )
    orchestrator = Orchestrator(search_client=search, crawl_client=crawler, llm_client=llm, config=OrchestratorConfig())

    async def cancel_midstream() -> None:
        agen = orchestrator.search(SearchRequest(query="python"))
        await agen.__anext__()
        await agen.__anext__()
        await agen.__anext__()
        await agen.__anext__()
        task = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_midstream())
