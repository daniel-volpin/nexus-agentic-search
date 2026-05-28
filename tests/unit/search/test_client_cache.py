"""Cache integration for DefaultSearchClient (Spec 09 wiring)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus.cache import DiskCacheBackend
from nexus.search import (
    DefaultSearchClient,
    Result,
    SearchRequest,
    SearchResponse,
    SearchUnavailable,
)


def _response(urls: list[str]) -> SearchResponse:
    return SearchResponse(
        results=[
            Result(
                url=u,
                title="t",
                snippet="s",
                engine="brave",
                rank=i,
                fetched_at=datetime.now(UTC),
            )
            for i, u in enumerate(urls)
        ],
        provider="brave",
        query_sent="q",
        latency_ms=1,
    )


class _CountingBrave:
    enabled = True

    def __init__(self, response: SearchResponse) -> None:
        self._response = response
        self.calls = 0

    async def search(self, _req: SearchRequest) -> SearchResponse:
        self.calls += 1
        return self._response


@pytest.fixture
def cache(tmp_path: Path) -> DiskCacheBackend:
    return DiskCacheBackend(
        root=tmp_path, namespace="search", version=1, ttl_default_s=3600, size_limit_bytes=1 << 20
    )


async def test_second_identical_query_served_from_cache(cache: DiskCacheBackend) -> None:
    brave = _CountingBrave(_response(["https://a", "https://b", "https://c"]))
    client = DefaultSearchClient(brave=brave, cache=cache)
    req = SearchRequest(query="python")

    first = await client.search(req)
    second = await client.search(req)

    assert brave.calls == 1, "second identical query must hit the cache, not the provider"
    assert [r.url for r in first.results] == [r.url for r in second.results]
    assert second.provider == "brave"


async def test_distinct_queries_are_cached_separately(cache: DiskCacheBackend) -> None:
    brave = _CountingBrave(_response(["https://a", "https://b", "https://c"]))
    client = DefaultSearchClient(brave=brave, cache=cache)

    await client.search(SearchRequest(query="python"))
    await client.search(SearchRequest(query="rust"))

    assert brave.calls == 2


async def test_no_cache_always_calls_provider() -> None:
    brave = _CountingBrave(_response(["https://a", "https://b", "https://c"]))
    client = DefaultSearchClient(brave=brave, cache=None)
    req = SearchRequest(query="python")

    await client.search(req)
    await client.search(req)

    assert brave.calls == 2


async def test_failure_is_not_cached(cache: DiskCacheBackend) -> None:
    class _FailingBrave:
        enabled = True

        def __init__(self) -> None:
            self.calls = 0

        async def search(self, _req: SearchRequest) -> SearchResponse:
            self.calls += 1
            raise SearchUnavailable("down")

    brave = _FailingBrave()
    client = DefaultSearchClient(brave=brave, searxng=None, cache=cache)
    req = SearchRequest(query="python")

    with pytest.raises(SearchUnavailable):
        await client.search(req)
    with pytest.raises(SearchUnavailable):
        await client.search(req)
    # Both attempts hit the provider — a raised failure is never cached.
    assert brave.calls == 2


async def test_cached_response_roundtrips_through_model_validate(
    cache: DiskCacheBackend,
) -> None:
    brave = _CountingBrave(_response(["https://a", "https://b", "https://c"]))
    client = DefaultSearchClient(brave=brave, cache=cache)
    req = SearchRequest(query="python")

    await client.search(req)
    restored = await client.search(req)

    # The cached value reconstructs into a fully-typed SearchResponse.
    assert isinstance(restored, SearchResponse)
    assert all(isinstance(r, Result) for r in restored.results)
