"""Search routing.

``DefaultSearchClient`` is the single entry point the orchestrator
depends on. Routing is deterministic and explicit — no silent
degradation:

- If Brave is enabled: call Brave. On ``SearchUnavailable`` OR thin
  coverage (< MIN_RESULTS) fall through to SearXNG, then merge +
  dedup (Brave order preserved, SearXNG appended).
- If Brave is not enabled (no API key): go straight to SearXNG.
- If the chosen path(s) all fail: raise ``SearchUnavailable``. The
  client never returns an empty response to mask an upstream failure
  — empty results mean "the providers genuinely found nothing", a
  failure means "raise so the orchestrator surfaces it".
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from nexus.cache import CacheLike
from nexus.cache.keys import search_key
from nexus.telemetry import CACHE_HIT_TOTAL, CACHE_MISS_TOTAL

from .brave import BraveProvider
from .searxng import SearXNGProvider
from .types import Result, SearchRequest, SearchResponse, SearchUnavailable

logger = logging.getLogger(__name__)

_MIN_RESULTS_BEFORE_FALLBACK = 3
_CACHE_NAMESPACE = "search"


class SearchClient(Protocol):
    async def search(self, req: SearchRequest) -> SearchResponse: ...


class DefaultSearchClient:
    """Brave-first router with SearXNG fallback and an optional result cache.

    The cache (if provided) stores the *merged* response keyed by the
    logical query — provider-agnostic, so a Brave hit and a
    Brave+SearXNG merge for the same query share one entry. Caching is
    best-effort: a cache miss or backend error simply runs the live
    route.
    """

    def __init__(
        self,
        *,
        brave: BraveProvider,
        searxng: SearXNGProvider | None = None,
        cache: CacheLike | None = None,
    ) -> None:
        self._brave = brave
        self._searxng = searxng
        self._cache = cache

    async def search(self, req: SearchRequest) -> SearchResponse:
        key = search_key(
            query=req.query,
            freshness=req.freshness,
            max_results=req.max_results,
            lang=req.lang,
            country=req.country,
        )
        if self._cache is not None:
            cached = await self._cache.get(key)
            if cached is not None:
                CACHE_HIT_TOTAL.labels(namespace=_CACHE_NAMESPACE).inc()
                return self._cap_response(SearchResponse.model_validate(cached), req.max_results)
            CACHE_MISS_TOTAL.labels(namespace=_CACHE_NAMESPACE).inc()

        response = await self._route(req)

        if self._cache is not None:
            await self._cache.set(key, response.model_dump(mode="json"))
        return self._cap_response(response, req.max_results)

    async def _route(self, req: SearchRequest) -> SearchResponse:
        started = time.perf_counter()

        if not self._brave.enabled:
            response = await self._searxng_only(req)
            return self._stamp(response, started)

        try:
            brave_response = await self._brave.search(req)
        except SearchUnavailable as exc:
            logger.warning("brave_unavailable_falling_back", extra={"reason": str(exc)})
            response = await self._searxng_only(req, brave_error=exc)
            return self._stamp(response, started)

        if len(brave_response.results) >= _MIN_RESULTS_BEFORE_FALLBACK or self._searxng is None:
            return self._stamp(brave_response, started)

        # Thin Brave coverage — augment with SearXNG, keep Brave's order.
        merged = await self._augment_with_searxng(req, brave_response)
        return self._stamp(merged, started)

    # ---------- routing helpers ----------

    async def _searxng_only(
        self, req: SearchRequest, *, brave_error: SearchUnavailable | None = None
    ) -> SearchResponse:
        if self._searxng is None:
            detail = "no search providers configured"
            if brave_error is not None:
                detail = f"brave unavailable ({brave_error}) and searxng not configured"
            raise SearchUnavailable(detail)
        return await self._searxng.search(req)

    async def _augment_with_searxng(
        self, req: SearchRequest, brave_response: SearchResponse
    ) -> SearchResponse:
        assert self._searxng is not None  # guarded by caller
        try:
            searxng_response = await self._searxng.search(req)
        except SearchUnavailable:
            # Augmentation is best-effort; Brave's (thin) results stand.
            return brave_response

        seen = {r.url for r in brave_response.results}
        merged: list[Result] = list(brave_response.results)
        next_rank = len(merged)
        for result in searxng_response.results:
            if result.url in seen:
                continue
            seen.add(result.url)
            merged.append(result.model_copy(update={"rank": next_rank}))
            next_rank += 1

        return SearchResponse(
            results=merged,
            provider="brave+searxng",
            query_sent=req.query,
            latency_ms=brave_response.latency_ms + searxng_response.latency_ms,
        )

    @staticmethod
    def _stamp(response: SearchResponse, started: float) -> SearchResponse:
        # Record total routing latency (overrides per-provider value).
        return response.model_copy(
            update={"latency_ms": int((time.perf_counter() - started) * 1000)}
        )

    @staticmethod
    def _cap_response(response: SearchResponse, max_results: int) -> SearchResponse:
        if len(response.results) <= max_results:
            return response
        return response.model_copy(update={"results": response.results[:max_results]})
