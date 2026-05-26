# Spec 07 — MCP Transport

## Purpose
Expose the orchestrator over the Model Context Protocol via FastMCP v2 so the adjacent chat-agent container can call it as a tool with streaming progress.

## Bounded context

**Does**
- Run a FastMCP v2 server inside the same container as the orchestrator.
- Define exactly one MCP tool (`agentic_search`) plus a small set of read-only resources.
- Translate orchestrator `AnswerEvent` stream into MCP `notifications/progress` events.
- Enforce bearer auth on the MCP connection.
- Apply transport-level size limits on tool inputs and results.

**Does NOT**
- Mount the orchestrator as multiple tools (no `crawl`, no `search` exposed directly — these are internal-only and must NOT be callable by the LLM).
- Negotiate capabilities beyond what FastMCP v2 provides by default.
- Persist sessions.

## Tool surface

```
tool: agentic_search
description: "Run an agentic web search and return a citation-grounded answer. The service handles search, ranking, fetching, and synthesis. Use one focused question per call."
input_schema:
  type: object
  required: [query]
  additionalProperties: false
  properties:
    query:
      type: string
      minLength: 1
      maxLength: 512
    freshness:
      type: string
      enum: [any, day, week, month, year]
      default: any
    max_results:
      type: integer
      minimum: 1
      maximum: 50
      default: 20
    lang:
      type: string
      pattern: "^[a-z]{2}$"
    country:
      type: string
      pattern: "^[A-Z]{2}$"
output_schema:
  type: object
  required: [answer_text, citations, ungrounded, cost_usd, latency_ms]
  properties:
    answer_text:        { type: string }
    citations:
      type: array
      items:
        type: object
        required: [url, content_hash, byte_start, byte_end, quote, claim_id]
    rejected_citations: { type: array }
    documents:
      type: array
      items:
        type: object
        required: [url, content_hash]
    cost_usd:           { type: number }
    tokens_in:          { type: integer }
    tokens_out:         { type: integer }
    latency_ms:         { type: integer }
    degraded:           { type: boolean }
    ungrounded:         { type: boolean }
```

Tools NOT exposed: `crawl`, `search`, `rerank`, `synthesize`, `llm_complete`, anything else. The single `agentic_search` tool is the only callable.

## Resources (read-only)

- `nexus://status` — health JSON: `{uptime_s, version, daily_cost_usd, requests_today}`.
- `nexus://config/roles` — current role→model mapping (sensitive values redacted).

No write resources. No prompts surface (no MCP `prompts/*` exposed).

## Streaming model

- Orchestrator's `AnswerEvent` stream is mapped to MCP `notifications/progress` with `progress_token` set per call.
- `accepted`, `expanded`, `searched`, `ranked`, `page_ready`, `synthesized`, `validated` → progress notifications with `{stage, payload}`.
- Final `answer` event → returned as the tool result (not a progress notification).
- `error` event → returned as a tool error with the structured reason.

Clients that don't support `notifications/progress` still receive a correct (non-streamed) final result.

## Auth

- Bearer token via MCP transport headers. Implementation depends on FastMCP v2 transport choice (streamable HTTP for adjacent-container use).
- Token loaded from `NEXUS_MCP_TOKEN` env var; mandatory at startup. Service refuses to start if empty.
- Single token; rotated manually.
- Request rejected with `Unauthorized` if token mismatches; no retries surfaced to caller.

## Size limits

- Input JSON ≤ 4 KB (orchestrator validates again).
- Tool result text fields capped: `answer_text` ≤ 16 KB, each `quote` ≤ 800 chars (Spec 04), citations ≤ 32 entries, documents ≤ 16 entries.
- Over-cap result triggers a `validated` failure: result is truncated and `degraded=true` is set.

## Invariants

- Exactly one `agentic_search` tool registered at startup.
- `output_schema` is validated server-side before sending. If orchestrator produced a malformed `AnswerEnvelope`, the MCP layer raises and returns a tool error rather than passing corrupt JSON.
- No internal types leak: stack traces, file paths, environment variables, and provider API responses are not present in any tool error message.
- Server binds only on the Docker user-defined bridge `agentic-net`. No host port published. No `0.0.0.0` bind.

## Failure modes

| Failure | Behavior |
|---|---|
| Auth missing/invalid | Reject connection with 401-equivalent; log structured event with caller IP. |
| Tool input fails schema validation | Tool error `invalid_input`; do not invoke orchestrator. |
| Orchestrator emits `error` | Tool error with `{reason, retriable}` (no detail field leaked). |
| FastMCP v2 internal exception | Generic tool error; full exception logged server-side with redaction. |
| Caller disconnect mid-stream | Cancel orchestrator pipeline; no further events emitted. |
| Result exceeds size cap | Truncate, set `degraded=true`, log warning. |

## Security requirements

- See Spec 10 §Transport hardening.
- `nexus://config/roles` MUST redact model IDs only? No — model IDs are fine to expose. MUST redact provider names if their presence helps an attacker probe (e.g., omit which provider served last call). Acceptable to publish currently-active providers but never their keys or endpoint URLs.
- MCP tool descriptions and parameter descriptions MUST NOT contain any text resembling instructions ("You are…", "Always…") — these descriptions reach the calling LLM and create injection surface from your own server.

## Telemetry contract

Span `mcp.tool.agentic_search` wraps `orchestrator.search`.
- Attributes: `caller_id` (from auth context if available, else `anonymous`), `progress_events_sent`, `result_bytes`, `truncated` (bool).

Metrics
- `mcp_tool_calls_total{tool="agentic_search",outcome}` counter.
- `mcp_tool_latency_ms{tool="agentic_search"}` histogram.
- `mcp_unauthorized_total` counter (alert on rate spike).

## Out of scope / deferred

- Multiple tools / sub-tools.
- MCP `roots/*`, `sampling/*`, `prompts/*` features.
- stdio transport (adjacent container uses HTTP).
- Per-tenant token scoping.

## Open questions

- FastMCP v2 transport: streamable HTTP recommended; verify against current FastMCP v2 docs at implementation.
- Whether to also expose `nexus://golden-queries` (for caller-visible quality testing) — leaning no.
