from __future__ import annotations

import math


class BgeScorer:
    """Placeholder deterministic scorer for Spec 02 scaffolding."""

    def __init__(self, model_path: str | None = None, device: str = "cpu") -> None:
        self.model_path = model_path
        self.device = device

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
