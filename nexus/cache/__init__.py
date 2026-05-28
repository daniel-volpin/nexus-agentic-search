"""Cache layer.

Per-namespace disk-backed caches with schema versioning and TTL.

Components consume cache via the module-level namespace handles
populated by :func:`setup_cache`. Failures fall through to a miss /
silent skip — the cache MUST NOT fail a user request.

Public surface intentionally small:

- :class:`DiskCacheBackend` — the implementation type.
- :class:`CacheError`, :class:`CacheDisabled` — exceptions.
- :func:`setup_cache`, :func:`shutdown_cache` — lifecycle.
- :mod:`nexus.cache.keys` — canonical key builders.
- :mod:`nexus.cache.namespaces` — module-level handles.
"""

from .diskcache_backend import DiskCacheBackend
from .namespaces import setup_cache, shutdown_cache
from .types import (
    COST_DAILY_VERSION,
    CRAWL_DOCUMENT_VERSION,
    CRAWL_ROBOTS_VERSION,
    RERANK_BGE_VERSION,
    SEARCH_BRAVE_VERSION,
    SEARCH_SEARXNG_VERSION,
    CacheDisabled,
    CacheError,
    CacheLike,
)

__all__ = [
    "COST_DAILY_VERSION",
    "CRAWL_DOCUMENT_VERSION",
    "CRAWL_ROBOTS_VERSION",
    "RERANK_BGE_VERSION",
    "SEARCH_BRAVE_VERSION",
    "SEARCH_SEARXNG_VERSION",
    "CacheDisabled",
    "CacheError",
    "CacheLike",
    "DiskCacheBackend",
    "setup_cache",
    "shutdown_cache",
]
