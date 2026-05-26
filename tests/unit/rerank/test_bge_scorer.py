from nexus.rerank.bge import BgeScorer


def test_scores_in_range() -> None:
    scorer = BgeScorer()
    scores = scorer.score([("python tutorial", "python tutorial for beginners")])
    assert len(scores) == 1
    assert 0.0 <= scores[0] <= 1.0
