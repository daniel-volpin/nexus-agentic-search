from nexus.rerank.dedup import near_duplicate_jaccard


def test_near_duplicate_high_similarity() -> None:
    a = "Best pizza in NYC\nTry these top slices"
    b = "best pizza in nyc\ntry these top slices now"
    assert near_duplicate_jaccard(a, b) >= 0.85
