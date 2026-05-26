from __future__ import annotations

import asyncio
from dataclasses import dataclass
import socket
import threading
import time

from fastmcp import Client
import pytest
from starlette.testclient import TestClient
import uvicorn

from nexus.citations import Citation
from nexus.mcp import MCPConfig, MCPTransport, create_bearer_auth, create_streamable_http_app
from nexus.orchestrator import AnswerEvent


@dataclass
class FakeRoleConfig:
    primary: str
    fallback: list[str]
    max_input_tokens: int
    max_output_tokens: int


class FakeOrchestrator:
    def __init__(self, events: list[AnswerEvent]) -> None:
        self.events = events
        self.calls = []

    async def search(self, req):
        self.calls.append(req)
        for event in self.events:
            yield event


def make_answer_event(*, degraded: bool = False) -> AnswerEvent:
    citation = Citation(
        url="https://example.com",
        content_hash="doc-1",
        byte_start=0,
        byte_end=20,
        quote="A sufficiently long quote",
        claim_id="claim-1",
    )
    return AnswerEvent(
        stage="answer",
        payload={
            "answer_text": "A" * 20,
            "citations": [citation],
            "rejected_citations": [],
            "documents": [{"url": "https://example.com", "content_hash": "doc-1"}],
            "cost_usd": 0.1,
            "tokens_in": 10,
            "tokens_out": 5,
            "latency_ms": 100,
            "degraded": degraded,
            "ungrounded": False,
        },
    )


