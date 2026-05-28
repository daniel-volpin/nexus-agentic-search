"""SearXNG fallback provider (Spec 01 §SearXNG fallback provider).

Engine-locked to ``google`` + ``duckduckgo``. Two safety controls,
both mandatory because scraping Google from a residential IP can get
the whole home network CAPTCHA-walled:

1. Per-engine client-side QPS cap (token bucket) — google ≤ 0.2 qps,
   duckduckgo ≤ 0.5 qps.
2. Per-engine CAPTCHA / abuse circuit breaker with exponential
   cool-down ``min(2^n * 30min, 64h)``, n = trips today. Auto-reset
   at UTC midnight.

A tripped engine is dropped from the engine list for subsequent
calls. If every configured engine is tripped, ``search`` raises
``SearchUnavailable`` — it never silently returns nothing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, date, datetime

import httpx

from .canonical import canonicalize
from .types import Result, SearchRequest, SearchResponse, SearchUnavailable

logger = logging.getLogger(__name__)

_ALLOWED_ENGINES = ("google", "duckduckgo")
_DEFAULT_QPS = {"google": 0.2, "duckduckgo": 0.5}
_TIMEOUT_S = 6.0
_TRIP_BASE_S = 30 * 60
_TRIP_MAX_S = 64 * 3600
_CAPTCHA_MARKERS = ("sorry/index", "captcha", "unusual traffic", "blocked", "too many requests")


class CircuitBreaker:
    """Per-engine breaker. Trips on CAPTCHA / 429; auto-recovers after a
    cool-down that grows with the day's trip count."""

    def __init__(self) -> None:
        self._disabled_until: dict[str, float] = {}
        self._trip_count: dict[str, int] = {}
        self._trip_day: dict[str, date] = {}

    def is_open(self, engine: str) -> bool:
        return time.time() < self._disabled_until.get(engine, 0.0)

    def record_success(self, engine: str) -> None:
        # Success does not reset the day's trip count; cool-down growth is
        # intentional within a day. The daily reset happens in record_trip.
        return

    def record_trip(self, engine: str, reason: str) -> None:
        today = datetime.now(UTC).date()
        if self._trip_day.get(engine) != today:
            self._trip_day[engine] = today
            self._trip_count[engine] = 0
        n = self._trip_count[engine]
        cooldown = min(_TRIP_BASE_S * (2**n), _TRIP_MAX_S)
        self._disabled_until[engine] = time.time() + cooldown
        self._trip_count[engine] = n + 1
        logger.warning(
            "searxng_engine_tripped",
            extra={"engine": engine, "reason": reason, "cooldown_s": cooldown},
        )


class _TokenBucket:
    """Minimal async token bucket for per-engine QPS limiting."""

    def __init__(self, rate_per_s: float, burst: int = 1) -> None:
        self._rate = rate_per_s
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
            self._updated = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
                self._updated = time.monotonic()
            else:
                self._tokens -= 1.0


class SearXNGProvider:
    """Async SearXNG JSON client, engine-locked + breaker-guarded."""

    def __init__(
        self,
        base_url: str,
        *,
        engines: tuple[str, ...] = _ALLOWED_ENGINES,
        qps_per_engine: dict[str, float] | None = None,
        timeout_s: float = _TIMEOUT_S,
        breaker: CircuitBreaker | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        bad = set(engines) - set(_ALLOWED_ENGINES)
        if bad:
            raise ValueError(f"engines must be a subset of {_ALLOWED_ENGINES}; got extra {bad}")
        self._base_url = base_url.rstrip("/")
        self._engines = engines
        self._timeout_s = timeout_s
        self._breaker = breaker or CircuitBreaker()
        self._client = client
        qps = {**_DEFAULT_QPS, **(qps_per_engine or {})}
        self._buckets = {e: _TokenBucket(qps[e]) for e in engines}

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    def active_engines(self) -> list[str]:
        return [e for e in self._engines if not self._breaker.is_open(e)]

    async def search(self, req: SearchRequest) -> SearchResponse:
        engines = self.active_engines()
        if not engines:
            raise SearchUnavailable("all searxng engines are tripped")

        for engine in engines:
            await self._buckets[engine].acquire()

        params = {
            "q": req.query,
            "format": "json",
            "engines": ",".join(engines),
        }
        if req.lang:
            params["language"] = req.lang

        started = time.perf_counter()
        payload = await self._request(params)
        latency_ms = int((time.perf_counter() - started) * 1000)

        self._apply_breaker_from_response(payload, engines)
        results = self._parse(payload)
        return SearchResponse(
            results=results,
            provider="searxng",
            query_sent=req.query,
            latency_ms=latency_ms,
        )

    # ---------- HTTP ----------

    async def _request(self, params: dict[str, str]) -> dict:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout_s)
        try:
            resp = await client.get(f"{self._base_url}/search", params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise SearchUnavailable(f"searxng transport error: {exc!s}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if resp.status_code == 429:
            # A 429 from SearXNG's front door — trip every active engine.
            for engine in params["engines"].split(","):
                self._breaker.record_trip(engine, "http_429")
            raise SearchUnavailable("searxng returned http 429")
        if resp.status_code != 200:
            raise SearchUnavailable(f"searxng returned http {resp.status_code}")
        return resp.json()

    # ---------- breaker detection ----------

    def _apply_breaker_from_response(self, payload: dict, engines: list[str]) -> None:
        """SearXNG reports per-engine failures in ``unresponsive_engines``,
        a list of ``[engine, reason]`` pairs. Trip the breaker on
        CAPTCHA-flavored reasons."""
        for entry in payload.get("unresponsive_engines", []) or []:
            if not isinstance(entry, list | tuple) or len(entry) < 2:
                continue
            engine, reason = str(entry[0]), str(entry[1]).lower()
            if engine not in engines:
                continue
            if any(marker in reason for marker in _CAPTCHA_MARKERS) or "429" in reason:
                self._breaker.record_trip(engine, reason)

    # ---------- parsing ----------

    @staticmethod
    def _parse(payload: dict) -> list[Result]:
        fetched_at = datetime.now(UTC)
        seen: set[str] = set()
        results: list[Result] = []
        rank = 0
        for item in payload.get("results", []) or []:
            url = canonicalize(item.get("url", ""))
            if not url or url in seen:
                continue
            engine_name = str(item.get("engine", "")).lower()
            if engine_name not in _ALLOWED_ENGINES:
                # Result came from an engine we did not request / allow.
                continue
            seen.add(url)
            results.append(
                Result(
                    url=url,
                    title=item.get("title", ""),
                    snippet=item.get("content", ""),
                    engine=f"searxng:{engine_name}",
                    rank=rank,
                    published_at=_parse_published(item),
                    fetched_at=fetched_at,
                )
            )
            rank += 1
        return results


def _parse_published(item: dict) -> datetime | None:
    raw = item.get("publishedDate")
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
