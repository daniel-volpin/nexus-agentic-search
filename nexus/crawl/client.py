"""Async crawl client.

The system's primary network egress and primary attack surface. Every
fetch:

1. Passes the SSRF guard, which resolves the host and returns a pinned
   public IP. The connection is made to that IP with the original
   hostname presented for SNI / cert / Host — DNS rebinding cannot
   swap in a private address between check and connect.
2. Follows redirects MANUALLY (httpx auto-redirect disabled), re-running
   the SSRF guard on every hop, capped at 5.
3. Respects robots.txt (default-allow on robots fetch failure) and a
   per-domain rate limit.
4. Caps body size, restricts content types, extracts visible text only
   (scripts/styles/hidden content stripped), and wraps the result in
   the untrusted-source envelope.

The JS-rendering path (``render_js=True``) is NOT implemented — it
raises ``NotImplementedError`` rather than silently downgrading to the
non-JS path. No weird fallbacks.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime

import httpx

from nexus.cache import CacheLike
from nexus.cache.keys import crawl_doc_key
from nexus.search.canonical import canonicalize
from nexus.telemetry import CACHE_HIT_TOTAL, CACHE_MISS_TOTAL

from .envelope import wrap_untrusted
from .extract import extract_markdown
from .rate_limit import PerDomainRateLimiter
from .robots import RobotsCache
from .ssrf import SSRFGuard
from .types import CrawlRequest, CrawlStatus, Document

logger = logging.getLogger(__name__)

_CACHE_NAMESPACE = "crawl.document"

_ALLOWED_CONTENT_TYPES = {"text/html", "text/markdown", "application/xhtml+xml", "text/plain"}
_MAX_REDIRECTS = 5
_EMPTY_HASH = hashlib.sha256(b"").hexdigest()
_DEFAULT_UA = "NexusAgenticSearch/0.1"


class CrawlClient:
    def __init__(
        self,
        ssrf_guard: SSRFGuard | None = None,
        *,
        user_agent: str = _DEFAULT_UA,
        rate_limiter: PerDomainRateLimiter | None = None,
        robots: RobotsCache | None = None,
        cache: CacheLike | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._ssrf = ssrf_guard or SSRFGuard()
        self._user_agent = user_agent
        self._rate = rate_limiter or PerDomainRateLimiter()
        self._robots = robots or RobotsCache(user_agent=user_agent)
        self._cache = cache
        self._client = client  # injectable for tests
        self._owns_client = client is None

    async def fetch(self, req: CrawlRequest) -> Document:
        if req.render_js:
            # No silent downgrade: the caller asked for rendered DOM and
            # we cannot provide it yet. Surface it explicitly.
            raise NotImplementedError(
                "render_js=True is not implemented; JS rendering "
                "(Crawl4AI/Playwright) is not wired yet"
            )

        requested = req.url
        now = datetime.now(UTC)

        # Document cache: keyed by canonical URL + render_js + max_bytes.
        # Only successful (status="ok") documents are cached; failures are
        # transient and must be re-attempted.
        cache_key = crawl_doc_key(
            canonical_url=canonicalize(req.url) or req.url,
            render_js=req.render_js,
            max_bytes=req.max_bytes,
        )
        if self._cache is not None:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                CACHE_HIT_TOTAL.labels(namespace=_CACHE_NAMESPACE).inc()
                return Document.model_validate(cached)
            CACHE_MISS_TOTAL.labels(namespace=_CACHE_NAMESPACE).inc()

        # Rate limit (per registrable domain).
        if not await self._rate.try_acquire(req.url):
            return self._empty(requested, now, "rate_limited", "", None, [])

        # robots.txt (default-allow on fetch failure).
        if req.respect_robots and not await self._robots.allowed(req.url, self._fetch_robots_text):
            return self._empty(requested, now, "blocked_by_robots", "", None, [])

        client = self._get_client()
        document = await self._fetch_following_redirects(req, requested, now, client)
        if self._cache is not None and document.status == "ok":
            await self._cache.set(cache_key, document.model_dump(mode="json"))
        return document

    # ---------- redirect loop with per-hop SSRF guard ----------

    async def _fetch_following_redirects(
        self,
        req: CrawlRequest,
        requested: str,
        now: datetime,
        client: httpx.AsyncClient,
    ) -> Document:
        url = req.url
        chain: list[str] = []
        render_start = time.perf_counter()

        for _hop in range(_MAX_REDIRECTS + 1):
            try:
                target = self._ssrf.resolve_pinned(url)
            except ValueError:
                return self._empty(requested, now, "blocked_by_ssrf_guard", "", None, chain)

            chain.append(url)
            try:
                resp = await self._request_pinned(client, url, target, req.timeout_s)
            except httpx.TimeoutException:
                return self._empty(requested, now, "timeout", url, None, chain)
            except httpx.HTTPError as exc:
                logger.warning("crawl_transport_error", extra={"reason": str(exc)})
                return self._empty(requested, now, "extraction_failed", url, None, chain)

            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    return self._empty(requested, now, "http_4xx", url, resp.status_code, chain)
                url = str(httpx.URL(url).join(location))
                continue

            return self._finalize(req, requested, now, url, chain, resp, render_start)

        # Exceeded redirect budget.
        return self._empty(requested, now, "http_4xx", url, None, chain)

    async def _request_pinned(
        self,
        client: httpx.AsyncClient,
        url: str,
        target,
        timeout_s: float,
    ) -> httpx.Response:
        """Issue a GET to the pinned IP while presenting the original
        hostname for routing (Host header) and TLS (sni_hostname)."""
        original = httpx.URL(url)
        pinned = httpx.URL(target.pinned_url_base).copy_with(raw_path=original.raw_path)
        headers = {"User-Agent": self._user_agent, "Host": original.netloc.decode("ascii")}
        extensions = {"sni_hostname": target.host} if target.scheme == "https" else {}
        return await client.get(pinned, headers=headers, extensions=extensions, timeout=timeout_s)

    # ---------- finalize a non-redirect response ----------

    def _finalize(
        self,
        req: CrawlRequest,
        requested: str,
        now: datetime,
        final_url: str,
        chain: list[str],
        resp: httpx.Response,
        render_start: float,
    ) -> Document:
        status_code = resp.status_code
        if 400 <= status_code <= 499:
            return self._empty(requested, now, "http_4xx", final_url, status_code, chain)
        if status_code >= 500:
            return self._empty(requested, now, "http_5xx", final_url, status_code, chain)

        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type not in _ALLOWED_CONTENT_TYPES:
            return self._empty(
                requested, now, "unsupported_content_type", final_url, status_code, chain
            )

        body = resp.content
        if len(body) > req.max_bytes:
            return self._empty(requested, now, "too_large", final_url, status_code, chain)

        render_ms = int((time.perf_counter() - render_start) * 1000)
        extract_start = time.perf_counter()
        markdown = extract_markdown(body.decode("utf-8", errors="ignore"))
        extraction_ms = int((time.perf_counter() - extract_start) * 1000)
        content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()

        return Document(
            url=final_url,
            requested_url=requested,
            content_hash=content_hash,
            markdown=markdown,
            enveloped_markdown=wrap_untrusted(final_url, content_hash, markdown),
            content_type=content_type,
            fetched_at=now,
            status="ok",
            http_status=status_code,
            bytes_in=len(body),
            render_ms=render_ms,
            extraction_ms=extraction_ms,
            redirect_chain=chain,
        )

    # ---------- robots fetch (guarded, no redirects) ----------

    async def _fetch_robots_text(self, robots_url: str) -> str | None:
        try:
            target = self._ssrf.resolve_pinned(robots_url)
        except ValueError:
            return None
        client = self._get_client()
        try:
            resp = await self._request_pinned(client, robots_url, target, 10.0)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        return resp.text

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(follow_redirects=False)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---------- helpers ----------

    def _empty(
        self,
        requested_url: str,
        fetched_at: datetime,
        status: CrawlStatus,
        final_url: str,
        http_status: int | None,
        redirect_chain: list[str],
    ) -> Document:
        return Document(
            url=final_url or requested_url,
            requested_url=requested_url,
            content_hash=_EMPTY_HASH,
            markdown="",
            enveloped_markdown="",
            content_type="",
            fetched_at=fetched_at,
            status=status,
            http_status=http_status,
            bytes_in=0,
            render_ms=0,
            extraction_ms=0,
            redirect_chain=redirect_chain,
        )
