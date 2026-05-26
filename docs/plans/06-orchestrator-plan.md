# Plan 06 — Orchestrator

> Spec: [`docs/specs/06-orchestrator.md`](../specs/06-orchestrator.md) · spec wins on disagreement.

## Module layout

```
nexus/orchestrator/
├── pipeline.py         # search()/stream() entries; stage runner
├── events.py           # AnswerEvent ADT; SSE/MCP-agnostic
├── prompts.py          # synthesis system + user prompt assembly (ONLY place)
├── budget.py           # per-query wall clock + token + $ enforcement
└── __init__.py

tests/unit/orchestrator/
├── test_pipeline.py
├── test_events.py
├── test_prompts.py
└── test_budget.py
tests/integration/test_orchestrator_e2e.py
tests/security/test_prompt_assembly.py
```

## Public symbols

```python
# nexus/orchestrator/events.py
@dataclass(frozen=True)
class AnswerEvent:
    stage: Stage
    payload: dict
    ts: datetime

class Stage(str, Enum):
    ACCEPTED="accepted"
    EXPANDED="expanded"
    SEARCHED="searched"
    RANKED="ranked"
    PAGE_READY="page_ready"
    SYNTHESIZED="synthesized"
    VALIDATED="validated"
    ANSWER="answer"
    ERROR="error"

# nexus/orchestrator/pipeline.py
class Orchestrator:
    def __init__(self, search, rerank_fn, crawler, llm, citations_fn, cache, cost_meter, config): ...
    async def stream(self, req: SearchRequest, request_id: str) -> AsyncIterator[AnswerEvent]: ...
    async def aggregate(self, req: SearchRequest, request_id: str) -> AnswerEnvelope: ...

# nexus/orchestrator/prompts.py
SYSTEM_PROMPT: str   # canonical preamble from Spec 10
def build_synthesis_messages(query: str, ranked_docs: list[Document]) -> list[Message]: ...

# nexus/orchestrator/budget.py
class Budget:
    def __init__(self, wall_clock_s: float, max_input_tokens: int, max_pages: int): ...
    def deadline(self) -> datetime: ...
    def remaining_s(self) -> float: ...
    def fit_documents(self, docs: list[Document], llm: LLMClient, role: str) -> list[Document]: ...
```

## External dependencies

| Package | Why |
|---|---|
| stdlib `asyncio` | Concurrency. |
| Internal: `nexus.search`, `nexus.rerank`, `nexus.crawl`, `nexus.citations`, `nexus.llm`, `nexus.cache` | All wired by DI. |

No new third-party deps; orchestrator is integration code.

## Build order

1. **`events.py`** — `Stage` enum, `AnswerEvent` dataclass. Helpers `make(stage, **payload)`. ➜ `test_events.py` covers ordering invariants if any.
2. **`prompts.py`** — `SYSTEM_PROMPT` constant verbatim per Spec 10 §System prompt for grounded synthesis. `build_synthesis_messages(query, docs)` constructs:
   - `[{"role":"system","content":SYSTEM_PROMPT}, {"role":"user","content": <user_block>}]`
   - User block: `<user_query>{query}</user_query>\n\n` followed by each document wrapped via `wrap_untrusted(d.url, d.content_hash, d.markdown)`.
   - The wrap function lives in `nexus/crawl/envelope.py` (Plan 03). Import path: `from nexus.security.envelope import wrap_untrusted` (re-export for module surface).
   - ➜ `test_prompts.py` covers: preamble verbatim match, envelope wrapping per doc, no key/token leaked, deterministic output ordering.
   - ➜ `tests/security/test_prompt_assembly.py`: hostile doc body containing the literal envelope-close tag is escaped; system prompt never references caller's bearer token; assert no env var values appear.
3. **`budget.py`** — `Budget` class.
   - `deadline()` returns `started_at + wall_clock_s`.
   - `fit_documents(docs, llm, role)`: greedy fit. Sort docs by `rerank_score` desc. While `llm.count_tokens(messages_with(docs))` > `max_input_tokens`, drop the lowest-scored doc. If even top-1 doesn't fit, truncate top doc's markdown to halves until fit; record `truncated_doc_url`.
   - ➜ `test_budget.py`: wall clock countdown, fit_documents with synthetic docs and mock count_tokens.
