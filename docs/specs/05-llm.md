# Spec 05 — LLM Gateway

## Purpose
Provider-neutral LLM access via LiteLLM with a thin in-house interface, role-based model pinning, per-call cost accounting, key redaction, and untrusted-source envelope helpers.

## Bounded context

**Does**
- Expose a small interface (`LLMClient`) abstracting LiteLLM.
- Map named roles (`synthesis`, `rerank-decision`, `query-expansion`) to pinned dated model IDs.
- Stream completions with token accounting per chunk.
- Enforce per-request input and output token caps.
- Enforce per-day dollar cap, fall back across providers when over budget.
- Redact provider keys and PII patterns from logs and exceptions.
- Provide envelope helpers for wrapping crawled text as untrusted content (Spec 10).

**Does NOT**
- Decide WHAT to prompt — orchestrator owns prompts.
- Persist conversation state.
- Implement agent loops, retrieval-augmented chains, or tool selection logic.
- Talk to embedding stores (no vector DB in v1).

## Interface

```python
class Message(TypedDict):
    role: Literal["system","user","assistant","tool"]
    content: str

class ToolSpec(TypedDict):
    name: str
    description: str
    parameters: dict      # JSON Schema

class ToolCall(TypedDict):
    id: str
    name: str
    arguments: dict

class CompletionResult:
    text: str
    finish_reason: Literal["stop","length","tool_calls","content_filter","error"]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    tool_calls: list[ToolCall]
    model_id: str
    role: str

class LLMClient(Protocol):
    async def complete(
        self,
        role: str,
        messages: list[Message],
        max_output_tokens: int,
        temperature: float = 0.0,
        tools: list[ToolSpec] | None = None,
    ) -> CompletionResult: ...

    async def stream_complete(
        self,
        role: str,
        messages: list[Message],
        max_output_tokens: int,
        temperature: float = 0.0,
    ) -> AsyncIterator[StreamChunk]: ...

    def count_tokens(self, role: str, messages: list[Message]) -> int: ...
```

## Role configuration

```toml
# config/llm.toml
[role.synthesis]
primary  = "openai/gpt-4o-2024-11-20"
fallback = ["anthropic/claude-sonnet-4-5", "gemini/gemini-2.0-flash-001"]
max_input_tokens  = 32000
max_output_tokens = 2000

[role.rerank-decision]
primary  = "openai/gpt-4o-mini-2024-07-18"
fallback = ["anthropic/claude-haiku-4-5-20251001"]
max_input_tokens  = 8000
max_output_tokens = 200

[role.query-expansion]
primary  = "openai/gpt-4o-mini-2024-07-18"
fallback = ["anthropic/claude-haiku-4-5-20251001"]
max_input_tokens  = 2000
max_output_tokens = 200
```

Model IDs MUST be dated; non-dated aliases (e.g., `gpt-4o`, `claude-3-5-sonnet-latest`) are rejected at startup.

## Untrusted-source envelope (helper)

```python
def wrap_untrusted(url: str, content_hash: str, body: str) -> str:
    # Returns: <untrusted_source url="…" sha256="…">…</untrusted_source>
    # Body is escaped so the closing tag cannot be forged from the body itself.
```

Synthesis prompts MUST include the security preamble defined in Spec 10 §System prompt for grounded synthesis.

## Cost accounting

- Each call records `cost_usd` from LiteLLM's pricing table (validated at startup against pinned table version).
- Daily counter in SQLite, key `(date_utc, role)`.
- Soft cap (default 80% of daily budget): emit warning, continue.
- Hard cap (100%): all subsequent calls refused with `BudgetExceeded` (degraded mode — orchestrator returns last-known partial result).
- Configurable via env: `DAILY_USD_BUDGET=10.00`.

## Invariants

- Every call passes through `_redact_secrets` before logging. Patterns redacted: `sk-[A-Za-z0-9-_]{20,}`, `sk-ant-[A-Za-z0-9-_]{20,}`, `AIza[0-9A-Za-z-_]{35}`, `gsk_[A-Za-z0-9]{40,}`, anything matching `Authorization: Bearer …`.
- No model ID resolves to an alias at runtime. If LiteLLM returns a different `model` field than configured, log mismatch and mark response `model_drift=true`.
- Input token count is computed BEFORE sending; if exceeds `max_input_tokens`, the input is truncated by the orchestrator (engine raises `InputTooLarge` — never silently truncates here).
- `max_output_tokens` is always set; never relies on provider default.
- Temperature default 0.0 for synthesis; rerank/expansion may use higher.

## Failure modes

| Failure | Behavior |
|---|---|
| Primary provider 5xx / 429 / timeout | Try next fallback in order; record which model served. |
| All providers fail | Raise `LLMUnavailable`; orchestrator decides whether to refuse or return partial. |
| Provider returns malformed JSON tool call | Repair attempt capped at 1 retry with stricter system message; on failure return `finish_reason="error"`. |
| `InputTooLarge` | Raised; never auto-truncated by the gateway. |
| `BudgetExceeded` (daily) | Raised; orchestrator may surface a structured error to caller. |
| Key missing for a provider listed in role config | Skip that provider in fallback chain; log warning at startup. |
| Pricing table drift (LiteLLM table version != pinned) | Log warning; cost accounting continues with the LiteLLM-reported number but marked `cost_authoritative=false`. |

## Security requirements

- API keys loaded from env (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`/`GEMINI_API_KEY`, …). Never in code, image, or persisted state.
- LiteLLM debug logging permanently disabled (`litellm.set_verbose = False`).
- Outbound restricted to provider endpoints in the egress allowlist (Spec 12).
- No prompt / response payload is logged at INFO level. DEBUG-level payload logs include only hashed contents (`sha256(prompt)[:8]`).
- The orchestrator MUST NOT pass the bearer token, internal URLs, secret env vars, or any cache-key plaintext into any `messages` field. This is enforced by code review and a static check (see Spec 13).

## Telemetry contract

Span `llm.<role>`
- Attributes: `role`, `model_id_resolved`, `input_tokens`, `output_tokens`, `cost_usd`, `latency_ms`, `finish_reason`, `fallback_used` (bool), `model_drift` (bool).

Metrics
- `llm_input_tokens_total{role,model}` counter.
- `llm_output_tokens_total{role,model}` counter.
- `llm_cost_usd_total{role}` counter.
- `llm_latency_ms{role}` histogram.
- `llm_errors_total{role,reason}` counter.
- `llm_budget_remaining_usd{role}` gauge.

## Out of scope / deferred

- Embeddings (no vector store in v1).
- Vision input (synthesis is text-only).
- Hosted prompt-injection classifiers / guardrails (e.g., Llama Guard) — may add later as an opt-in pre-filter.
- LangSmith / hosted tracing (rejected for privacy).

## Open questions

- Whether to expose `cache_control` (Anthropic prompt caching) as a first-class parameter on `LLMClient.complete`, or hide it behind LiteLLM auto-cache hints.
- Local-model integration shape: separate endpoint per local model, or a single `ollama/<model>` route via LiteLLM's OpenAI-compat passthrough.
