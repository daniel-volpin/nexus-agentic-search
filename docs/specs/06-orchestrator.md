# Spec 06 — Orchestrator

## Purpose
Coordinate the search → rerank → crawl → synthesis → citation-validation pipeline, stream stage events to the caller, enforce per-query budgets, propagate cancellation.

## Bounded context

**Does**
- Own the request lifecycle from `SearchRequest` to the final `answer` event.
- Run stages in fixed order, parallelizing the crawl stage under a semaphore.
- Stream `AnswerEvent` chunks to the transport layer.
- Enforce per-query wall-clock timeout, token budget, and cost budget.
- Propagate cancellation when the caller disconnects.
- Wrap every crawled document in an untrusted-source envelope before LLM ingestion.

**Does NOT**
- Own provider integrations (delegates to components).
- Decide model identities (LLM Gateway does).
- Format transport-specific responses — emits component events; transport layer adapts.

## Stage contract

```
accepted   → query received and validated
expanded   → query-expansion done (or skipped)
searched   → SERP returned
ranked     → rerank done, top-K selected
page_ready → emitted once per crawled document, as soon as each completes
synthesized → LLM produced answer + raw citations
validated  → citations engine verified citations
answer     → final answer emitted (terminal)
error      → terminal failure with structured reason
```

Stages are emitted strictly in this order. Multiple `page_ready` events may interleave with each other but always between `ranked` and `synthesized`.

## Inputs / Outputs

```python
class SearchRequest:                     # from Spec 01
    query: str
    freshness: Literal[...]
    max_results: int
    lang: str | None
    country: str | None

class AnswerEvent:
    stage: Literal[
        "accepted","expanded","searched","ranked",
        "page_ready","synthesized","validated","answer","error"
    ]
    payload: dict                        # stage-specific schema below
    ts: datetime

class AnswerEnvelope:                    # terminal payload at stage="answer"
    answer_text: str
    citations: list[Citation]            # validated only (Spec 04)
    rejected_citations: list[CitationRejection]
    documents: list[DocumentRef]         # url + content_hash only; full text NOT echoed
    cost_usd: float
    tokens_in: int
    tokens_out: int
    latency_ms: int
    degraded: bool                       # true if any fallback was used
    ungrounded: bool                     # true if no citations survived validation
```

### `payload` shape per stage

| stage | payload |
|---|---|
| `accepted` | `{request_id, normalized_query}` |
| `expanded` | `{sub_queries: list[str]}` |
| `searched` | `{result_count, provider}` |
| `ranked` | `{kept: list[{url,title,score}]}` |
| `page_ready` | `{url, content_hash, status, render_ms}` |
| `synthesized` | `{tokens_in, tokens_out, model_id, raw_citation_count}` |
| `validated` | `{valid_count, rejected_count}` |
| `answer` | `AnswerEnvelope` |
| `error` | `{reason, retriable: bool, detail}` |

## Concurrency model

- One asyncio task per pipeline.
- Crawl stage uses an asyncio Semaphore (`CRAWL_CONCURRENCY`, default 4).
- Each crawl runs under its own per-host rate limiter (Spec 03).
- Synthesis is strictly sequential after all crawls complete OR after the per-query wall clock reaches 80% of budget (whichever first — orchestrator may proceed with whatever pages succeeded).
- `asyncio.CancelledError` propagates: in-flight HTTP requests are aborted, browser contexts closed, partial state dropped.

## Budgets and timeouts

| Budget | Default | Behavior over budget |
|---|---|---|
| Per-query wall clock | 60s | Cancel in-flight, emit best-effort `answer` if synthesis reachable, else `error`. |
| Per-query LLM input tokens | 32k (synthesis role) | Truncate ranked documents by removing lowest-scored docs until under cap; if even top-1 doesn't fit, truncate top doc to half then quarter; if still over, raise. |
| Per-query LLM output tokens | per-role cap from Spec 05 | Truncate at provider; orchestrator records `finish_reason="length"` and surfaces. |
| Per-query crawl pages | 8 | Hard cap on number of `page_ready` events. |
| Per-day USD | from Spec 05 | `LLMUnavailable` → orchestrator emits `error{reason="budget_exhausted"}`. |

