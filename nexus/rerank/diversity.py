from __future__ import annotations

from collections import Counter
from urllib.parse import urlparse

from nexus.search.types import RankedResult


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def apply_per_domain_cap(items: list[RankedResult], cap: int) -> list[RankedResult]:
    if cap < 1:
        return []
    counts: Counter[str] = Counter()
    kept: list[RankedResult] = []
    for item in items:
        domain = _domain(item.result.url)
        if counts[domain] >= cap:
            continue
        counts[domain] += 1
        kept.append(item)
    return kept
