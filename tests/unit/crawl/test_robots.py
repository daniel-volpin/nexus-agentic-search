"""Tests for robots.txt evaluation (Spec 03)."""

from __future__ import annotations

from nexus.crawl.robots import RobotsCache

_UA = "NexusAgenticSearch/0.1"


def _fetcher(body: str | None):
    async def fetch(_robots_url: str) -> str | None:
        return body

    return fetch


async def test_disallowed_path_blocked() -> None:
    robots = "User-agent: *\nDisallow: /private"
    cache = RobotsCache(user_agent=_UA)
    assert await cache.allowed("https://x.com/private/page", _fetcher(robots)) is False
    assert await cache.allowed("https://x.com/public/page", _fetcher(robots)) is True


async def test_default_allow_when_robots_missing() -> None:
    """robots fetch returns None (404/error) → default allow (Spec 03)."""
    cache = RobotsCache(user_agent=_UA)
    assert await cache.allowed("https://x.com/anything", _fetcher(None)) is True


async def test_default_allow_when_fetcher_raises() -> None:
    async def boom(_url: str) -> str | None:
        raise RuntimeError("network down")

    cache = RobotsCache(user_agent=_UA)
    assert await cache.allowed("https://x.com/anything", boom) is True


async def test_result_is_cached_per_host() -> None:
    calls = {"n": 0}

    async def counting_fetcher(_url: str) -> str | None:
        calls["n"] += 1
        return "User-agent: *\nDisallow: /no"

    cache = RobotsCache(user_agent=_UA)
    await cache.allowed("https://x.com/a", counting_fetcher)
    await cache.allowed("https://x.com/b", counting_fetcher)
    await cache.allowed("https://x.com/no", counting_fetcher)
    # One fetch for the host; subsequent calls hit the cache.
    assert calls["n"] == 1


async def test_different_hosts_fetched_separately() -> None:
    calls = {"n": 0}

    async def counting_fetcher(_url: str) -> str | None:
        calls["n"] += 1
        return "User-agent: *\nAllow: /"

    cache = RobotsCache(user_agent=_UA)
    await cache.allowed("https://a.com/x", counting_fetcher)
    await cache.allowed("https://b.com/x", counting_fetcher)
    assert calls["n"] == 2


async def test_crawl_delay_parsed() -> None:
    robots = "User-agent: *\nCrawl-delay: 5\nDisallow:"
    cache = RobotsCache(user_agent=_UA)
    await cache.allowed("https://x.com/a", _fetcher(robots))
    delay = await cache.crawl_delay("https://x.com/a")
    assert delay == 5.0


async def test_ttl_expiry_refetches() -> None:
    calls = {"n": 0}

    async def counting_fetcher(_url: str) -> str | None:
        calls["n"] += 1
        return "User-agent: *\nAllow: /"

    cache = RobotsCache(user_agent=_UA, ttl_s=0)  # immediate expiry
    await cache.allowed("https://x.com/a", counting_fetcher)
    await cache.allowed("https://x.com/b", counting_fetcher)
    assert calls["n"] == 2
