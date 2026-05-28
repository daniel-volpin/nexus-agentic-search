"""Adversarial tests for the HTTP transport surface (Spec 10 / Spec 08).

These tests complement ``tests/unit/http/test_app.py`` with explicit
adversarial scenarios: doc routes, header injection, error-body leakage,
server-header fingerprinting.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from nexus.http import HTTPConfig, create_app
from nexus.orchestrator import AnswerEvent

pytestmark = pytest.mark.security


@dataclass
class _FakeRoleConfig:
    primary: str
    fallback: list[str]
    max_input_tokens: int
    max_output_tokens: int


class _StaticOrchestrator:
    def __init__(self, events: list[AnswerEvent]) -> None:
        self.events = events

    async def search(self, _req):
        for event in self.events:
            yield event


_TOKEN = "t" * 32


def _answer_event() -> AnswerEvent:
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


def _make_client() -> TestClient:
    orchestrator = _StaticOrchestrator([_answer_event()])
    app = create_app(
        orchestrator=orchestrator,
        llm_config_roles={
            "synthesis": _FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)
        },
        config=HTTPConfig(token=_TOKEN),
    )
    return TestClient(app)


# ---------- documentation routes intentionally disabled ----------


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_documentation_routes_return_404(path: str) -> None:
    """Spec 08: `/docs`, `/redoc`, `/openapi.json` MUST be disabled in
    production. Default FastAPI exposes them; create_app() must turn
    them off."""
    client = _make_client()
    response = client.get(path)
    assert response.status_code == 404, f"{path} leaked: {response.status_code}"


# ---------- server header does not reveal framework / version ----------


def test_server_header_is_nexus_not_uvicorn() -> None:
    """Spec 08 §Security: `Server: nexus`, no `X-Powered-By`, no
    FastAPI default banner."""
    client = _make_client()
    response = client.get("/v1/health")
    assert response.headers.get("Server") == "nexus"
    assert "uvicorn" not in (response.headers.get("Server") or "").lower()


def test_no_x_powered_by_header() -> None:
    client = _make_client()
    response = client.get("/v1/health")
    assert "X-Powered-By" not in response.headers


# ---------- error responses never leak traceback or internal paths ----------


def test_error_response_contains_only_error_field() -> None:
    """Spec 08 §Failure modes: error bodies are `{"error": "..."}`
    only — no traceback, no internal path, no env names."""
    client = _make_client()
    response = client.post("/v1/search", json={"query": "x"})
    # No auth → 401 with strict error shape.
    assert response.status_code == 401
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert body["error"] == "unauthorized"


def test_413_body_too_large_minimal_shape() -> None:
    orchestrator = _StaticOrchestrator([_answer_event()])
    app = create_app(
        orchestrator=orchestrator,
        llm_config_roles={
            "synthesis": _FakeRoleConfig("openai/gpt-4o-2024-11-20", [], 32000, 2000)
        },
        config=HTTPConfig(token=_TOKEN, body_limit_bytes=10),
    )
    client = TestClient(app)
    response = client.post(
        "/v1/search", headers={"Authorization": f"Bearer {_TOKEN}"}, json={"query": "x"}
    )
    assert response.status_code == 413
    assert response.json() == {"error": "body_too_large"}


# ---------- authorization header injection / smuggling resistance ----------


def test_authorization_in_query_string_is_ignored() -> None:
    """Spec 08: token MUST be in the Authorization header, never the
    query string. A request with `?Authorization=Bearer …` must still
    be rejected as unauthorized."""
    client = _make_client()
    response = client.post(
        f"/v1/search?Authorization=Bearer%20{_TOKEN}",
        json={"query": "x"},
    )
    assert response.status_code == 401


def test_authorization_must_use_bearer_prefix() -> None:
    """Basic auth / API-Key headers / raw token MUST NOT bypass bearer."""
    client = _make_client()
    for header in (
        _TOKEN,  # raw token, no prefix
        f"Basic {_TOKEN}",
        f"Token {_TOKEN}",
    ):
        response = client.post("/v1/search", headers={"Authorization": header}, json={"query": "x"})
        assert response.status_code == 401, f"prefix {header!r} accepted"


def test_wrong_token_rejected_with_401() -> None:
    client = _make_client()
    response = client.post(
        "/v1/search", headers={"Authorization": "Bearer wrong"}, json={"query": "x"}
    )
    assert response.status_code == 401


# ---------- internal sub-component endpoints intentionally absent ----------


@pytest.mark.parametrize(
    "path",
    [
        "/v1/crawl",
        "/v1/search/raw",
        "/v1/llm/complete",
        "/v1/rerank",
        "/internal",
        "/admin",
    ],
)
def test_no_internal_sub_component_endpoints(path: str) -> None:
    """If any of these existed, the calling LLM (or an attacker on the
    Docker bridge) could drive a sub-component directly, bypassing the
    orchestrator's safety perimeter."""
    client = _make_client()
    response = client.post(path, headers={"Authorization": f"Bearer {_TOKEN}"}, json={})
    assert response.status_code in {404, 405}


# ---------- health unauthenticated (per Spec 08) ----------


def test_health_does_not_require_auth() -> None:
    """Health probe is the only unauthenticated route — needed by
    Docker / k8s liveness."""
    client = _make_client()
    response = client.get("/v1/health")
    assert response.status_code == 200
