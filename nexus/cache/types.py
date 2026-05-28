"""Cache layer types and exceptions."""

from __future__ import annotations

from typing import Any, Final, Protocol


class CacheLike(Protocol):
    """Minimal async cache surface consumed by components (search, crawl).

    ``DiskCacheBackend`` satisfies this. Components depend on the
    protocol, not the concrete backend, and accept ``None`` to mean
    "no cache" — best-effort, never a hard dependency.
    """

    async def get(self, key: str) -> Any | None: ...

    async def set(self, key: str, value: Any, *, ttl_s: int | None = None) -> None: ...


class CacheError(Exception):
    """Base cache error.

    Internal: the cache layer is best-effort. Most failures are converted to
    misses and never propagate. ``CacheError`` is raised only when a caller
    explicitly requires a side effect to succeed (currently only ``incr``,
    used by the cost-daily counter).
    """


class CacheDisabled(CacheError):
    """Raised when a side-effect operation is attempted on a disabled cache."""


# Per-namespace schema versions. Bumping a version invalidates all existing
# entries in that namespace on read (mismatch ⇒ miss). Migrations are NOT
# performed.
SEARCH_BRAVE_VERSION: Final[int] = 1
SEARCH_SEARXNG_VERSION: Final[int] = 1
RERANK_BGE_VERSION: Final[int] = 1
CRAWL_DOCUMENT_VERSION: Final[int] = 1
CRAWL_ROBOTS_VERSION: Final[int] = 1
COST_DAILY_VERSION: Final[int] = 1
