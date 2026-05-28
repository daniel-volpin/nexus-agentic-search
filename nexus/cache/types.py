"""Cache layer types and exceptions (Spec 09)."""

from __future__ import annotations

from typing import Final


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
# performed (Spec 09 §Failure modes).
SEARCH_BRAVE_VERSION: Final[int] = 1
SEARCH_SEARXNG_VERSION: Final[int] = 1
RERANK_BGE_VERSION: Final[int] = 1
CRAWL_DOCUMENT_VERSION: Final[int] = 1
CRAWL_ROBOTS_VERSION: Final[int] = 1
COST_DAILY_VERSION: Final[int] = 1
