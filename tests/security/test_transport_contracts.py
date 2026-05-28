from __future__ import annotations

import asyncio
import socket
import threading
import time
from dataclasses import dataclass

import httpx
import uvicorn
from fastapi.testclient import TestClient

from nexus.http import HTTPConfig, create_app
from nexus.orchestrator import AnswerEvent


@dataclass
class FakeRoleConfig:
    primary: str
    fallback: list[str]
    max_input_tokens: int
    max_output_tokens: int


class FakeOrchestrator:
    async def search(self, req):
        yield AnswerEvent(stage="answer", payload=_answer_payload())


class StreamingOrchestrator:
    async def search(self, req):
        yield AnswerEvent(
            stage="accepted", payload={"request_id": "1", "normalized_query": req.query}
        )
        await asyncio.sleep(0.2)
        yield AnswerEvent(stage="searched", payload={"result_count": 1, "provider": "brave"})
        await asyncio.sleep(0.2)
        yield AnswerEvent(stage="answer", payload=_answer_payload())


def _answer_payload() -> dict:
    return {
        "answer_text": "ok",
        "citations": [],
        "rejected_citations": [],
        "documents": [],
        "cost_usd": 0.0,
        "tokens_in": 0,
        "tokens_out": 0,
        "latency_ms": 1,
        "degraded": False,
        "ungrounded": True,
    }


def _make_client(orchestrator) -> TestClient:
    app = create_app(
        orchestrator=orchestrator,
        llm_config_roles={"synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)},
        config=HTTPConfig(token="secret"),
    )
    return TestClient(app)


def test_http_search_rejects_malformed_json() -> None:
    client = _make_client(FakeOrchestrator())

    response = client.post(
        "/v1/search",
        headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
        content="{",
    )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_input"


def test_http_stream_rejects_malformed_json() -> None:
    client = _make_client(FakeOrchestrator())

    response = client.post(
        "/v1/search/stream",
        headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
        content="{",
    )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_input"


def test_http_stream_emits_incremental_events() -> None:
    app = create_app(
        orchestrator=StreamingOrchestrator(),
        llm_config_roles={"synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)},
        config=HTTPConfig(token="secret"),
    )

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

    first_chunk_at = None
    chunk_count = 0
    started_at = time.perf_counter()
    try:
        with httpx.stream(
            "POST",
            f"http://127.0.0.1:{port}/v1/search/stream",
            headers={"Authorization": "Bearer secret"},
            json={"query": "python"},
            timeout=5.0,
        ) as response:
            for chunk in response.iter_raw():
                if not chunk:
                    continue
                chunk_count += 1
                if first_chunk_at is None:
                    first_chunk_at = time.perf_counter() - started_at
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    assert first_chunk_at is not None
    assert first_chunk_at < 0.35
    assert chunk_count >= 2
