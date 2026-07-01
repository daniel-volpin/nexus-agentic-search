"""Tests for the async CrawlClient (Spec 03)."""

from __future__ import annotations

import socket
from unittest.mock import patch

import httpx
import pytest

from nexus.crawl import CrawlClient, CrawlRequest, PerDomainRateLimiter, RobotsCache, SSRFGuard

_PUBLIC = "93.184.216.34"


def _resolver(mapping: dict[str, str]):
    def fake(host, port, *args, **kwargs):
        ip = mapping.get(host, host)
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, None, None, "", (ip, port or 0))]

    return fake


class _AllowAllRobots(RobotsCache):
    async def allowed(self, url, fetcher):  # type: ignore[override]
        return True


def _crawler(
    handler, *, robots: RobotsCache | None = None, rate: PerDomainRateLimiter | None = None
):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return CrawlClient(
        ssrf_guard=SSRFGuard(),
        rate_limiter=rate or PerDomainRateLimiter(rate_per_s=1000, burst=1000),
        robots=robots or _AllowAllRobots(user_agent="test"),
        client=client,
    )


def _patch_dns(mapping: dict[str, str]):
    return patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=_resolver(mapping))


# ---------- happy path ----------


async def test_fetch_ok_extracts_and_envelopes() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            content=b"<html><body><p>hello body</p><script>x</script></body></html>",
        )

    crawler = _crawler(handler)
    with _patch_dns({"public.test": _PUBLIC}):
        doc = await crawler.fetch(CrawlRequest(url="http://public.test/a"))

    assert doc.status == "ok"
    assert "hello body" in doc.markdown
    assert "x" not in doc.markdown.split()  # script dropped
    assert doc.enveloped_markdown.startswith("<untrusted_source")
    assert doc.content_hash == __import__("hashlib").sha256(doc.markdown.encode()).hexdigest()


async def test_pinned_connection_sets_host_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.headers.get("Host", "")
        seen["url_host"] = request.url.host
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<p>ok</p>")

    crawler = _crawler(handler)
    with _patch_dns({"public.test": _PUBLIC}):
        await crawler.fetch(CrawlRequest(url="http://public.test/a"))

    # Connected to the pinned IP, but presented the hostname via Host.
    assert seen["url_host"] == _PUBLIC
    assert seen["host"] == "public.test"


# ---------- status mapping ----------


async def test_http_404_mapped() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    crawler = _crawler(handler)
    with _patch_dns({"public.test": _PUBLIC}):
        doc = await crawler.fetch(CrawlRequest(url="http://public.test/a"))
    assert doc.status == "http_4xx"
    assert doc.http_status == 404


async def test_http_503_mapped() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    crawler = _crawler(handler)
    with _patch_dns({"public.test": _PUBLIC}):
        doc = await crawler.fetch(CrawlRequest(url="http://public.test/a"))
    assert doc.status == "http_5xx"


async def test_unsupported_content_type() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=b"%PDF")

    crawler = _crawler(handler)
    with _patch_dns({"public.test": _PUBLIC}):
        doc = await crawler.fetch(CrawlRequest(url="http://public.test/a"))
    assert doc.status == "unsupported_content_type"


async def test_too_large_body() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"x" * 100)

    crawler = _crawler(handler)
    with _patch_dns({"public.test": _PUBLIC}):
        doc = await crawler.fetch(CrawlRequest(url="http://public.test/a", max_bytes=10))
    assert doc.status == "too_large"


async def test_timeout_mapped() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    crawler = _crawler(handler)
    with _patch_dns({"public.test": _PUBLIC}):
        doc = await crawler.fetch(CrawlRequest(url="http://public.test/a"))
    assert doc.status == "timeout"


async def test_failed_document_has_empty_markdown() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    crawler = _crawler(handler)
    with _patch_dns({"public.test": _PUBLIC}):
        doc = await crawler.fetch(CrawlRequest(url="http://public.test/a"))
    assert doc.markdown == ""
    assert doc.enveloped_markdown == ""


# ---------- robots ----------


async def test_blocked_by_robots() -> None:
    class _DenyAll(RobotsCache):
        async def allowed(self, url, fetcher):  # type: ignore[override]
            return False

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<p>x</p>")

    crawler = _crawler(handler, robots=_DenyAll(user_agent="test"))
    with _patch_dns({"public.test": _PUBLIC}):
        doc = await crawler.fetch(CrawlRequest(url="http://public.test/a"))
    assert doc.status == "blocked_by_robots"


async def test_respect_robots_false_skips_robots() -> None:
    class _DenyAll(RobotsCache):
        async def allowed(self, url, fetcher):  # type: ignore[override]
            raise AssertionError("robots must not be consulted when respect_robots=False")

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<p>ok</p>")

    crawler = _crawler(handler, robots=_DenyAll(user_agent="test"))
    with _patch_dns({"public.test": _PUBLIC}):
        doc = await crawler.fetch(CrawlRequest(url="http://public.test/a", respect_robots=False))
    assert doc.status == "ok"


# ---------- rate limiting ----------


async def test_rate_limited_status() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<p>x</p>")

    # burst=1, rate≈0 → second request to same domain is rate_limited.
    rate = PerDomainRateLimiter(rate_per_s=0.0001, burst=1)
    crawler = _crawler(handler, rate=rate)
    with _patch_dns({"public.test": _PUBLIC}):
        first = await crawler.fetch(CrawlRequest(url="http://public.test/a"))
        second = await crawler.fetch(CrawlRequest(url="http://public.test/b"))
    assert first.status == "ok"
    assert second.status == "rate_limited"


# ---------- render_js: no silent fallback ----------


async def test_render_js_raises_not_implemented() -> None:
    crawler = _crawler(lambda _r: httpx.Response(200))
    with pytest.raises(NotImplementedError, match="render_js"):
        await crawler.fetch(CrawlRequest(url="http://public.test/a", render_js=True))


async def test_owned_client_is_reused_across_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed = 0
    closed = 0

    class _FakeClient:
        async def get(self, *args, **kwargs) -> httpx.Response:
            return httpx.Response(
                200, headers={"Content-Type": "text/html"}, content=b"<html><body><p>ok</p></body></html>"
            )

        async def aclose(self) -> None:
            nonlocal closed
            closed += 1

    def _factory(*args, **kwargs):
        nonlocal constructed
        constructed += 1
        return _FakeClient()

    monkeypatch.setattr("nexus.crawl.client.httpx.AsyncClient", _factory)
    crawler = CrawlClient(
        ssrf_guard=SSRFGuard(),
        rate_limiter=PerDomainRateLimiter(rate_per_s=1000, burst=1000),
        robots=_AllowAllRobots(user_agent="test"),
    )

    with _patch_dns({"public.test": _PUBLIC}):
        await crawler.fetch(CrawlRequest(url="http://public.test/a"))
        await crawler.fetch(CrawlRequest(url="http://public.test/b"))
    await crawler.aclose()

    assert constructed == 1
    assert closed == 1
