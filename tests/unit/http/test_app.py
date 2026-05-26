from __future__ import annotations

from dataclasses import dataclass

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
    def __init__(self, events: list[AnswerEvent]) -> None:
        self.events = events
        self.calls = []

    async def search(self, req):
        self.calls.append(req)
        for event in self.events:
            yield event


def make_answer_event() -> AnswerEvent:
    return AnswerEvent(
        stage="answer",
        payload={
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
        },
    )


def make_client(events: list[AnswerEvent], *, token: str = "secret", body_limit: int = 4096) -> tuple[TestClient, FakeOrchestrator]:
    orchestrator = FakeOrchestrator(events)
    app = create_app(
        orchestrator=orchestrator,
        llm_config_roles={"synthesis": FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)},
        config=HTTPConfig(token=token, body_limit_bytes=body_limit),
    )
    return TestClient(app), orchestrator


def test_http_search_requires_bearer_auth() -> None:
    client, _ = make_client([make_answer_event()])

    response = client.post("/v1/search", json={"query": "python"})

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_http_search_returns_terminal_answer() -> None:
    client, orchestrator = make_client(
        [
            AnswerEvent(stage="accepted", payload={"request_id": "1", "normalized_query": "python"}),
            AnswerEvent(stage="searched", payload={"result_count": 1, "provider": "brave"}),
            make_answer_event(),
        ]
    )

    response = client.post("/v1/search", headers={"Authorization": "Bearer secret"}, json={"query": "python"})

    assert response.status_code == 200
    assert response.json()["answer_text"] == "ok"
    assert len(orchestrator.calls) == 1


def test_http_stream_returns_sse_events() -> None:
    client, _ = make_client(
        [
            AnswerEvent(stage="accepted", payload={"request_id": "1", "normalized_query": "python"}),
            AnswerEvent(stage="searched", payload={"result_count": 1, "provider": "brave"}),
            make_answer_event(),
        ]
    )

    with client.stream("POST", "/v1/search/stream", headers={"Authorization": "Bearer secret"}, json={"query": "python"}) as response:
        body = b"".join(response.iter_bytes()).decode("utf-8")

    assert response.status_code == 200
    assert "event: accepted" in body
    assert "event: searched" in body
    assert "event: answer" in body


def test_http_status_and_health_are_exposed() -> None:
    client, _ = make_client([make_answer_event()])

    health = client.get("/v1/health")
    status = client.get("/v1/status", headers={"Authorization": "Bearer secret"})

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert status.status_code == 200
    assert "version" in status.json()


def test_http_unknown_route_returns_404() -> None:
    client, _ = make_client([make_answer_event()])

    response = client.get("/docs")

    assert response.status_code == 404


def test_http_body_too_large_returns_413() -> None:
    client, _ = make_client([make_answer_event()], body_limit=10)

    response = client.post("/v1/search", headers={"Authorization": "Bearer secret"}, json={"query": "python"})

    assert response.status_code == 413
    assert response.json() == {"error": "body_too_large"}


def test_http_invalid_input_returns_422() -> None:
    client, _ = make_client([make_answer_event()])

    response = client.post("/v1/search", headers={"Authorization": "Bearer secret"}, json={"query": " "})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_input"


def test_http_rate_limit_returns_429() -> None:
    client, _ = make_client([make_answer_event()])
    app_state = client.app.state
    app_state.rate_limiter.token_requests["secret"] = 30

    response = client.post("/v1/search", headers={"Authorization": "Bearer secret"}, json={"query": "python"})

    assert response.status_code == 429
    assert response.json()["error"] == "rate_limited"
