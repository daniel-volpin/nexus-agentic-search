from nexus.rerank import rerank
from nexus.search.types import Result


def test_rerank_applies_top_k_and_unique_urls() -> None:
    candidates = [
        Result(
            url="https://a.com/1?utm_source=x",
            title="python tips",
            snippet="learn python fast",
            engine="brave",
            rank=0,
        ),
        Result(
            url="https://a.com/1",
            title="python tips",
            snippet="learn python fast",
            engine="brave",
            rank=1,
        ),
        Result(
            url="https://b.com/2", title="cooking", snippet="best pasta", engine="brave", rank=2
        ),
    ]
    out = rerank("python", candidates, top_k=2, per_domain_cap=2)
    assert len(out) == 2
    assert out[0].rerank_rank == 0
    assert out[1].rerank_rank == 1
