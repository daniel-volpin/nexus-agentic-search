"""Per-domain crawl rate limiting (Spec 03 §Per-domain rate limiting).

A token bucket keyed by registrable domain (eTLD+1 approximated by the
last two labels — no external dependency). Default 1 request / 1.5s,
burst 2. Politeness control that, together with robots crawl-delay,
keeps the home IP from hammering a single site.

``acquire`` returns immediately if a token is available; otherwise it
reports the wait so the caller can decide to wait or classify the
fetch as ``rate_limited`` rather than blocking the whole pipeline.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit


def registrable_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    return ".".join(labels[-2:])


class _Bucket:
    def __init__(self, rate_per_s: float, burst: int) -> None:
        self._rate = rate_per_s
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._updated = time.monotonic()

    def try_acquire(self) -> bool:
        now = time.monotonic()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class PerDomainRateLimiter:
    """Token bucket per registrable domain. One shared instance across
    concurrent crawls; guarded by an asyncio lock."""

    def __init__(self, rate_per_s: float = 1.0 / 1.5, burst: int = 2) -> None:
        self._rate = rate_per_s
        self._burst = burst
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def try_acquire(self, url: str) -> bool:
        """Consume a token for ``url``'s domain. Returns False if the
        domain is over its rate (caller classifies as ``rate_limited``)."""
        domain = registrable_domain(url)
        async with self._lock:
            bucket = self._buckets.get(domain)
            if bucket is None:
                bucket = _Bucket(self._rate, self._burst)
                self._buckets[domain] = bucket
            return bucket.try_acquire()
