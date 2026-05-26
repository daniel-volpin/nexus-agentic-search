from __future__ import annotations

from nexus.search.canonical import canonicalize
from nexus.search.types import RankedResult, Result

from .bge import BgeScorer
from .dedup import drop_near_duplicates
from .diversity import apply_per_domain_cap



def rerank(
    query: str,
    candidates: list[Result],
    *,
    top_k: int = 8,
    per_domain_cap: int = 2,
    scorer: BgeScorer | None = None,
    max_candidates: int = 30,
) -> list[RankedResult]:
    if not candidates or top_k <= 0:
        return []

    scorer = scorer or BgeScorer()
    bounded = candidates[:max_candidates]
    texts = [f"{c.title[:256]}\n{c.snippet[:1024]}" for c in bounded]
    scores = scorer.score([(query, text) for text in texts])

    ranked = sorted(
        (RankedResult(result=item, score=score, rerank_rank=idx) for idx, (item, score) in enumerate(zip(bounded, scores, strict=False))),
        key=lambda x: x.score,
        reverse=True,
    )

    unique_urls: set[str] = set()
    unique_ranked: list[RankedResult] = []
    for row in ranked:
        key = canonicalize(row.result.url)
        if key in unique_urls:
            continue
        unique_urls.add(key)
        unique_ranked.append(row)

    diversified = apply_per_domain_cap(unique_ranked, cap=per_domain_cap)
    deduped = drop_near_duplicates(diversified)
    final = deduped[:top_k]

    for idx, row in enumerate(final):
        row.rerank_rank = idx
    return final


__all__ = ["BgeScorer", "rerank"]
