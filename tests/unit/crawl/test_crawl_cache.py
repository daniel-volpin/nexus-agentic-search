"""Document-cache integration for CrawlClient (Spec 09 wiring)."""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from nexus.cache import DiskCacheBackend
from nexus.crawl import CrawlClient, CrawlRequest, PerDomainRateLimiter, RobotsCache, SSRFGuard
from nexus.crawl.types import Document

_PUBLIC = "93.184.216.34"


def _resolver(host, port, *args, **kwargs):
    return [(socket.AF_INET, None, None, "", (_PUBLIC, port or 0))]


class _AllowRobots(RobotsCache):
    async def allowed(self, url, fetcher):  # type: ignore[override]
        return True


@pytest.fixture
def cache(tmp_path: Path) -> DiskCacheBackend:
    return DiskCacheBackend(
        root=tmp_path,
        namespace="crawl.document",
        version=1,
        ttl_default_s=3600,
        size_limit_bytes=1 << 20,
    )


def _crawler(handler, cache: DiskCacheBackend | None) -> CrawlClient:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return CrawlClient(
        ssrf_guard=SSRFGuard(),
        rate_limiter=PerDomainRateLimiter(rate_per_s=1000, burst=1000),
        robots=_AllowRobots(user_agent="test"),
        cache=cache,
        client=client,
    )


async def test_ok_document_cached_and_reused(cache: DiskCacheBackend) -> None:
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200, headers={"Content-Type": "text/html"}, content=b"<p>cached body</p>"
        )

    crawler = _crawler(handler, cache)
    req = CrawlRequest(url="http://public.test/a")
    with patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=_resolver):
        first = await crawler.fetch(req)
        second = await crawler.fetch(req)

    assert first.status == "ok"
    assert calls["n"] == 1, "second fetch of same URL must be served from cache"
    assert second.markdown == first.markdown
    assert isinstance(second, Document)


async def test_failed_document_not_cached(cache: DiskCacheBackend) -> None:
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    crawler = _crawler(handler, cache)
    req = CrawlRequest(url="http://public.test/a")
    with patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=_resolver):
        await crawler.fetch(req)
        await crawler.fetch(req)

    # A non-ok document is transient — re-attempted, never cached.
    assert calls["n"] == 2


async def test_no_cache_refetches(cache: DiskCacheBackend) -> None:
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<p>x</p>")

    crawler = _crawler(handler, None)
    req = CrawlRequest(url="http://public.test/a")
    with patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=_resolver):
        await crawler.fetch(req)
        await crawler.fetch(req)

    assert calls["n"] == 2


async def test_cache_key_distinguishes_urls(cache: DiskCacheBackend) -> None:
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<p>x</p>")

    crawler = _crawler(handler, cache)
    with patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=_resolver):
        await crawler.fetch(CrawlRequest(url="http://public.test/a"))
        await crawler.fetch(CrawlRequest(url="http://public.test/b"))

    assert calls["n"] == 2
