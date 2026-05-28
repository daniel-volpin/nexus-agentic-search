from __future__ import annotations

import math


class BgeScorer:
    """Deterministic lexical scorer (query/candidate token overlap).

    A real, explainable default — not random — so ranking is stable
    without a heavyweight model. The interface matches a cross-encoder
    so a real one (e.g. ``BAAI/bge-reranker-v2-m3``, multilingual,
    Apache-2.0) can be dropped in behind ``model_id`` without API churn.
    """

    DEFAULT_MODEL_ID = "BAAI/bge-reranker-v2-m3"

    def __init__(
        self, model_path: str | None = None, device: str = "cpu", model_id: str | None = None
    ) -> None:
        self.model_path = model_path
        self.device = device
        self.model_id = model_id or self.DEFAULT_MODEL_ID

    def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        logits = [self._logit(query, text) for query, text in pairs]
        return [1.0 / (1.0 + math.exp(-x)) for x in logits]

    @staticmethod
    def _logit(query: str, text: str) -> float:
        q = set(query.lower().split())
        t = set(text.lower().split())
        if not q or not t:
            return -5.0
        overlap = len(q & t)
        return float(overlap) - 1.0
