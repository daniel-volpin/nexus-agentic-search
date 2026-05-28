"""Adversarial tests for the crawler's per-hop redirect re-validation
(Spec 03 §Failure modes + Spec 10 §SSRF guard).

The async ``CrawlClient`` follows redirects MANUALLY with httpx
auto-redirect disabled, re-running the SSRF guard on every hop. We
mock ``socket.getaddrinfo`` so hostnames resolve to chosen IPs and use
an httpx ``MockTransport`` to emit responses.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import httpx
import pytest

from nexus.crawl import CrawlClient, CrawlRequest, PerDomainRateLimiter, RobotsCache, SSRFGuard

pytestmark = pytest.mark.security

_PUBLIC = "93.184.216.34"


def _resolver(mapping: dict[str, str]):
    """getaddrinfo replacement resolving hostnames per ``mapping``;
    IP-literal hosts resolve to themselves."""

    def fake(host, port, *args, **kwargs):
        ip = mapping.get(host, host)
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, None, None, "", (ip, port or 0))]

    return fake


def _crawler(handler, *, mapping: dict[str, str]) -> tuple[CrawlClient, dict]:
    """Build a CrawlClient whose HTTP is mocked and whose robots +
    rate limiter are permissive, so tests isolate redirect/SSRF logic."""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, follow_redirects=False)

    class _AllowRobots(RobotsCache):
        async def allowed(self, url, fetcher):  # type: ignore[override]
            return True

    crawler = CrawlClient(
        ssrf_guard=SSRFGuard(),
        rate_limiter=PerDomainRateLimiter(rate_per_s=1000, burst=1000),
        robots=_AllowRobots(user_agent="test"),
        client=client,
    )
    return crawler, {"resolver": _resolver(mapping)}


async def test_redirect_to_rfc1918_is_blocked_on_next_hop() -> None:
    """A 302 to http://10.0.0.1/ must trip the guard on the NEXT hop
    before any request is issued to the private address."""
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(str(request.url))
        # The first (public) request returns a redirect to RFC1918.
        return httpx.Response(302, headers={"Location": "http://10.0.0.1/secret"})

    crawler, ctx = _crawler(handler, mapping={"public.test": _PUBLIC})
    with patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=ctx["resolver"]):
        doc = await crawler.fetch(CrawlRequest(url="http://public.test/start"))

    assert doc.status == "blocked_by_ssrf_guard"
    assert doc.markdown == ""
    # The redirect chain recorded the public start, then stopped — the
    # private hop was never requested (guard fired before the request).
    assert all("10.0.0.1" not in p for p in requested_paths)
    assert "http://public.test/start" in doc.redirect_chain


async def test_redirect_to_metadata_ip_blocked() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    crawler, ctx = _crawler(handler, mapping={"public.test": _PUBLIC})
    with patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=ctx["resolver"]):
        doc = await crawler.fetch(CrawlRequest(url="http://public.test/x"))
    assert doc.status == "blocked_by_ssrf_guard"


async def test_redirect_to_public_is_followed_and_extracted() -> None:
    """A redirect to another public host is followed and the final body
    extracted — proves the rejection above is specific to blocked ranges."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(302, headers={"Location": "http://elsewhere.test/final"})
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            content=b"<html><body><p>final content</p></body></html>",
        )

    crawler, ctx = _crawler(
        handler, mapping={"public.test": _PUBLIC, "elsewhere.test": "93.184.216.35"}
    )
    with patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=ctx["resolver"]):
        doc = await crawler.fetch(CrawlRequest(url="http://public.test/start"))

    assert doc.status == "ok"
    assert "final content" in doc.markdown
    assert doc.url == "http://elsewhere.test/final"
    assert doc.redirect_chain == ["http://public.test/start", "http://elsewhere.test/final"]


async def test_initial_blocked_url_never_makes_request() -> None:
    issued = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        issued["n"] += 1
        return httpx.Response(200)

    crawler, ctx = _crawler(handler, mapping={})
    # No mapping → 169.254.169.254 is an IP literal, rejected outright.
    with patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=ctx["resolver"]):
        doc = await crawler.fetch(CrawlRequest(url="http://169.254.169.254/"))
    assert doc.status == "blocked_by_ssrf_guard"
    assert issued["n"] == 0


async def test_redirect_loop_exceeds_budget() -> None:
    """Endless redirects are capped at the redirect budget."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://public.test/again"})

    crawler, ctx = _crawler(handler, mapping={"public.test": _PUBLIC})
    with patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=ctx["resolver"]):
        doc = await crawler.fetch(CrawlRequest(url="http://public.test/start"))
    assert doc.status == "http_4xx"  # budget exceeded → classified, not hung
