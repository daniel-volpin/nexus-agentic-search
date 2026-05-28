"""robots.txt evaluation.

Fetches ``/robots.txt`` for a host (through the same caller-supplied,
SSRF-guarded fetch function), parses it with the stdlib
``urllib.robotparser`` (no new dependency), and caches the parser per
host for ``ttl_s``.

Policy:
- Disallowed path → caller returns ``blocked_by_robots``.
- robots fetch failure → DEFAULT ALLOW (with a logged warning). Many
  sites have no robots.txt; failing closed would block the whole web.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)

_DEFAULT_TTL_S = 24 * 3600


class _Entry:
    __slots__ = ("expires_at", "parser")

    def __init__(self, parser: RobotFileParser | None, expires_at: float) -> None:
        self.parser = parser
        self.expires_at = expires_at


class RobotsCache:
    """Per-host robots.txt cache. ``robots_text_fetcher`` is an async
    callable ``(robots_url) -> str | None`` that returns the body or
    None on any failure; the cache treats None as default-allow."""

    def __init__(
        self,
        *,
        user_agent: str,
        ttl_s: int = _DEFAULT_TTL_S,
    ) -> None:
        self._user_agent = user_agent
        self._ttl_s = ttl_s
        self._cache: dict[str, _Entry] = {}

    async def allowed(self, url: str, robots_text_fetcher) -> bool:
        parts = urlsplit(url)
        host_key = f"{parts.scheme}://{parts.netloc}"
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"

        entry = self._cache.get(host_key)
        now = time.time()
        if entry is None or entry.expires_at <= now:
            entry = await self._refresh(host_key, robots_text_fetcher, now)

        if entry.parser is None:
            return True  # default-allow on fetch failure
        return entry.parser.can_fetch(self._user_agent, path)

    async def crawl_delay(self, url: str) -> float | None:
        parts = urlsplit(url)
        host_key = f"{parts.scheme}://{parts.netloc}"
        entry = self._cache.get(host_key)
        if entry is None or entry.parser is None:
            return None
        delay = entry.parser.crawl_delay(self._user_agent)
        return float(delay) if delay is not None else None

    async def _refresh(self, host_key: str, fetcher, now: float) -> _Entry:
        robots_url = f"{host_key}/robots.txt"
        text: str | None
        try:
            text = await fetcher(robots_url)
        except Exception as exc:
            logger.warning("robots_fetch_failed", extra={"host": host_key, "reason": str(exc)})
            text = None

        parser: RobotFileParser | None
        if text is None:
            parser = None
        else:
            parser = RobotFileParser()
            parser.parse(text.splitlines())

        entry = _Entry(parser=parser, expires_at=now + self._ttl_s)
        self._cache[host_key] = entry
        return entry
