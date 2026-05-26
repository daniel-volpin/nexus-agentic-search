from __future__ import annotations

import math


class BgeScorer:
    """Placeholder deterministic scorer for Spec 02 scaffolding.

    Notes:
    - Runtime target is an open-weight cross-encoder backend.
    - Default candidate model is `BAAI/bge-reranker-v2-m3` (multilingual, Apache-2.0).
    - `model_id` is kept now so integration can swap in real inference without API churn.
    """

    DEFAULT_MODEL_ID = "BAAI/bge-reranker-v2-m3"

    def __init__(self, model_path: str | None = None, device: str = "cpu", model_id: str | None = None) -> None:
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
