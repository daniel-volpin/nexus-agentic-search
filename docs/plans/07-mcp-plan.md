# Plan 07 — MCP Transport

> Spec: [`docs/specs/07-mcp.md`](../specs/07-mcp.md) · spec wins on disagreement.

## Module layout

```
nexus/transport/
├── mcp.py              # FastMCP v2 app; tool & resource registration
├── http.py             # (Plan 08)
└── auth.py             # shared bearer middleware

tests/unit/transport/
├── test_mcp_surface.py        # tool inventory + schema
├── test_mcp_streaming.py      # progress notifications
└── test_mcp_auth.py
tests/security/test_mcp_surface.py
tests/integration/test_mcp_e2e.py
```

## Public symbols

```python
# nexus/transport/mcp.py
def build_mcp_app(orch: Orchestrator, config: TransportConfig) -> FastMCP: ...
async def run_mcp(orch: Orchestrator, config: TransportConfig) -> None: ...

# nexus/transport/auth.py
class BearerAuth:
    def __init__(self, token: SecretStr): ...
    def verify(self, presented: str | None) -> bool: ...
```

## External dependencies

| Package | Why |
|---|---|
| `fastmcp` v2 | MCP framework. |
| `mcp` | Underlying protocol package (transitive but pinned). |
| `uvicorn` | ASGI server for streamable HTTP transport. |

Pin both `fastmcp` and `mcp` to exact versions; verify compatibility in CI.

## Build order

1. **`auth.py`** — `BearerAuth`. Constant-time comparison (`hmac.compare_digest`). ➜ `test_auth.py`: timing-attack resistance covered by reading the docstring + asserting `compare_digest` is used (lightweight static check).
2. **`mcp.py` scaffold** — `FastMCP("nexus-agentic-search")` instance. Health: register a heartbeat method (FastMCP-provided). Disable any default examples / demo tools.
3. **Tool registration** — `@mcp.tool(name="agentic_search", ...)` with input schema exactly matching Spec 07. Description is purely informational; MUST NOT contain instruction-shaped strings (asserted by test). The tool function:
   - Accepts validated kwargs (FastMCP enforces input schema).
   - Builds `SearchRequest`.
   - Acquires per-call `request_id` (uuid4).
   - Iterates `orch.stream(req, request_id)`:
     - Non-terminal events → `ctx.report_progress(progress, total, message=json.dumps({stage, payload}))` or the FastMCP v2 equivalent for progress notifications.
     - Terminal `ANSWER` event → return its payload (validated against the output schema).
     - Terminal `ERROR` event → raise a `ToolError` with `{reason, retriable}` body.
   - Output validated against output schema before return.
4. **Resources** — read-only:
   - `nexus://status` → returns `{uptime_s, version, daily_cost_usd, requests_today}`. `daily_cost_usd` omitted if `STATUS_REVEAL_COST=false`.
   - `nexus://config/roles` → returns sanitized role→model map.
5. **Auth integration** — FastMCP v2's auth hooks: register a token verifier that calls `BearerAuth.verify` on every connection / request, per FastMCP v2 docs. Missing/invalid → reject before tool dispatch.
6. **Run** — `run_mcp()` boots a Uvicorn server bound to `0.0.0.0:8185` (container-internal). Streamable HTTP transport. Healthcheck endpoint is implicit via uvicorn `/healthz` or a small ASGI middleware route.
7. **Wire into `main.py`** — both transports (this + HTTP) run in the same process. Use `asyncio.gather` so a failure in one bubbles up cleanly.

## Configuration loading

```python
class TransportConfig(BaseSettings):
    mcp_token: SecretStr        # from NEXUS_MCP_TOKEN env
    http_token: SecretStr       # from NEXUS_HTTP_TOKEN env (may equal mcp_token)
    bind_host: str = "0.0.0.0"
    bind_port: int = 8185
    metrics_port: int = 9090
    status_reveal_cost: bool = True
    cors_enabled: bool = False
    cors_origins: list[str] = []
```

Startup fails if either token is empty or shorter than 32 chars.

## Test plan (mapping to spec invariants)

| Spec invariant | Test |
|---|---|
| Exactly one `agentic_search` tool | `tests/security/test_mcp_surface.py::test_tool_inventory` |
| No `crawl`/`search`/`llm`/etc. tools | same |
| Input schema enforced | `test_mcp_surface::test_input_validation` |
| Output schema validated server-side | `test_mcp_surface::test_output_schema` |
| Tool description has no instruction strings | `test_mcp_surface::test_description_clean` (asserts absence of "You are", "Always", "Never", etc.) |
| Bearer auth required | `test_mcp_auth.py` |
| Progress notifications emitted per non-terminal event | `test_mcp_streaming::test_progress_events` |
| Caller disconnect cancels orchestrator | `test_mcp_streaming::test_cancellation` |
| No host port published | manual / compose smoke; assert via `docker port nexus-search` returns empty |
| Result size cap | `test_mcp_surface::test_result_truncation` |

## Adversarial tests required

`tests/security/test_mcp_surface.py`:
- Tool inventory enumerated; assert exact membership.
- Resource list enumerated; assert exact membership.
- Wrong token → connection rejected, no info leak.
- Send a payload with an unknown field → rejected.
- Send a 100KB query → rejected by size cap.

## Risks & mitigations

- **FastMCP v2 API change**: pin `==`; one-file wrapper isolates churn.
- **Progress-notification client support uneven**: tested non-streaming clients still receive the correct final result; tested via a synthetic client that ignores progress.
- **Tool description as injection surface**: enforced by a unit test that scans for instruction-shaped substrings.
- **mcp ↔ fastmcp version skew**: CI step that imports both and asserts compatibility (a smoke test).

## Done criteria
- [ ] All unit + security + integration tests pass.
- [ ] `test_mcp_surface::test_tool_inventory` asserts the exact tool set `{"agentic_search"}`.
- [ ] Tool description audited and free of instruction-shaped text.
- [ ] Integration test connects from an adjacent container (Docker compose under test), calls the tool, receives progress events + final result.
- [ ] `mypy --strict` clean for `nexus/transport/mcp.py` and `nexus/transport/auth.py`.
