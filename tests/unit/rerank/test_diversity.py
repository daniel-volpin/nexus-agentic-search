from nexus.rerank.diversity import apply_per_domain_cap
from nexus.search.types import RankedResult, Result


def _row(url: str, score: float) -> RankedResult:
    return RankedResult(
        result=Result(url=url, title="t", snippet="s", engine="brave", rank=0),
        score=score,
        rerank_rank=0,
    )


def test_per_domain_cap() -> None:
    rows = [
        _row("https://a.com/1", 0.9),
        _row("https://a.com/2", 0.8),
        _row("https://b.com/1", 0.7),
    ]
    kept = apply_per_domain_cap(rows, cap=1)
    assert [r.result.url for r in kept] == ["https://a.com/1", "https://b.com/1"]