def test_server_definition_exposes_exactly_one_tool() -> None:
    transport = MCPTransport(orchestrator=FakeOrchestrator([]), llm_config_roles={"synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)}, config=MCPConfig(token="secret"))

    tool_defs = transport.tool_definitions()

    assert [tool["name"] for tool in tool_defs] == ["agentic_search"]


def test_validate_token_rejects_invalid_bearer() -> None:
    transport = MCPTransport(orchestrator=FakeOrchestrator([]), llm_config_roles={"synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)}, config=MCPConfig(token="secret"))

    assert transport.validate_token("Bearer wrong") is False
    assert transport.validate_token("Bearer secret") is True


def test_handle_call_rejects_invalid_input() -> None:
    orchestrator = FakeOrchestrator([])
    transport = MCPTransport(orchestrator=orchestrator, llm_config_roles={"synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)}, config=MCPConfig(token="secret"))

    result = asyncio.run(transport.handle_call({"query": ""}))

    assert result["error"] == "invalid_input"
    assert orchestrator.calls == []


def test_handle_call_maps_progress_and_final_answer() -> None:
    orchestrator = FakeOrchestrator(
        [
            AnswerEvent(stage="accepted", payload={"request_id": "1", "normalized_query": "python"}),
            AnswerEvent(stage="searched", payload={"result_count": 1, "provider": "brave"}),
            make_answer_event(),
        ]
    )
    transport = MCPTransport(orchestrator=orchestrator, llm_config_roles={"synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)}, config=MCPConfig(token="secret"))

    result = asyncio.run(transport.handle_call({"query": "python"}))

    assert result["answer_text"] == "A" * 20
    assert [item["stage"] for item in transport.progress_events] == ["accepted", "searched"]


def test_handle_call_reports_progress_to_context() -> None:
    class FakeContext:
        def __init__(self) -> None:
            self.progress = []

        async def report_progress(self, progress: float, total=None, message=None) -> None:
            self.progress.append({"progress": progress, "message": message})

    orchestrator = FakeOrchestrator(
        [
            AnswerEvent(stage="accepted", payload={"request_id": "1", "normalized_query": "python"}),
            AnswerEvent(stage="searched", payload={"result_count": 1, "provider": "brave"}),
            make_answer_event(),
        ]
    )
    transport = MCPTransport(orchestrator=orchestrator, llm_config_roles={"synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)}, config=MCPConfig(token="secret"))
    ctx = FakeContext()

    result = asyncio.run(transport.handle_call({"query": "python"}, ctx=ctx))

    assert result["answer_text"] == "A" * 20
    assert len(ctx.progress) == 2
    assert '"stage": "accepted"' in ctx.progress[0]["message"]


def test_handle_call_maps_orchestrator_error_without_detail_leak() -> None:
    orchestrator = FakeOrchestrator(
        [
            AnswerEvent(stage="accepted", payload={"request_id": "1", "normalized_query": "python"}),
            AnswerEvent(stage="error", payload={"reason": "llm_unavailable", "retriable": True, "detail": "provider down"}),
        ]
    )
    transport = MCPTransport(orchestrator=orchestrator, llm_config_roles={"synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)}, config=MCPConfig(token="secret"))

    result = asyncio.run(transport.handle_call({"query": "python"}))

    assert result == {"error": "llm_unavailable", "retriable": True}


def test_answer_is_truncated_and_marked_degraded_when_over_cap() -> None:
    answer = make_answer_event()
    answer.payload["answer_text"] = "x" * 17000
    answer.payload["documents"] = [{"url": f"https://example.com/{idx}", "content_hash": f"doc-{idx}"} for idx in range(20)]
    answer.payload["citations"] = [
        Citation(
            url=f"https://example.com/{idx}",
            content_hash=f"doc-{idx}",
            byte_start=0,
            byte_end=20,
            quote="A sufficiently long quote",
            claim_id=f"claim-{idx}",
        )
        for idx in range(40)
    ]
    orchestrator = FakeOrchestrator([answer])
    transport = MCPTransport(orchestrator=orchestrator, llm_config_roles={"synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)}, config=MCPConfig(token="secret"))

    result = asyncio.run(transport.handle_call({"query": "python"}))

    assert len(result["answer_text"]) == 16000
    assert len(result["documents"]) == 16
    assert len(result["citations"]) == 32
    assert result["degraded"] is True


def test_status_resource_and_roles_resource_are_exposed() -> None:
    transport = MCPTransport(
        orchestrator=FakeOrchestrator([]),
        llm_config_roles={
            "synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", ["anthropic/claude-sonnet-4-5-20250929"], 32000, 2000)
        },
        config=MCPConfig(token="secret", version="1.2.3"),
    )

    status = transport.read_resource("nexus://status")
    roles = transport.read_resource("nexus://config/roles")

    assert status["version"] == "1.2.3"
    assert "daily_cost_usd" in status
    assert roles["synthesis"]["primary"] == "openai/gpt-4o-2024-11-20"


def test_missing_token_config_is_rejected() -> None:
    with pytest.raises(ValueError):
        MCPConfig(token="")


def test_bearer_auth_verifier_accepts_matching_token() -> None:
    verifier = create_bearer_auth("secret")

    token = asyncio.run(verifier.verify_token("secret"))

    assert token is not None
    assert token.client_id == "nexus-mcp-client"


def test_bearer_auth_verifier_rejects_non_matching_token() -> None:
    verifier = create_bearer_auth("secret")

    token = asyncio.run(verifier.verify_token("wrong"))

    assert token is None


def test_handle_call_rejects_malformed_answer_payload() -> None:
    orchestrator = FakeOrchestrator(
        [
            AnswerEvent(
                stage="answer",
                payload={"answer_text": "missing required fields"},
            )
        ]
    )
    transport = MCPTransport(orchestrator=orchestrator, llm_config_roles={"synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)}, config=MCPConfig(token="secret"))

    result = asyncio.run(transport.handle_call({"query": "python"}))

    assert result == {"error": "internal", "retriable": False}


def test_live_http_auth_blocks_unauthorized_and_allows_authorized_protocol_handling() -> None:
    orchestrator = FakeOrchestrator([make_answer_event()])
    transport = MCPTransport(orchestrator=orchestrator, llm_config_roles={"synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)}, config=MCPConfig(token="secret"))
    app = create_streamable_http_app(transport=transport)

    with TestClient(app) as client:
        unauthorized = client.get("/mcp")
        authorized = client.get("/mcp", headers={"Authorization": "Bearer secret"})

    assert unauthorized.status_code == 401
    assert authorized.status_code == 406
    assert "text/event-stream" in authorized.text


def test_end_to_end_mcp_tool_call_over_streamable_http() -> None:
    orchestrator = FakeOrchestrator(
        [
            AnswerEvent(stage="accepted", payload={"request_id": "1", "normalized_query": "python"}),
            AnswerEvent(stage="searched", payload={"result_count": 1, "provider": "brave"}),
            make_answer_event(),
        ]
    )
    transport = MCPTransport(
        orchestrator=orchestrator,
        llm_config_roles={"synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)},
        config=MCPConfig(token="secret"),
    )
    app = create_streamable_http_app(transport=transport)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)

    async def run_client() -> tuple[list[str], object, list[tuple[float, object, object]]]:
        progress: list[tuple[float, object, object]] = []

        def handle_progress(progress_value, total=None, message=None, **kwargs) -> None:
            progress.append((progress_value, total, message))

        async with Client(f"http://127.0.0.1:{port}/mcp", auth="secret", progress_handler=handle_progress) as client:
            tools = await client.list_tools()
            result = await client.call_tool("agentic_search", {"query": "python"})
            return [tool.name for tool in tools], result, progress

    try:
        tool_names, result, progress = asyncio.run(run_client())
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    assert tool_names == ["agentic_search"]
    assert result.structured_content["answer_text"] == "A" * 20
    assert len(progress) >= 2
