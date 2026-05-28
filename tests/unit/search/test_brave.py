"""Tests for the Brave Search provider (Spec 01)."""

from __future__ import annotations

import httpx
import pytest

from nexus.search import SearchRequest, SearchUnavailable
from nexus.search.brave import BraveProvider


def _transport(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok_payload() -> dict:
    return {
        "web": {
            "results": [
                {
                    "url": "https://example.com/a?utm_source=x",
                    "title": "A",
                    "description": "first",
                    "page_age": "2026-01-02T00:00:00Z",
                },
                {"url": "https://example.com/b", "title": "B", "description": "second"},
                # duplicate after canonicalization (tracking param stripped)
                {"url": "https://example.com/a", "title": "A dup", "description": "dup"},
            ]
        }
    }


# ---------- enabled / disabled ----------


async def test_disabled_without_api_key() -> None:
    provider = BraveProvider(api_key="")
    assert provider.enabled is False
    with pytest.raises(SearchUnavailable, match="api key"):
        await provider.search(SearchRequest(query="hello"))


def test_enabled_with_api_key() -> None:
    assert BraveProvider(api_key="k").enabled is True


# ---------- happy path ----------


async def test_search_parses_and_dedupes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Subscription-Token"] == "key"
        assert request.url.params["q"] == "hello"
        return httpx.Response(200, json=_ok_payload())

    provider = BraveProvider(api_key="key", client=_transport(handler))
    resp = await provider.search(SearchRequest(query="hello"))
    assert resp.provider == "brave"
    # Three raw results, one is a canonical duplicate → two unique.
    assert len(resp.results) == 2
    assert resp.results[0].url == "https://example.com/a"
    assert resp.results[0].engine == "brave"
    assert resp.results[0].published_at is not None
    assert resp.results[0].rank == 0
    assert resp.results[1].rank == 1


async def test_freshness_mapped_to_brave_param() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["freshness"] = request.url.params.get("freshness", "")
        return httpx.Response(200, json={"web": {"results": []}})

    provider = BraveProvider(api_key="key", client=_transport(handler))
    await provider.search(SearchRequest(query="x", freshness="week"))
    assert seen["freshness"] == "pw"


async def test_count_clamped_to_20() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["count"] = request.url.params.get("count", "")
        return httpx.Response(200, json={"web": {"results": []}})

    provider = BraveProvider(api_key="key", client=_transport(handler))
    await provider.search(SearchRequest(query="x", max_results=50))
    assert seen["count"] == "20"


async def test_zero_results_is_not_an_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"web": {"results": []}})

    provider = BraveProvider(api_key="key", client=_transport(handler))
    resp = await provider.search(SearchRequest(query="x"))
    assert resp.results == []


# ---------- failure modes ----------


async def test_4xx_raises_search_unavailable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    provider = BraveProvider(api_key="key", client=_transport(handler))
    with pytest.raises(SearchUnavailable, match="http 401"):
        await provider.search(SearchRequest(query="x"))


async def test_5xx_retried_once_then_raises() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    provider = BraveProvider(api_key="key", client=_transport(handler))
    with pytest.raises(SearchUnavailable):
        await provider.search(SearchRequest(query="x"))
    assert calls["n"] == 2  # initial + one retry


async def test_5xx_then_success_recovers() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500)
        return httpx.Response(200, json={"web": {"results": []}})

    provider = BraveProvider(api_key="key", client=_transport(handler))
    resp = await provider.search(SearchRequest(query="x"))
    assert resp.results == []
    assert calls["n"] == 2


async def test_transport_error_raises_search_unavailable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    provider = BraveProvider(api_key="key", client=_transport(handler))
    with pytest.raises(SearchUnavailable, match="transport"):
        await provider.search(SearchRequest(query="x"))


# ---------- secret hygiene ----------


async def test_api_key_not_in_response_object() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_payload())

    provider = BraveProvider(api_key="super-secret-key", client=_transport(handler))
    resp = await provider.search(SearchRequest(query="x"))
    assert "super-secret-key" not in resp.model_dump_json()
