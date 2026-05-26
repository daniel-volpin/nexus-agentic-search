# Plan 05 — LLM Gateway

> Spec: [`docs/specs/05-llm.md`](../specs/05-llm.md) · spec wins on disagreement.

## Module layout

```
nexus/llm/
├── client.py            # LLMClient Protocol + DefaultLLMClient (LiteLLM-backed)
├── litellm_adapter.py   # thin LiteLLM call wrapper with redacted logging
├── roles.py             # role config loader, dated-model validation
├── cost.py              # cost meter + daily counter (uses Spec 09 cache)
├── redact.py            # logger Filter implementing secret patterns
└── types.py             # Message, ToolSpec, CompletionResult, exceptions

tests/unit/llm/
├── test_roles.py
├── test_cost.py
├── test_redact.py
├── test_litellm_adapter.py
└── test_client.py
tests/security/test_redaction.py
tests/security/test_budget.py
```

## Public symbols

```python
# nexus/llm/types.py
class LLMUnavailable(Exception): ...
class BudgetExceeded(Exception): ...
class InputTooLarge(Exception): ...

class Message(TypedDict): ...
class ToolSpec(TypedDict): ...
class ToolCall(TypedDict): ...
class CompletionResult: ...
class StreamChunk: ...

# nexus/llm/client.py
class LLMClient(Protocol):
    async def complete(self, role, messages, max_output_tokens, temperature=0.0, tools=None) -> CompletionResult: ...
    async def stream_complete(self, role, messages, max_output_tokens, temperature=0.0) -> AsyncIterator[StreamChunk]: ...
    def count_tokens(self, role, messages) -> int: ...

class DefaultLLMClient: ...

# nexus/llm/roles.py
@dataclass(frozen=True)
class RoleConfig:
    primary: str         # dated model id, validated
    fallbacks: tuple[str, ...]
    max_input_tokens: int
    max_output_tokens: int

def load_role_config(path: Path) -> dict[str, RoleConfig]: ...
def assert_dated(model_id: str) -> None: ...   # raises on alias

# nexus/llm/cost.py
class CostMeter:
    async def record(self, role: str, cost_usd: float) -> None: ...
    async def daily_total(self, role: str | None = None) -> float: ...
    async def check_budget(self, role: str) -> None: ...   # raises BudgetExceeded if over

# nexus/llm/redact.py
class SecretRedactor(logging.Filter): ...
PATTERNS: tuple[re.Pattern, ...] = (...)
```

## External dependencies

| Package | Why |
|---|---|
| `litellm` | Multi-provider routing. Set `litellm.set_verbose = False` at import. |
| `tiktoken` | Token counting fallback for OpenAI-shaped tokenization. Other models use LiteLLM's `token_counter`. |
| `diskcache` | Via Spec 09 backend, used by `CostMeter` for daily counters. |

## Build order

1. **Types** (`types.py`). Exceptions, TypedDicts, dataclasses. ➜ `test_types.py` smoke.
2. **`redact.py`** — logging Filter. Patterns from Spec 10. `filter()` mutates `record.msg` and `record.args`. Also wraps formatted exceptions: hook into a custom exception formatter that masks `os.environ` values. ➜ `tests/security/test_redaction.py` (catalog from Spec 13).
3. **`roles.py`** — load TOML/YAML, validate every model id matches the dated-pattern allowlist (e.g., `gpt-\d`, `claude-.*-\d{4}-\d{2}-\d{2}` etc.). `assert_dated()` is the gatekeeper. ➜ `test_roles.py`: covers parse, reject aliases, fallback chain ordering, missing-role error.
4. **`cost.py`** — CostMeter. Backed by `nexus.cache.namespaces.cost_daily` (Spec 09). `record()` adds cents to today's counter atomically. `check_budget()` reads `DAILY_USD_BUDGET` from config; raises `BudgetExceeded` if `>=` cap, logs WARN at 80%. ➜ `test_cost.py`: in-memory backend, time-controlled date. `tests/security/test_budget.py`: end-to-end with the cache backend.
5. **`litellm_adapter.py`** — thin wrapper around `litellm.acompletion`. Inputs: model id, messages, tools (None for `synthesis` role), `max_tokens`, `temperature`. Output: parsed `CompletionResult` with `input_tokens`, `output_tokens`, `cost_usd` from LiteLLM's response. Failure: re-raises as `LLMUnavailable` after exhausting one HTTP retry on 429/5xx. ➜ `test_litellm_adapter.py` mocks `litellm.acompletion`.
6. **`DefaultLLMClient`** (`client.py`).
   - On `complete(role, ...)`:
     1. `roles[role]` resolved; assert dated model.
     2. `count_tokens(role, messages)`; if > `max_input_tokens` → raise `InputTooLarge`.
     3. `cost.check_budget(role)`.
     4. Try primary; on `LLMUnavailable`, try each fallback in order.
     5. Record cost on success; record outcome on failure.
     6. `model_drift` check: if returned `model` field differs from primary, set `model_drift=True` on result.
   - `stream_complete()` similar; aggregates token counts from chunks, charges cost on final chunk.
   - For `role == "synthesis"`: `tools=None` enforced at this layer; raises if caller passes tools. (Defense-in-depth with prompt rules.)
   - ➜ `test_client.py`: fallback chain, budget refuse, synthesis-tools-disabled.