4. **`Orchestrator.stream()`** (`pipeline.py`).
   - Step 1: emit `ACCEPTED` with `{request_id, normalized_query}`.
   - Step 2 (optional, gated): query expansion via `llm.complete(role="query-expansion", ...)`. Skip per config default. Emit `EXPANDED`.
   - Step 3: `search.search(req)`. Emit `SEARCHED`. On `SearchUnavailable`: emit `ERROR{reason="search_unavailable",retriable=true}` and return.
   - Step 4: `rerank(query, results, top_k, per_domain_cap)`. Emit `RANKED` with `kept = [{url,title,score}]`.
   - Step 5: parallel crawl under `asyncio.Semaphore(CRAWL_CONCURRENCY)`. For each crawl task, `as_completed`; emit `PAGE_READY` only for `status="ok"` documents. Other statuses logged + counted but not surfaced.
   - Step 5a: deadline check: if `remaining_s() < 5` before all crawls finish, cancel pending; proceed with completed `ok` docs.
   - Step 6: if no `ok` docs → emit `ANSWER{answer_text="No sources could be fetched.", citations=[], ungrounded=true}` and return.
   - Step 7: `budget.fit_documents(ok_docs, llm, "synthesis")`.
   - Step 8: `messages = build_synthesis_messages(query, fitted_docs)`. `result = await llm.complete(role="synthesis", messages=messages, max_output_tokens=...)`. Emit `SYNTHESIZED` with token counts. On `LLMUnavailable`: emit `ERROR{reason="llm_unavailable",retriable=true}`. On `BudgetExceeded`: emit `ERROR{reason="budget_exhausted",retriable=false}`.
   - Step 9: parse raw citations from `result.text` (format: footnote markers `[^id]` plus a structured block; details in `prompts.py`). Call `validate_citations(answer_text, raw, documents)`. Emit `VALIDATED` with counts.
   - Step 10: emit `ANSWER` with full `AnswerEnvelope`.
   - All steps wrapped in `try/except asyncio.CancelledError` → silent return (caller disconnected).
   - ➜ `test_pipeline.py`: stage ordering, error stages, ungrounded path, cancellation propagation.
5. **`Orchestrator.aggregate()`** — wraps `stream()`, returns terminal `ANSWER`/`ERROR` payload as `AnswerEnvelope` (or raises a structured exception for HTTP transport to map). Iterates and discards non-terminal events.
6. **Wire**: `nexus/main.py` constructs all dependencies, builds the orchestrator, hands it to both transports.

## Synthesis output parsing

Convention enforced by `prompts.py` system message:

```
Answer: <prose with [^cite_id] markers>

Citations:
- cite_id: <id-1>
  url: <url>
  content_hash: <hex>
  quote: <verbatim quote>
- cite_id: <id-2>
  ...
```

Parser is strict YAML-ish: tolerant of leading/trailing whitespace, intolerant of structural changes. Malformed → return zero citations (synthesis is then flagged as `ungrounded`).

## Test plan (mapping to spec invariants)

| Spec invariant | Test |
|---|---|
| `ACCEPTED` is first event | `test_pipeline::test_first_event` |
| Terminal is exactly one `ANSWER` or `ERROR` | `test_pipeline::test_terminal_event` |
| `page_ready` only for ok docs | `test_pipeline::test_skips_failed_docs` |
| No partial markdown in synthesis input | `test_prompts::test_only_ok_docs` |
| Bearer token never in messages | `tests/security/test_prompt_assembly.py` |
| Cancellation cancels within 2s | `test_pipeline::test_cancellation` |
| Wall-clock timeout cancels in-flight | `test_pipeline::test_wall_clock_timeout` |
| Ungrounded path when zero docs | `test_pipeline::test_ungrounded_no_docs` |
| Budget exhaustion → error | `test_pipeline::test_budget_exhausted` |
| Synthesis called with `tools=None` | `test_pipeline::test_no_tools_to_synthesis` |
| No raw HTML in synthesis messages | `test_prompts::test_envelope_wrap` |

## Adversarial tests required

`tests/security/test_prompt_assembly.py`:
- Hostile document with `</untrusted_source>` literal in body → escaped; assertion on the assembled message string.
- Document `url` containing `"` → attribute-escaped.
- Doc with attempted instruction injection — prompt assembly is unchanged (we trust the system preamble + envelope); the assertion is structural (no extra system messages, no preamble truncation).
- Attempt to pass `tools=[...]` to `complete(role="synthesis", ...)` → raises (defense in depth from Plan 05).

## Risks & mitigations

- **Race between cancellation and citation validation** — cancellation cleanly aborts; partial citations dropped.
- **Greedy fit_documents removes the most relevant doc** if its markdown is largest. Mitigation: rank-aware fit; never drop top-1 unless even alone it doesn't fit (then truncate).
- **Synthesis output parser too strict**: malformed output → zero citations, ungrounded path. Acceptable; logged WARN.
- **Streaming back-pressure**: transports consume events; if a slow consumer blocks, events queue. Mitigation: bounded asyncio.Queue (size 64) per request; full queue cancels pipeline.

## Done criteria
- [ ] All unit + security + integration tests pass.
- [ ] `test_pipeline::test_full_happy_path` runs end-to-end with all components real except network mocks; emits the full stage sequence; returns ≥ 1 valid citation.
- [ ] `mypy --strict` clean.
- [ ] No `from nexus.llm import` outside `orchestrator/` constructs synthesis messages (enforced by an import-graph test).
