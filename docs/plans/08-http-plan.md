# Plan 08 — HTTP Transport

> Spec: [`docs/specs/08-http.md`](../specs/08-http.md) · spec wins on disagreement.

## Module layout

```
nexus/transport/
├── http.py             # FastAPI app
├── auth.py             # (shared with MCP, Plan 07)
└── middleware.py       # request_id, size cap, rate limit, error mapping

tests/unit/transport/
├── test_http_routes.py
├── test_http_auth.py
├── test_http_rate_limit.py
└── test_http_sse.py
tests/security/test_http_surface.py
tests/integration/test_http_e2e.py
```

## Public symbols

```python
# nexus/transport/http.py
def build_http_app(orch: Orchestrator, config: TransportConfig) -> FastAPI: ...
async def run_http(orch: Orchestrator, config: TransportConfig) -> None: ...

# nexus/transport/middleware.py
class RequestIdMiddleware: ...
class BodySizeLimitMiddleware: ...
class RateLimitMiddleware: ...
class ErrorMapperMiddleware: ...
```

## External dependencies

| Package | Why |
|---|---|
| `fastapi` | Web framework. |
| `uvicorn[standard]` | ASGI server. |
| `slowapi` or in-house token bucket | Rate limiting. Lean in-house: 30 lines of asyncio, no deps. |
| `pydantic` v2 | Request/response models. |
| `sse-starlette` | Lightweight SSE responses. |

## Build order

1. **Models** — Pydantic models for `SearchInput` and `SearchOutput`, exact parity with MCP schema (Plan 07). Field validators match Spec 01 caps.
2. **`auth.py`** — already built in Plan 07; HTTP uses the same `BearerAuth`. Read `Authorization: Bearer <token>` header; mismatch → 401. Token in query string is REJECTED with 401 (asserted by test).
3. **Middleware stack** (order applied: outermost first):
   - `RequestIdMiddleware`: generate uuid4 per request, store in contextvar, attach as `X-Request-Id` response header.
   - `BodySizeLimitMiddleware`: reject > 4 KB with 413.
   - `RateLimitMiddleware`: per-token bucket (30/min, 5 concurrent) + per-IP bucket (60/min). Buckets in-memory, asyncio.Lock-guarded.
   - `ErrorMapperMiddleware`: catches orchestrator exceptions, maps to status codes from Spec 08 §Failure modes; never leaks tracebacks.
4. **Routes**:
   - `POST /v1/search` → builds `SearchRequest`, calls `orch.aggregate()`, returns `AnswerEnvelope` JSON.
   - `POST /v1/search/stream` → returns `EventSourceResponse` from `sse-starlette`. Async generator wraps `orch.stream()`, emits `event: <stage>\ndata: <json>\nid: <n>\n\n`.
   - `GET /v1/health` → returns `{"status":"ok"}` 200 if orchestrator ready, else 503.
   - `GET /v1/status` → parity with `nexus://status` resource.
5. **Disable defaults** — set `docs_url=None, redoc_url=None, openapi_url=None` on `FastAPI(...)`. `default_response_class=ORJSONResponse`. Custom `server` header via response-middleware (`Server: nexus`).
6. **CORS** — by default, `CORSMiddleware` is NOT installed. If `cors_enabled=True`, install with an explicit `allow_origins` list (no `*`).
7. **Run** — uvicorn programmatic invocation, bound to `config.bind_host:config.bind_port` (same port as MCP — both transports share port 8185 via path routing).

## Path routing decision

MCP server and FastAPI both want to live in the same process. Two approaches:

- **Different ports**: simplest. MCP on 8185, HTTP on 8186. Requires both ports inside the container; adjacent caller uses one or the other by URL. Recommended.
- **Same port, path-prefix**: complex; FastMCP v2 mounted under `/mcp/` and FastAPI under `/v1/`. Possible but adds debugging surface.

Plan picks **different ports inside the container**:
- MCP: `8185` (default)
- HTTP: `8186`
- Compose only exposes them within the agentic-net network; nothing published.

## SSE response shape

```
event: searched
data: {"result_count": 18, "provider": "brave"}
id: 3

event: page_ready
data: {"url": "...", "content_hash": "...", "status": "ok", "render_ms": 1840}
id: 4

event: answer
data: {<AnswerEnvelope JSON>}
id: 9

```

Closing: server emits a final newline pair after the terminal event; clients close on receipt of `answer` or `error` event.

## Test plan (mapping to spec invariants)

| Spec invariant | Test |
|---|---|
| Same code path for `/v1/search` and `/v1/search/stream` | `test_http_routes::test_aggregated_matches_streamed` |
| 404 for any non-allowlisted path | `test_http_routes::test_404_strict` |
| `/docs` and `/redoc` 404 in prod | `test_http_surface::test_no_openapi_doc` |
| Auth missing → 401 | `test_http_auth::test_missing_token_401` |
| Auth wrong → 401 | `test_http_auth::test_bad_token_401` |
| Token in query string ignored | `test_http_auth::test_token_in_query_ignored` |
| Body > 4 KB → 413 | `test_http_routes::test_body_size_limit` |
| Rate limit | `test_http_rate_limit.py` |
| Error responses contain no traceback | `test_http_surface::test_error_no_trace` |
| `Server: nexus` header | `test_http_routes::test_server_header` |
| `X-Request-Id` on every response | `test_http_routes::test_request_id_header` |
| SSE terminal event closes cleanly | `test_http_sse.py` |

## Adversarial tests required

`tests/security/test_http_surface.py`:
- Path traversal (`/../etc/passwd`) → 404 (FastAPI shouldn't be vulnerable; assert anyway).
- Header injection in `Authorization`: CR/LF chars rejected.
- Slow-loris request: incomplete body within 30s → 408/closed.
- Large header set (> 16 KB total) → 431.
- CORS preflight when CORS disabled → no `Access-Control-*` headers returned.

## Risks & mitigations

- **FastAPI default routes**: explicitly disabled. Test asserts.
- **uvicorn config drift**: pin `uvicorn[standard]`; ensure `proxy_headers=False` since we have no proxy in front initially.
- **SSE buffering by intermediaries**: not relevant on Docker bridge directly; document that if a reverse proxy is added (Caddy/nginx), `X-Accel-Buffering: no` may be required.
- **Rate-limiter resets on restart**: acceptable; documented.

## Done criteria
- [ ] All unit + security + integration tests pass.
- [ ] `/v1/search` and `/v1/search/stream` return identical content for the same query (modulo timestamps), verified by `test_aggregated_matches_streamed`.
- [ ] Integration test from adjacent container (compose under test): aggregated POST returns valid JSON; SSE POST emits stage sequence ending with `answer` event.
- [ ] `mypy --strict` clean.
