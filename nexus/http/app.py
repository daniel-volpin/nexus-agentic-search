from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError as PydanticValidationError

from nexus.mcp.resources import read_status
from nexus.mcp.types import StatusState
from nexus.search.types import SearchRequest

from .auth import require_bearer_token
from .limits import RateLimiter
from .types import HTTPConfig


def create_app(*, orchestrator, llm_config_roles: dict[str, object], config: HTTPConfig) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.rate_limiter = RateLimiter()
    app.state.status_state = StatusState()

    @app.middleware("http")
    async def body_limit_and_server_header(request: Request, call_next):
        body = await request.body()
        if request.method == "POST" and len(body) > config.body_limit_bytes:
            response = JSONResponse(status_code=413, content={"error": "body_too_large"})
            response.headers["Server"] = "nexus"
            return response
        request.state.cached_body = body
        response = await call_next(request)
        response.headers["Server"] = "nexus"
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(
                status_code=exc.status_code, content=detail, headers={"Server": "nexus"}
            )
        return JSONResponse(
            status_code=exc.status_code, content={"error": "internal"}, headers={"Server": "nexus"}
        )

    def _authorize(request: Request) -> str:
        token = require_bearer_token(config.token, request.headers.get("Authorization"))
        client_ip = request.client.host if request.client else "unknown"
        allowed = app.state.rate_limiter.allow(
            token=token,
            client_ip=client_ip,
            token_limit=config.token_rate_limit_per_minute,
            ip_limit=config.ip_rate_limit_per_minute,
        )
        if not allowed:
            raise HTTPException(
                status_code=429, detail={"error": "rate_limited", "retry_after_s": 60}
            )
        return token

    def _parse_payload(request: Request) -> dict:
        try:
            return json.loads(request.state.cached_body.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail={"error": "invalid_input", "field": "body"}) from exc

    def _validate_request(payload: dict) -> SearchRequest:
        try:
            return SearchRequest.model_validate(payload)
        except PydanticValidationError as exc:
            field = ".".join(str(part) for part in exc.errors()[0]["loc"])
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_input", "field": field},
            ) from exc

    async def _run_orchestrator(payload: dict):
        request = _validate_request(payload)
        events = []
        async for event in orchestrator.search(request):
            events.append(event)
        return events

    async def _stream_orchestrator(payload: dict):
        request = _validate_request(payload)
        async for event in orchestrator.search(request):
            yield event

    @app.post("/v1/search")
    async def search(request: Request):
        token = _authorize(request)
        if not app.state.rate_limiter.acquire_concurrency(
            token=token, limit=config.max_concurrent_per_token
        ):
            raise HTTPException(
                status_code=429, detail={"error": "rate_limited", "retry_after_s": 60}
            )
        try:
            payload = _parse_payload(request)
            events = await _run_orchestrator(payload)
            terminal = events[-1]
            if terminal.stage == "answer":
                return JSONResponse(status_code=200, content=terminal.payload)
            if terminal.payload["reason"] == "timeout":
                return JSONResponse(
                    status_code=504, content={"error": "timeout", "retriable": True}
                )
            if terminal.payload["reason"] in {"search_unavailable", "llm_unavailable"}:
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": terminal.payload["reason"],
                        "retriable": terminal.payload["retriable"],
                    },
                )
            if terminal.payload["reason"] == "budget_exhausted":
                return JSONResponse(
                    status_code=503, content={"error": "budget_exhausted", "retriable": False}
                )
            return JSONResponse(status_code=500, content={"error": "internal"})
        finally:
            app.state.rate_limiter.release_concurrency(token=token)

    @app.post("/v1/search/stream")
    async def search_stream(request: Request):
        token = _authorize(request)
        if not app.state.rate_limiter.acquire_concurrency(
            token=token, limit=config.max_concurrent_per_token
        ):
            raise HTTPException(
                status_code=429, detail={"error": "rate_limited", "retry_after_s": 60}
            )
        payload = _parse_payload(request)

        async def generate():
            idx = 0
            try:
                async for event in _stream_orchestrator(payload):
                    idx += 1
                    yield f"event: {event.stage}\nid: {idx}\ndata: {json.dumps(event.payload, default=str)}\n\n"
            finally:
                app.state.rate_limiter.release_concurrency(token=token)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/v1/health")
    async def health():
        return JSONResponse(status_code=200, content={"status": "ok"})

    @app.get("/v1/status")
    async def status(request: Request):
        _authorize(request)
        return JSONResponse(
            status_code=200,
            content=read_status(
                config=type(
                    "StatusConfig",
                    (),
                    {"version": config.version, "reveal_cost": config.reveal_cost},
                )(),
                state=app.state.status_state,
            ),
        )

    return app
