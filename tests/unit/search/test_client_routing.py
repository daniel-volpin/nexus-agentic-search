"""Tests for DefaultSearchClient routing (Spec 01 §Activation policy)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nexus.search import (
    DefaultSearchClient,
    Result,
    SearchRequest,
    SearchResponse,
    SearchUnavailable,
)


def _result(url: str, rank: int, engine: str = "brave") -> Result:
    return Result(
        url=url,
        title=f"t{rank}",
        snippet="s",
        engine=engine,  # type: ignore[arg-type]
        rank=rank,
        fetched_at=datetime.now(UTC),
    )


def _response(provider: str, urls: list[str], engine: str = "brave") -> SearchResponse:
    return SearchResponse(
        results=[_result(u, i, engine) for i, u in enumerate(urls)],
        provider=provider,
        query_sent="q",
        latency_ms=1,
    )


class _FakeBrave:
    def __init__(self, *, enabled: bool, response=None, error=None) -> None:
        self.enabled = enabled
        self._response = response
        self._error = error
        self.calls = 0

    async def search(self, _req: SearchRequest) -> SearchResponse:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._response


class _FakeSearxng:
    def __init__(self, *, response=None, error=None) -> None:
        self._response = response
        self._error = error
        self.calls = 0

    async def search(self, _req: SearchRequest) -> SearchResponse:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._response


_REQ = SearchRequest(query="q")


# ---------- brave primary ----------


async def test_brave_with_enough_results_does_not_call_searxng() -> None:
    brave = _FakeBrave(
        enabled=True,
        response=_response("brave", ["https://a", "https://b", "https://c"]),
    )
    searxng = _FakeSearxng(response=_response("searxng", []))
    client = DefaultSearchClient(brave=brave, searxng=searxng)
    resp = await client.search(_REQ)
    assert resp.provider == "brave"
    assert len(resp.results) == 3
    assert searxng.calls == 0


# ---------- thin coverage → augment ----------


async def test_thin_brave_results_augmented_by_searxng() -> None:
    brave = _FakeBrave(enabled=True, response=_response("brave", ["https://a"]))
    searxng = _FakeSearxng(
        response=_response("searxng", ["https://a", "https://x", "https://y"], "searxng:google")
    )
    client = DefaultSearchClient(brave=brave, searxng=searxng)
    resp = await client.search(_REQ)
    assert resp.provider == "brave+searxng"
    urls = [r.url for r in resp.results]
    # Brave's "https://a" preserved first, dedup against searxng's "https://a".
    assert urls == ["https://a", "https://x", "https://y"]
    # Ranks are contiguous after merge.
    assert [r.rank for r in resp.results] == [0, 1, 2]


async def test_thin_brave_augment_best_effort_when_searxng_fails() -> None:
    brave = _FakeBrave(enabled=True, response=_response("brave", ["https://a"]))
    searxng = _FakeSearxng(error=SearchUnavailable("tripped"))
    client = DefaultSearchClient(brave=brave, searxng=searxng)
    resp = await client.search(_REQ)
    # Brave's thin results stand; no exception.
    assert [r.url for r in resp.results] == ["https://a"]


# ---------- brave failure → fallback ----------


async def test_brave_unavailable_falls_back_to_searxng() -> None:
    brave = _FakeBrave(enabled=True, error=SearchUnavailable("brave down"))
    searxng = _FakeSearxng(response=_response("searxng", ["https://x"], "searxng:duckduckgo"))
    client = DefaultSearchClient(brave=brave, searxng=searxng)
    resp = await client.search(_REQ)
    assert resp.provider == "searxng"
    assert searxng.calls == 1


# ---------- no brave key → searxng only ----------


async def test_no_brave_key_uses_searxng_directly() -> None:
    brave = _FakeBrave(enabled=False)
    searxng = _FakeSearxng(response=_response("searxng", ["https://x"], "searxng:google"))
    client = DefaultSearchClient(brave=brave, searxng=searxng)
    resp = await client.search(_REQ)
    assert resp.provider == "searxng"
    assert brave.calls == 0


# ---------- everything down → raise, never empty ----------


async def test_both_unavailable_raises_not_empty() -> None:
    brave = _FakeBrave(enabled=True, error=SearchUnavailable("brave down"))
    searxng = _FakeSearxng(error=SearchUnavailable("searxng down"))
    client = DefaultSearchClient(brave=brave, searxng=searxng)
    with pytest.raises(SearchUnavailable):
        await client.search(_REQ)


async def test_no_brave_and_no_searxng_raises() -> None:
    brave = _FakeBrave(enabled=False)
    client = DefaultSearchClient(brave=brave, searxng=None)
    with pytest.raises(SearchUnavailable, match="configured"):
        await client.search(_REQ)


async def test_brave_fails_and_no_searxng_raises_with_context() -> None:
    brave = _FakeBrave(enabled=True, error=SearchUnavailable("brave 503"))
    client = DefaultSearchClient(brave=brave, searxng=None)
    with pytest.raises(SearchUnavailable, match="brave unavailable"):
        await client.search(_REQ)
