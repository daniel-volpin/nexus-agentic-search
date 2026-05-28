"""Canonical cache key builders (Spec 09 §Invariants).

Every cache key is a hex digest (or hex+small int / ISO-date+role for
``cost_daily``) — NEVER raw query / URL / token plaintext. This keeps
queries out of disk-stored cache keys even if the cache directory is
later exposed via a host backup.

Keys are stable across Python versions: SHA-256 over a documented
canonical-form string. Same inputs ⇒ same key, deterministic.
"""

from __future__ import annotations

import hashlib
from datetime import date


def _sha256_hex(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def search_key(
    *,
    query: str,
    freshness: str,
    max_results: int,
    lang: str | None,
    country: str | None,
) -> str:
    """Key for search-provider responses (Brave / SearXNG)."""
    canonical = f"q={query}|f={freshness}|n={max_results}|lang={lang or ''}|c={country or ''}"
    return _sha256_hex(canonical)


def rerank_key(*, query: str, canonical_urls: list[str]) -> str:
    """Key for rerank outputs. URLs sorted so caller order does not vary key."""
    sorted_urls = "|".join(sorted(canonical_urls))
    canonical = f"q={query}|urls={sorted_urls}"
    return _sha256_hex(canonical)


def crawl_doc_key(*, canonical_url: str, render_js: bool, max_bytes: int) -> str:
    """Key for crawl documents."""
    canonical = f"u={canonical_url}|js={int(render_js)}|max={max_bytes}"
    return _sha256_hex(canonical)


def robots_key(*, host: str) -> str:
    """Key for robots.txt evaluations. Host lower-cased."""
    return _sha256_hex(f"host={host.lower()}")


def cost_daily_key(*, role: str, day: date) -> str:
    """Key for the per-day per-role cost counter.

    Plaintext-safe: role identifiers and ISO dates contain no user data.
    Kept plaintext so operators can inspect today's counter directly.
    """
    return f"{day.isoformat()}|{role}"
