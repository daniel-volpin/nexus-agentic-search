"""Module-level namespace handles populated by :func:`setup_cache`.

Spec 09 §Namespaces. Consumers import the constants and call ``.get`` /
``.set`` on them. Before :func:`setup_cache` runs the handles are ``None``
and callers must treat that as "cache disabled" — guard with a truthiness
check.

The single-process module-global pattern is acceptable here because the
service runs as one process per container and cache lifetime matches
process lifetime.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .diskcache_backend import DiskCacheBackend
from .types import (
    COST_DAILY_VERSION,
    CRAWL_DOCUMENT_VERSION,
    CRAWL_ROBOTS_VERSION,
    RERANK_BGE_VERSION,
    SEARCH_BRAVE_VERSION,
    SEARCH_SEARXNG_VERSION,
)

logger = logging.getLogger(__name__)

SEARCH_BRAVE: DiskCacheBackend | None = None
SEARCH_SEARXNG: DiskCacheBackend | None = None
RERANK_BGE: DiskCacheBackend | None = None
CRAWL_DOCUMENT: DiskCacheBackend | None = None
CRAWL_ROBOTS: DiskCacheBackend | None = None
COST_DAILY: DiskCacheBackend | None = None

# TTL defaults per namespace (Spec 09 §Namespaces table).
_TTL_SEARCH_BRAVE_S = 6 * 3600
_TTL_SEARCH_SEARXNG_S = 3 * 3600
_TTL_RERANK_BGE_S = 24 * 3600
_TTL_CRAWL_DOCUMENT_S = 24 * 3600
_TTL_CRAWL_ROBOTS_S = 24 * 3600
_TTL_COST_DAILY_S = 48 * 3600

_NAMESPACE_COUNT = 6


def setup_cache(
    root: Path,
    *,
    total_size_gb: float = 2.0,
) -> None:
    """Create the per-namespace caches under ``root``.

    Idempotent: subsequent calls replace the handles. Tests rely on this.
    On any backend failure the affected handle is ``None`` and the
    namespace runs in cache-disabled mode — callers fall through to the
    source of truth.
    """
    global SEARCH_BRAVE, SEARCH_SEARXNG, RERANK_BGE
    global CRAWL_DOCUMENT, CRAWL_ROBOTS, COST_DAILY

    bytes_per_namespace = int((total_size_gb * 1024**3) / _NAMESPACE_COUNT)

    SEARCH_BRAVE = DiskCacheBackend(
        root=root,
        namespace="search.brave",
        version=SEARCH_BRAVE_VERSION,
        ttl_default_s=_TTL_SEARCH_BRAVE_S,
        size_limit_bytes=bytes_per_namespace,
    )
    SEARCH_SEARXNG = DiskCacheBackend(
        root=root,
        namespace="search.searxng",
        version=SEARCH_SEARXNG_VERSION,
        ttl_default_s=_TTL_SEARCH_SEARXNG_S,
        size_limit_bytes=bytes_per_namespace,
    )
    RERANK_BGE = DiskCacheBackend(
        root=root,
        namespace="rerank.bge",
        version=RERANK_BGE_VERSION,
        ttl_default_s=_TTL_RERANK_BGE_S,
        size_limit_bytes=bytes_per_namespace,
    )
    CRAWL_DOCUMENT = DiskCacheBackend(
        root=root,
        namespace="crawl.document",
        version=CRAWL_DOCUMENT_VERSION,
        ttl_default_s=_TTL_CRAWL_DOCUMENT_S,
        size_limit_bytes=bytes_per_namespace,
    )
    CRAWL_ROBOTS = DiskCacheBackend(
        root=root,
        namespace="crawl.robots",
        version=CRAWL_ROBOTS_VERSION,
        ttl_default_s=_TTL_CRAWL_ROBOTS_S,
        size_limit_bytes=bytes_per_namespace,
    )
    COST_DAILY = DiskCacheBackend(
        root=root,
        namespace="cost.daily",
        version=COST_DAILY_VERSION,
        ttl_default_s=_TTL_COST_DAILY_S,
        size_limit_bytes=bytes_per_namespace,
    )

    disabled = [
        b.namespace
        for b in (
            SEARCH_BRAVE,
            SEARCH_SEARXNG,
            RERANK_BGE,
            CRAWL_DOCUMENT,
            CRAWL_ROBOTS,
            COST_DAILY,
        )
        if not b.enabled
    ]
    if disabled:
        logger.warning("cache_setup_partial", extra={"disabled_namespaces": disabled})


def shutdown_cache() -> None:
    """Close all open caches. Idempotent."""
    global SEARCH_BRAVE, SEARCH_SEARXNG, RERANK_BGE
    global CRAWL_DOCUMENT, CRAWL_ROBOTS, COST_DAILY

    for backend in (
        SEARCH_BRAVE,
        SEARCH_SEARXNG,
        RERANK_BGE,
        CRAWL_DOCUMENT,
        CRAWL_ROBOTS,
        COST_DAILY,
    ):
        if backend is not None:
            backend.close()

    SEARCH_BRAVE = None
    SEARCH_SEARXNG = None
    RERANK_BGE = None
    CRAWL_DOCUMENT = None
    CRAWL_ROBOTS = None
    COST_DAILY = None