## Pipeline assembly rules

- query-expansion is opt-in via config (`ENABLE_QUERY_EXPANSION=false` by default).
- Synthesis input MUST consist of:
  - the original user query (in a clearly-labeled `<user_query>` block),
  - the security preamble (Spec 10),
  - each ranked document wrapped in `<untrusted_source url="…" sha256="…">…</untrusted_source>`.
- Synthesis is configured with NO tool access — tool calling is disabled at the LLM Gateway boundary for the `synthesis` role.
- Raw citations from synthesis pass through the Citations engine (Spec 04) before being emitted.

## Invariants

- The `accepted` event is always emitted first.
- The terminal event is exactly one of `answer` or `error`.
- `page_ready` count ≤ ranked.kept_count and ≤ CRAWL_PAGES_MAX.
- No `page_ready` is emitted for documents with `status != "ok"` — those are logged but not surfaced (the orchestrator may emit summary-level info in `synthesized` payload).
- `answer.citations` ⊆ documents actually fetched (`page_ready` set with status=ok).
- `answer.documents` only contains URL + content_hash; the orchestrator never echoes full document text on the wire.
- Cancellation from caller → all background tasks cancelled within 2 seconds.

## Failure modes

| Failure | Required behavior |
|---|---|
| `SearchUnavailable` from Spec 01 | Emit `error{reason="search_unavailable", retriable=true}`; terminal. |
| All crawls fail | Continue to synthesis with empty document set; orchestrator MUST refuse to synthesize unsupported claims — emit `answer{ungrounded=true, answer_text="No sources could be fetched.", citations=[]}`. |
| Synthesis raises `LLMUnavailable` | Emit `error{reason="llm_unavailable", retriable=true}`. |
| `BudgetExceeded` | Emit `error{reason="budget_exhausted", retriable=false}`. |
| Citation engine rejects all citations | Emit `answer{ungrounded=true, ...}` with the answer_text but no citations. |
| Wall clock exceeded before any crawl completes | Emit `error{reason="timeout", retriable=true}`. |
| Caller disconnect | Cancel pipeline; do not emit `error` (no one is listening). |

## Security requirements

- The bearer token, env vars, and cache key plaintext are NEVER passed into LLM messages.
- Synthesis prompt construction is centralized in `orchestrator.prompts` — no LLM call site outside that module may construct synthesis messages.
- Any document with `status != "ok"` is excluded from synthesis input entirely (no partial markdown, no header).

## Telemetry contract

Root span `orchestrator.search` wraps the whole pipeline. Child spans for each component (search, rerank, crawl, llm, citations).

Span `orchestrator.search`
- Attributes: `request_id`, `query_hash`, `freshness`, `max_results`, `final_stage`, `degraded`, `ungrounded`, `cost_usd`, `latency_ms`, `crawled_pages_ok`, `crawled_pages_failed`, `citations_valid`, `citations_rejected`.

Metrics
- `orchestrator_requests_total{final_stage}` counter.
- `orchestrator_latency_ms` histogram.
- `orchestrator_pages_ok` histogram.
- `orchestrator_pages_failed` histogram.
- `orchestrator_ungrounded_total` counter (security-sensitive).

## Out of scope / deferred

- Multi-step agent loops (iterative search → reflect → search again).
- Caller-supplied tool definitions (no MCP tool passthrough from caller).
- Conversation memory.

## Open questions

- Whether to emit `page_ready` even for non-ok statuses, with `status` set, so the caller can show "tried but failed" UX. Default: skip.
- Whether to allow per-request override of `CRAWL_CONCURRENCY` and `CRAWL_PAGES_MAX` (default: no — config-only).
