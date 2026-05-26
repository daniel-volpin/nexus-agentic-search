# Spec 08 — HTTP Transport

## Purpose
Expose the orchestrator over plain HTTP with FastAPI for callers that do not (yet) speak MCP. Maintain strict parity with the MCP tool (Spec 07).

## Bounded context

**Does**
- Serve a small HTTP API on the same container, same orchestrator instance, same auth.
- Provide one POST endpoint mirroring the `agentic_search` MCP tool.
- Provide health + status endpoints.
- Stream `AnswerEvent` over Server-Sent Events (SSE) for callers that want progress.
- Enforce bearer auth, request size limits, and per-IP rate limits.

**Does NOT**
- Expose sub-component endpoints (`/search`, `/crawl`, `/rerank`) — these would re-introduce the attack surface the MCP tool surface intentionally closes.
- Implement OpenAI-compatible chat completion (this service is not a chat completion endpoint).
- Serve a UI.

## Endpoints

```
POST /v1/search                 # JSON body (input schema below); JSON response (output schema below)
POST /v1/search/stream          # JSON body; text/event-stream response of AnswerEvent
GET  /v1/health                 # 200 OK with {status:"ok"} when ready; 503 otherwise
GET  /v1/status                 # parity with nexus://status MCP resource
```

No other routes. `/docs` and `/redoc` (FastAPI defaults) DISABLED in production.

## Schemas

Input schema matches Spec 07 `agentic_search` input (same field names, same constraints).

Output schema matches Spec 07 `agentic_search` output (same shape as `AnswerEnvelope`).

`AnswerEvent` SSE format:
```
event: <stage>
data: <json payload>
id: <monotonic-int>

```

The terminal event is either `event: answer` or `event: error`. Clients close the stream when they observe one of those.

## Auth

- `Authorization: Bearer <token>` header.
- Token loaded from `NEXUS_HTTP_TOKEN` env var (may be same as `NEXUS_MCP_TOKEN`; if both env vars set to the same value, OK).
- Missing or wrong token → 401 with body `{"error":"unauthorized"}`. No www-authenticate prompt.

## Size and rate limits

- Body cap: 4 KB enforced by middleware. Over → 413.
- Per-token rate limit: 30 requests/minute (token bucket), 5 concurrent in-flight. Over → 429.
- Per-IP rate limit: 60 requests/minute total (defense even if a token leaks).
- SSE max stream duration matches orchestrator wall clock (60s).

## Binding

- Listens on the Docker bridge `agentic-net` interface only (resolved via `BIND_HOST` env, default container-internal).
- Container does NOT publish port to host. Adjacent containers reach via Docker DNS `http://nexus-search:8185`.
- TLS terminated upstream if LAN exposure is enabled later (Spec 12); HTTP plaintext acceptable on the internal Docker bridge.

## Invariants

- Endpoints `/v1/search` and `/v1/search/stream` invoke the same `orchestrator.search` code path. Their JSON output shapes are byte-identical for the same inputs (modulo timestamps).
- 404 for any path not in the allowlist (no implicit FastAPI default routes other than what's defined).
- 5xx responses contain `{"error":"<reason>"}` only — no traceback, no internal path, no env var name.
- CORS DISABLED by default. Enabling requires explicit env var and an allowlist of origins.

## Failure modes

| Failure | Status | Body |
|---|---|---|
| Auth missing/invalid | 401 | `{"error":"unauthorized"}` |
| Body too large | 413 | `{"error":"body_too_large"}` |
| Body fails schema | 422 | `{"error":"invalid_input","field":"<name>"}` |
| Rate limit hit | 429 | `{"error":"rate_limited","retry_after_s":<n>}` |
| Orchestrator `SearchUnavailable` | 503 | `{"error":"search_unavailable","retriable":true}` |
| Orchestrator `LLMUnavailable` | 503 | `{"error":"llm_unavailable","retriable":true}` |
| `BudgetExceeded` | 503 | `{"error":"budget_exhausted","retriable":false}` |
| Wall-clock timeout | 504 | `{"error":"timeout","retriable":true}` |
| Internal exception | 500 | `{"error":"internal"}` (full details only in server log) |

For SSE responses, an `error` event is sent with the same JSON body before closing.

## Security requirements

- See Spec 10 §Transport hardening.
- `/v1/status` does NOT echo `daily_cost_usd` if `STATUS_REVEAL_COST=false` (default true on Docker-only deploy; flip if exposed beyond LAN).
- Server header set to `nexus` (no version disclosure).
- Connection: keep-alive timeouts ≤ 75s, request line size ≤ 8 KB, header total ≤ 16 KB.

## Telemetry contract

Span `http.request` per request, parent of `orchestrator.search` if applicable.
- Attributes: `route`, `method`, `status_code`, `latency_ms`, `body_bytes`, `caller_id_hash` (sha256(token)[:8]), `client_ip_hash` (sha256(ip)[:8]).

Metrics
- `http_requests_total{route,status}` counter.
- `http_request_latency_ms{route}` histogram.
- `http_unauthorized_total` counter.
- `http_rate_limited_total` counter.

Access logs structured JSON. Never log token plaintext.

## Out of scope / deferred

- WebSocket transport (SSE covers streaming).
- gRPC.
- OpenAPI auto-doc exposure (intentionally disabled).
- mTLS (deferred until LAN exposure scenario).

## Open questions

- Whether to emit a `request_id` header on every response (`X-Request-Id`) for caller-side log correlation. Lean yes.
- Whether `/v1/search` (non-streaming) should still internally use the streaming pipeline and aggregate (simpler) or run an aggregated-only path (faster for small responses). Default: streaming + aggregate.
