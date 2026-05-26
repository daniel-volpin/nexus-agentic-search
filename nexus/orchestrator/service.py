from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
import hashlib
import json
import uuid

from nexus.citations import RawCitation, validate_citations
from nexus.crawl.types import CrawlRequest, Document
from nexus.llm import BudgetExceeded, CompletionResult, InputTooLarge, LLMUnavailable
from nexus.rerank import rerank as default_rerank
from nexus.search.types import RankedResult, Result, SearchRequest, SearchResponse, SearchUnavailable

from .prompts import build_synthesis_messages
from .types import AnswerEvent, OrchestratorConfig


class Orchestrator:
    def __init__(
        self,
        *,
        search_client,
        crawl_client,
        llm_client,
        config: OrchestratorConfig | None = None,
        telemetry=None,
        rerank_fn: Callable[[str, list[Result]], list[RankedResult]] | None = None,
    ) -> None:
        self._search = search_client
        self._crawl = crawl_client
        self._llm = llm_client
        self._config = config or OrchestratorConfig()
        self._telemetry = telemetry
        self._rerank = rerank_fn or self._simple_rerank

    async def search(self, req: SearchRequest) -> AsyncIterator[AnswerEvent]:
        started_at = asyncio.get_running_loop().time()
        request_id = str(uuid.uuid4())
        events: list[AnswerEvent] = []
        pages_failed = 0
        citations_valid = 0
        citations_rejected = 0
        degraded = False
        ungrounded = False
        final_stage = "error"
        cost_usd = 0.0

        accepted = AnswerEvent(stage="accepted", payload={"request_id": request_id, "normalized_query": req.query})
        events.append(accepted)
        yield accepted

        expanded = AnswerEvent(stage="expanded", payload={"sub_queries": [req.query] if self._config.enable_query_expansion else []})
        events.append(expanded)
        yield expanded

        try:
            response: SearchResponse = await self._search.search(req)
        except SearchUnavailable as exc:
            event = AnswerEvent(stage="error", payload={"reason": "search_unavailable", "retriable": True, "detail": str(exc)})
            self._record(request_id, req, "error", started_at, degraded, ungrounded, cost_usd, 0, pages_failed, citations_valid, citations_rejected)
            yield event
            return

        searched = AnswerEvent(stage="searched", payload={"result_count": len(response.results), "provider": response.provider})
        events.append(searched)
        yield searched

        ranked_rows = self._rerank(req.query, response.results)[: self._config.crawl_pages_max]
        ranked = AnswerEvent(
            stage="ranked",
            payload={"kept": [{"url": row.result.url, "title": row.result.title, "score": row.score} for row in ranked_rows]},
        )
        events.append(ranked)
        yield ranked

        remaining_before_crawl = self._remaining_budget(started_at)
        if remaining_before_crawl <= 0:
            event = AnswerEvent(stage="error", payload={"reason": "timeout", "retriable": True, "detail": "wall clock exceeded"})
            self._record(request_id, req, "error", started_at, degraded, ungrounded, cost_usd, 0, pages_failed, citations_valid, citations_rejected)
            yield event
            return

        try:
            crawled_docs = await asyncio.wait_for(self._crawl_ranked(ranked_rows), timeout=remaining_before_crawl)
        except asyncio.TimeoutError:
            event = AnswerEvent(stage="error", payload={"reason": "timeout", "retriable": True, "detail": "wall clock exceeded"})
            self._record(request_id, req, "error", started_at, degraded, ungrounded, cost_usd, 0, pages_failed, citations_valid, citations_rejected)
            yield event
            return
        ok_docs = [doc for doc in crawled_docs if doc.status == "ok"]
        pages_failed = len(crawled_docs) - len(ok_docs)
        for doc in ok_docs:
            page_ready = AnswerEvent(stage="page_ready", payload={"url": doc.url, "content_hash": doc.content_hash, "status": doc.status, "render_ms": doc.render_ms})
            events.append(page_ready)
            yield page_ready

        if not ok_docs:
            ungrounded = True
            final_stage = "answer"
            answer = AnswerEvent(
                stage="answer",
                payload={
                    "answer_text": "No sources could be fetched.",
                    "citations": [],
                    "rejected_citations": [],
                    "documents": [],
                    "cost_usd": 0.0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "latency_ms": _latency_ms(started_at),
                    "degraded": False,
                    "ungrounded": True,
                },
            )
            self._record(request_id, req, final_stage, started_at, degraded, ungrounded, cost_usd, len(ok_docs), pages_failed, citations_valid, citations_rejected)
            yield answer
            return

        messages = self._fit_messages(req.query, ok_docs)
        remaining_before_llm = self._remaining_budget(started_at)
        if remaining_before_llm <= 0:
            event = AnswerEvent(stage="error", payload={"reason": "timeout", "retriable": True, "detail": "wall clock exceeded"})
            self._record(request_id, req, "error", started_at, degraded, ungrounded, cost_usd, len(ok_docs), pages_failed, citations_valid, citations_rejected)
            yield event
            return

        try:
            completion: CompletionResult = await asyncio.wait_for(
                self._llm.complete(
                    role="synthesis",
                    messages=messages,
                    max_output_tokens=self._config.llm_output_tokens,
                    temperature=0.0,
                    tools=None,
                ),
                timeout=remaining_before_llm,
            )
        except asyncio.TimeoutError:
            event = AnswerEvent(stage="error", payload={"reason": "timeout", "retriable": True, "detail": "wall clock exceeded"})
            self._record(request_id, req, "error", started_at, degraded, ungrounded, cost_usd, len(ok_docs), pages_failed, citations_valid, citations_rejected)
            yield event
            return
        except BudgetExceeded as exc:
            event = AnswerEvent(stage="error", payload={"reason": "budget_exhausted", "retriable": False, "detail": str(exc)})
            self._record(request_id, req, "error", started_at, degraded, ungrounded, cost_usd, len(ok_docs), pages_failed, citations_valid, citations_rejected)
            yield event
            return
        except LLMUnavailable as exc:
            event = AnswerEvent(stage="error", payload={"reason": "llm_unavailable", "retriable": True, "detail": str(exc)})
            self._record(request_id, req, "error", started_at, degraded, ungrounded, cost_usd, len(ok_docs), pages_failed, citations_valid, citations_rejected)
            yield event
            return

        degraded = completion.fallback_used or completion.model_drift or not completion.cost_authoritative or completion.finish_reason == "length"
        cost_usd = completion.cost_usd
        parsed_answer_text, raw_citations = self._parse_completion(completion.text)
        synthesized = AnswerEvent(
            stage="synthesized",
            payload={
                "tokens_in": completion.input_tokens,
                "tokens_out": completion.output_tokens,
                "model_id": completion.model_id,
                "raw_citation_count": len(raw_citations),
            },
        )
        events.append(synthesized)
        yield synthesized

        document_map = {doc.content_hash: doc for doc in ok_docs}
        valid_citations, rejected_citations = validate_citations(parsed_answer_text, raw_citations, document_map)
        citations_valid = len(valid_citations)
        citations_rejected = len(rejected_citations)
        validated = AnswerEvent(stage="validated", payload={"valid_count": citations_valid, "rejected_count": citations_rejected})
        events.append(validated)
        yield validated

        if not valid_citations:
            ungrounded = True

        final_stage = "answer"
        answer = AnswerEvent(
            stage="answer",
            payload={
                "answer_text": parsed_answer_text,
                "citations": valid_citations,
                "rejected_citations": rejected_citations,
                "documents": [{"url": doc.url, "content_hash": doc.content_hash} for doc in ok_docs],
                "cost_usd": completion.cost_usd,
                "tokens_in": completion.input_tokens,
                "tokens_out": completion.output_tokens,
                "latency_ms": _latency_ms(started_at),
                "degraded": degraded,
                "ungrounded": ungrounded,
            },
        )
        self._record(request_id, req, final_stage, started_at, degraded, ungrounded, cost_usd, len(ok_docs), pages_failed, citations_valid, citations_rejected)
        yield answer

    async def _crawl_ranked(self, ranked_rows: list[RankedResult]) -> list[Document]:
        semaphore = asyncio.Semaphore(self._config.crawl_concurrency)

        async def run_one(item: RankedResult) -> Document:
            async with semaphore:
                return await asyncio.to_thread(self._crawl.fetch, CrawlRequest(url=item.result.url))

        return await asyncio.gather(*(run_one(row) for row in ranked_rows))

    def _fit_messages(self, query: str, documents: list[Document]) -> list[dict]:
        working_docs = list(documents)
        while working_docs:
            messages = build_synthesis_messages(query, working_docs)
            if self._llm.count_tokens("synthesis", messages) <= self._config.llm_input_tokens:
                return messages
            if len(working_docs) > 1:
                working_docs.pop()
                continue
            for fraction in (0.5, 0.25):
                doc = working_docs[0]
                truncated_markdown = doc.markdown[: max(1, int(len(doc.markdown) * fraction))]
                truncated_doc = doc.model_copy(
                    update={
                        "markdown": truncated_markdown,
                        "enveloped_markdown": f'<untrusted_source url="{doc.url}" sha256="{doc.content_hash}">{truncated_markdown}</untrusted_source>',
                    }
                )
                messages = build_synthesis_messages(query, [truncated_doc])
                if self._llm.count_tokens("synthesis", messages) <= self._config.llm_input_tokens:
                    return messages
            raise InputTooLarge("synthesis input exceeds cap after truncation")
        return build_synthesis_messages(query, [])

    def _parse_completion(self, text: str) -> tuple[str, list[RawCitation]]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text, []

        answer_text = str(payload.get("answer_text", ""))
        raw_rows = [RawCitation.model_validate(item) for item in payload.get("citations", [])]
        return answer_text, raw_rows

    def _record(
        self,
        request_id: str,
        req: SearchRequest,
        final_stage: str,
        started_at: float,
        degraded: bool,
        ungrounded: bool,
        cost_usd: float,
        pages_ok: int,
        pages_failed: int,
        citations_valid: int,
        citations_rejected: int,
    ) -> None:
        if self._telemetry is None:
            return
        query_hash = hashlib.sha256(req.query.encode("utf-8")).hexdigest()
        latency_ms = _latency_ms(started_at)
        self._telemetry.record_span(
            "orchestrator.search",
            {
                "request_id": request_id,
                "query_hash": query_hash,
                "freshness": req.freshness,
                "max_results": req.max_results,
                "final_stage": final_stage,
                "degraded": degraded,
                "ungrounded": ungrounded,
                "cost_usd": cost_usd,
                "latency_ms": latency_ms,
                "crawled_pages_ok": pages_ok,
                "crawled_pages_failed": pages_failed,
                "citations_valid": citations_valid,
                "citations_rejected": citations_rejected,
            },
        )
        self._telemetry.increment_counter("orchestrator_requests_total", 1, {"final_stage": final_stage})
        self._telemetry.observe_histogram("orchestrator_latency_ms", latency_ms, {})
        self._telemetry.observe_histogram("orchestrator_pages_ok", pages_ok, {})
        self._telemetry.observe_histogram("orchestrator_pages_failed", pages_failed, {})
        if ungrounded:
            self._telemetry.increment_counter("orchestrator_ungrounded_total", 1, {})

    def _remaining_budget(self, started_at: float) -> float:
        elapsed = asyncio.get_running_loop().time() - started_at
        return self._config.wall_clock_s - elapsed

    @staticmethod
    def _simple_rerank(query: str, candidates: list[Result]) -> list[RankedResult]:
        out: list[RankedResult] = []
        for idx, item in enumerate(candidates):
            out.append(RankedResult(result=item, score=max(0.0, 1.0 - (idx * 0.01)), rerank_rank=idx))
        return out


def _latency_ms(started_at: float) -> int:
    return int((asyncio.get_running_loop().time() - started_at) * 1000)
