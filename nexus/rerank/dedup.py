from __future__ import annotations

import re
import unicodedata

from nexus.search.types import RankedResult

_TOKEN_RE = re.compile(r"\w+")


def _normalize(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return _TOKEN_RE.findall(stripped.lower())


def near_duplicate_jaccard(a: str, b: str) -> float:
    a_tokens = set(_normalize(a))
    b_tokens = set(_normalize(b))
    if not a_tokens and not b_tokens:
        return 1.0
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


def drop_near_duplicates(items: list[RankedResult], threshold: float = 0.85) -> list[RankedResult]:
    kept: list[RankedResult] = []
    seen_texts: list[str] = []
    for item in items:
        text = f"{item.result.title}\n{item.result.snippet}".strip()
        if any(near_duplicate_jaccard(text, prior) >= threshold for prior in seen_texts):
            continue
        kept.append(item)
        seen_texts.append(text)
    return kept
