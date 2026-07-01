"""Brave Search API provider.

Contractual web search via the Brave Search API. No HTML scraping.
Returns results normalized into the provider-neutral ``Result`` schema
with canonical, deduped URLs.

Failure policy: 429 → exponential backoff with
jitter (max 3); 5xx → one retry; timeout → ``SearchUnavailable``. A
missing API key makes the provider ``enabled = False`` so the router
can skip it cleanly rather than burning a 401 round-trip.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from datetime import UTC, datetime

import httpx

from .canonical import canonicalize
from .types import Result, SearchRequest, SearchResponse, SearchUnavailable

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_TIMEOUT_S = 10.0
_MAX_429_RETRIES = 3
_BACKOFF_BASE_S = 0.2
_FRESHNESS_MAP = {"any": None, "day": "pd", "week": "pw", "month": "pm", "year": "py"}


class BraveProvider:
    """Async Brave Search API client."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = _ENDPOINT,
        timeout_s: float = _TIMEOUT_S,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._timeout_s = timeout_s
        self._client = client  # injectable for tests
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        """True only when an API key is configured. The router skips a
        disabled provider rather than issuing a doomed request."""
        return bool(self._api_key)

    async def search(self, req: SearchRequest) -> SearchResponse:
        if not self.enabled:
            raise SearchUnavailable("brave api key not configured")

        params: dict[str, str | int] = {
            "q": req.query,
            "count": min(req.max_results, 20),
        }
        freshness = _FRESHNESS_MAP.get(req.freshness)
        if freshness:
            params["freshness"] = freshness
        if req.country:
            params["country"] = req.country
        if req.lang:
            params["search_lang"] = req.lang

        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self._api_key,
        }

        started = time.perf_counter()
        payload = await self._request_with_retries(params, headers)
        latency_ms = int((time.perf_counter() - started) * 1000)

        results = self._parse(payload)
        return SearchResponse(
            results=results,
            provider="brave",
            query_sent=req.query,
            latency_ms=latency_ms,
        )

    # ---------- HTTP ----------

    async def _request_with_retries(
        self, params: dict[str, str | int], headers: dict[str, str]
    ) -> dict:
        client = self._get_client()
        attempt = 0
        while True:
            try:
                resp = await client.get(self._endpoint, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise SearchUnavailable(f"brave transport error: {exc!s}") from exc

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429 and attempt < _MAX_429_RETRIES:
                await asyncio.sleep(self._backoff(attempt))
                attempt += 1
                continue
            if 500 <= resp.status_code < 600 and attempt == 0:
                await asyncio.sleep(0.5)
                attempt += 1
                continue
            raise SearchUnavailable(f"brave returned http {resp.status_code}")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _backoff(attempt: int) -> float:
        # 0.2 / 0.8 / 3.2 s with added jitter.
        base = _BACKOFF_BASE_S * (4**attempt)
        return base + secrets.randbelow(100) / 1000.0

    # ---------- parsing ----------

    @staticmethod
    def _parse(payload: dict) -> list[Result]:
        web = payload.get("web") or {}
        raw_results = web.get("results") or []
        fetched_at = datetime.now(UTC)
        seen: set[str] = set()
        results: list[Result] = []
        rank = 0
        for item in raw_results:
            url = canonicalize(item.get("url", ""))
            if not url or url in seen:
                continue
            seen.add(url)
            results.append(
                Result(
                    url=url,
                    title=item.get("title", ""),
                    snippet=item.get("description", ""),
                    engine="brave",
                    rank=rank,
                    published_at=_parse_age(item),
                    fetched_at=fetched_at,
                )
            )
            rank += 1
        return results


def _parse_age(item: dict) -> datetime | None:
    """Brave returns `page_age` as an ISO timestamp when known."""
    raw = item.get("page_age") or item.get("age")
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
