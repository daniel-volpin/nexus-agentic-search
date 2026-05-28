from __future__ import annotations

import json
from typing import Any

from .resources import read_roles, read_status
from .schemas import truncate_answer_payload, validate_input, validate_output
from .types import MCPConfig, StatusState


class MCPTransport:
    def __init__(self, *, orchestrator, llm_config_roles: dict[str, object], config: MCPConfig) -> None:
        self._orchestrator = orchestrator
        self._roles = llm_config_roles
        self._config = config
        self._state = StatusState()
        self.progress_events: list[dict] = []

    def tool_definitions(self) -> list[dict]:
        return [
            {
                "name": "agentic_search",
                "description": "Run an agentic web search and return a citation-grounded answer.",
            }
        ]

    def validate_token(self, header: str | None) -> bool:
        if not header:
            return False
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        return header[len(prefix) :] == self._config.token

    async def handle_call(self, payload: dict, *, ctx=None) -> dict:
        try:
            request = validate_input(payload, config=self._config)
        except Exception as exc:
            return {"error": "invalid_input"}

        self._state.requests_today += 1
        self.progress_events = []
        progress_index = 0
        async for event in self._orchestrator.search(request):
            if event.stage == "answer":
                if not validate_output(event.payload):
                    return {"error": "internal", "retriable": False}
                out = truncate_answer_payload(event.payload, config=self._config)
                self._state.daily_cost_usd += float(out.get("cost_usd", 0.0))
                return out
            if event.stage == "error":
                return {"error": event.payload["reason"], "retriable": event.payload["retriable"]}
            progress_payload = {"stage": event.stage, "payload": event.payload}
            self.progress_events.append(progress_payload)
            if ctx is not None:
                progress_index += 1
                await ctx.report_progress(progress=float(progress_index), message=json.dumps(progress_payload, default=str))

        return {"error": "internal", "retriable": False}

    def read_resource(self, uri: str) -> dict:
        if uri == "nexus://status":
            return read_status(config=self._config, state=self._state)
        if uri == "nexus://config/roles":
            return read_roles(roles=self._roles)
        raise KeyError(uri)


def create_streamable_http_app(*, transport: MCPTransport):
    try:
        from fastmcp import Context, FastMCP
        from fastmcp.exceptions import ToolError
        from fastmcp.server.http import create_streamable_http_app as create_http_app
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("fastmcp is required to build the MCP server app") from exc

    globals()["Context"] = Context

    mcp = FastMCP("nexus-search")

    @mcp.tool(name="agentic_search", description="Run an agentic web search and return a citation-grounded answer.")
    async def agentic_search(query: str, freshness: str = "any", max_results: int = 20, lang: str | None = None, country: str | None = None, ctx: Context | None = None):
        result = await transport.handle_call(
            {
                "query": query,
                "freshness": freshness,
                "max_results": max_results,
                "lang": lang,
                "country": country,
            },
            ctx=ctx,
        )
        if "error" in result:
            raise ToolError(json.dumps(result, separators=(",", ":")))
        return result
    agentic_search.output_schema = {
        "type": "object",
        "required": ["answer_text", "citations", "rejected_citations", "documents", "cost_usd", "tokens_in", "tokens_out", "latency_ms", "degraded", "ungrounded"],
        "properties": {
            "answer_text": {"type": "string"},
            "citations": {"type": "array"},
            "rejected_citations": {"type": "array"},
            "documents": {"type": "array"},
            "cost_usd": {"type": "number"},
            "tokens_in": {"type": "integer"},
            "tokens_out": {"type": "integer"},
            "latency_ms": {"type": "integer"},
            "degraded": {"type": "boolean"},
            "ungrounded": {"type": "boolean"},
        },
    }

    @mcp.resource("nexus://status")
    def status_resource() -> dict:
        return transport.read_resource("nexus://status")

    @mcp.resource("nexus://config/roles")
    def roles_resource() -> dict:
        return transport.read_resource("nexus://config/roles")

    return create_http_app(server=mcp, streamable_http_path="/mcp", auth=create_bearer_auth(transport._config.token))


def create_bearer_auth(token: str):
    try:
        from fastmcp.server.auth import AccessToken, TokenVerifier
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("fastmcp is required to build MCP auth") from exc

    class BearerTokenVerifier(TokenVerifier):
        async def verify_token(self, provided_token: str):
            if provided_token != token:
                return None
            return AccessToken(token=provided_token, client_id="nexus-mcp-client", scopes=[])

    return BearerTokenVerifier()
