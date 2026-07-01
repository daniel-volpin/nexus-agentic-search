"""Tests for the SearXNG provider + circuit breaker (Spec 01)."""

from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import pytest

from nexus.search import SearchRequest, SearchUnavailable
from nexus.search.searxng import CircuitBreaker, SearXNGProvider


def _transport(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _results_payload() -> dict:
    return {
        "results": [
            {"url": "https://a.test/1", "title": "A", "content": "c1", "engine": "google"},
            {"url": "https://b.test/2", "title": "B", "content": "c2", "engine": "duckduckgo"},
            # engine outside the allowlist is dropped
            {"url": "https://c.test/3", "title": "C", "content": "c3", "engine": "bing"},
        ],
        "unresponsive_engines": [],
    }


# ---------- engine allowlist ----------


def test_rejects_engines_outside_allowlist() -> None:
    with pytest.raises(ValueError, match="subset"):
        SearXNGProvider("http://searxng:8080", engines=("google", "bing"))


async def test_results_from_disallowed_engine_are_dropped() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_results_payload())

    provider = SearXNGProvider("http://searxng:8080", client=_transport(handler))
    resp = await provider.search(SearchRequest(query="x"))
    engines = {r.engine for r in resp.results}
    assert engines == {"searxng:google", "searxng:duckduckgo"}
    assert all("bing" not in r.engine for r in resp.results)


async def test_only_allowlisted_engines_sent_in_query() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["engines"] = request.url.params.get("engines", "")
        return httpx.Response(200, json={"results": []})

    provider = SearXNGProvider("http://searxng:8080", client=_transport(handler))
    await provider.search(SearchRequest(query="x"))
    assert set(seen["engines"].split(",")) <= {"google", "duckduckgo"}


async def test_api_key_sent_as_x_searx_key_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["header"] = request.headers.get("X-Searx-Key", "")
        return httpx.Response(200, json={"results": []})

    provider = SearXNGProvider(
        "http://searxng:8080",
        api_key="test-searx-key",
        client=_transport(handler),
    )
    await provider.search(SearchRequest(query="x"))
    assert seen["header"] == "test-searx-key"


# ---------- circuit breaker ----------


def test_breaker_trips_and_opens() -> None:
    breaker = CircuitBreaker()
    assert breaker.is_open("google") is False
    breaker.record_trip("google", "recaptcha")
    assert breaker.is_open("google") is True
    # duckduckgo unaffected
    assert breaker.is_open("duckduckgo") is False


def test_breaker_cooldown_grows_with_trip_count() -> None:
    breaker = CircuitBreaker()
    now = 1_000_000.0
    with patch("nexus.search.searxng.time.time", return_value=now):
        breaker.record_trip("google", "x")
        first_until = breaker._disabled_until["google"]
        breaker.record_trip("google", "x")
        second_until = breaker._disabled_until["google"]
    # Second cooldown is longer (exponential).
    assert (second_until - now) > (first_until - now)


def test_breaker_recovers_after_cooldown() -> None:
    breaker = CircuitBreaker()
    t = [1_000_000.0]
    with patch("nexus.search.searxng.time.time", side_effect=lambda: t[0]):
        breaker.record_trip("google", "x")
        assert breaker.is_open("google") is True
        t[0] += 30 * 60 + 1  # past the 30-min base cooldown
        assert breaker.is_open("google") is False


async def test_captcha_in_unresponsive_engines_trips_breaker() -> None:
    payload = {
        "results": [],
        "unresponsive_engines": [["google", "CAPTCHA required"]],
    }

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    breaker = CircuitBreaker()
    provider = SearXNGProvider("http://searxng:8080", breaker=breaker, client=_transport(handler))
    await provider.search(SearchRequest(query="x"))
    assert breaker.is_open("google") is True
    assert breaker.is_open("duckduckgo") is False


async def test_tripped_engine_excluded_from_next_query() -> None:
    sent_engines: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_engines.append(request.url.params.get("engines", ""))
        return httpx.Response(200, json={"results": []})

    breaker = CircuitBreaker()
    breaker.record_trip("google", "recaptcha")
    provider = SearXNGProvider("http://searxng:8080", breaker=breaker, client=_transport(handler))
    await provider.search(SearchRequest(query="x"))
    assert sent_engines == ["duckduckgo"]


async def test_all_engines_tripped_raises() -> None:
    breaker = CircuitBreaker()
    breaker.record_trip("google", "recaptcha")
    breaker.record_trip("duckduckgo", "recaptcha")
    provider = SearXNGProvider("http://searxng:8080", breaker=breaker)
    with pytest.raises(SearchUnavailable, match="tripped"):
        await provider.search(SearchRequest(query="x"))


async def test_http_429_trips_all_active_engines_and_raises() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    breaker = CircuitBreaker()
    provider = SearXNGProvider("http://searxng:8080", breaker=breaker, client=_transport(handler))
    with pytest.raises(SearchUnavailable, match="429"):
        await provider.search(SearchRequest(query="x"))
    assert breaker.is_open("google") is True
    assert breaker.is_open("duckduckgo") is True


# ---------- failure modes ----------


async def test_non_200_raises() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    provider = SearXNGProvider("http://searxng:8080", client=_transport(handler))
    with pytest.raises(SearchUnavailable, match="http 502"):
        await provider.search(SearchRequest(query="x"))


async def test_transport_error_raises() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    provider = SearXNGProvider("http://searxng:8080", client=_transport(handler))
    with pytest.raises(SearchUnavailable, match="transport"):
        await provider.search(SearchRequest(query="x"))


# ---------- QPS limiter ----------


async def test_qps_limiter_throttles_second_call() -> None:
    """A second back-to-back call must wait for the bucket to refill.
    google qps=0.2 → ~5s between calls; we use a high override to keep
    the test fast while still proving the limiter waits."""

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    provider = SearXNGProvider(
        "http://searxng:8080",
        engines=("google",),
        qps_per_engine={"google": 20.0},  # 50ms spacing
        client=_transport(handler),
    )
    await provider.search(SearchRequest(query="x"))  # consumes burst token
    start = time.monotonic()
    await provider.search(SearchRequest(query="x"))  # must wait ~50ms
    elapsed = time.monotonic() - start
    assert elapsed >= 0.04, f"limiter did not throttle: {elapsed:.3f}s"


async def test_owned_client_is_reused_across_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed = 0
    closed = 0

    class _FakeClient:
        async def get(self, *args, **kwargs) -> httpx.Response:
            return httpx.Response(200, json={"results": [], "unresponsive_engines": []})

        async def aclose(self) -> None:
            nonlocal closed
            closed += 1

    def _factory(*args, **kwargs):
        nonlocal constructed
        constructed += 1
        return _FakeClient()

    monkeypatch.setattr("nexus.search.searxng.httpx.AsyncClient", _factory)
    provider = SearXNGProvider("http://searxng:8080", engines=("google",))

    await provider.search(SearchRequest(query="x"))
    await provider.search(SearchRequest(query="y"))
    await provider.aclose()

    assert constructed == 1
    assert closed == 1