7. **`wrap_untrusted` re-export** lives in `nexus/security/envelope.py`, but synthesis prompt assembly that *uses* it is in `nexus/orchestrator/prompts.py` (Plan 06).
8. **Wire into `nexus/main.py`**: build `DefaultLLMClient` once, inject into orchestrator. Install `SecretRedactor` on the root logger at the very top of `main.py` (before any other import logs).

## Configuration loading

`config/llm.toml` shape per Spec 05. `nexus/config.py` exposes:

```python
class LLMConfig(BaseSettings):
    config_path: Path = Path("config/llm.toml")
    daily_usd_budget: float = 10.00
    soft_cap_pct: float = 0.80
    redaction_enabled: bool = True
```

API keys loaded from env (LiteLLM convention): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` etc. `DefaultLLMClient` does NOT read them directly — LiteLLM does. We validate at startup: for each provider listed in any role's fallback chain, assert the corresponding env var is set; missing → log warning and remove that fallback from the chain.

## Test plan (mapping to spec invariants)

| Spec invariant | Test |
|---|---|
| Every call logs through redaction | `test_redact::test_filter_applied_to_record`, end-to-end in `tests/security/test_redaction.py` |
| No model ID resolves to an alias | `test_roles::test_rejects_alias`; runtime check in `test_client::test_model_drift_flag` |
| Input token count BEFORE send; over-cap raises | `test_client::test_input_too_large_raises` |
| `max_output_tokens` always set | `test_litellm_adapter::test_max_tokens_required` (asserts call arg) |
| Synthesis role has tool calling disabled | `test_client::test_synthesis_tools_none` |
| Daily budget hard cap | `tests/security/test_budget.py` |
| Soft cap warning at 80% | `test_cost::test_soft_cap_warn` |
| Fallback chain order | `test_client::test_fallback_order` |
| API key never in log / response / exception | `tests/security/test_redaction.py` full catalog |

## Risks & mitigations

- **LiteLLM API churn**: pin tight; wrapper lives in one file so a migration is contained.
- **Pricing table drift**: LiteLLM ships a pricing table; pin its version and validate at startup against the pinned hash. On mismatch, log warning, flag `cost_authoritative=false` on responses.
- **Token-count miscount across providers**: token counts come from LiteLLM's response when available (post-call) and from `tiktoken` for pre-call estimation. Pre-call estimate is conservative (rounds up by 10%) so we don't accidentally bust caps.
- **Provider OpenAI-compat shims** (Anthropic, Gemini, Ollama) differ: each has its own quirks. Mitigation: integration smoke per provider in a slow optional test (`make test-providers`) gated by API keys present.

## Done criteria
- [ ] All unit + security tests pass.
- [ ] `litellm.set_verbose = False` enforced at module import (asserted by `test_litellm_adapter::test_verbose_off`).
- [ ] Calling `complete(role="synthesis", tools=[...])` raises with a clear error (asserted).
- [ ] Daily budget hard-cap end-to-end test passes.
- [ ] `mypy --strict` clean.
