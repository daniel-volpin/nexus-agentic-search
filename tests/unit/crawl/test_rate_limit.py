"""Tests for per-domain rate limiting (Spec 03)."""

from __future__ import annotations

from nexus.crawl.rate_limit import PerDomainRateLimiter, registrable_domain


def test_registrable_domain_extraction() -> None:
    assert registrable_domain("https://www.example.com/path") == "example.com"
    assert registrable_domain("http://example.com/") == "example.com"
    assert registrable_domain("https://a.b.example.co/") == "example.co"
    assert registrable_domain("http://localhost:8080/") == "localhost"


async def test_burst_then_blocked() -> None:
    limiter = PerDomainRateLimiter(rate_per_s=0.0001, burst=2)
    assert await limiter.try_acquire("https://x.com/1") is True
    assert await limiter.try_acquire("https://x.com/2") is True
    assert await limiter.try_acquire("https://x.com/3") is False


async def test_domains_are_independent() -> None:
    limiter = PerDomainRateLimiter(rate_per_s=0.0001, burst=1)
    assert await limiter.try_acquire("https://a.com/") is True
    # Different domain has its own bucket.
    assert await limiter.try_acquire("https://b.com/") is True
    # Same domain again → blocked.
    assert await limiter.try_acquire("https://a.com/x") is False


async def test_subdomains_share_registrable_domain_bucket() -> None:
    limiter = PerDomainRateLimiter(rate_per_s=0.0001, burst=1)
    assert await limiter.try_acquire("https://www.x.com/") is True
    assert await limiter.try_acquire("https://api.x.com/") is False


async def test_tokens_refill_over_time() -> None:
    limiter = PerDomainRateLimiter(rate_per_s=1000.0, burst=1)
    assert await limiter.try_acquire("https://x.com/") is True
    import asyncio

    await asyncio.sleep(0.01)  # 1000/s → refills quickly
    assert await limiter.try_acquire("https://x.com/") is True
