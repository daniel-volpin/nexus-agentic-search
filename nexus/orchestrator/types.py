from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

AnswerStage = Literal[
    "accepted",
    "expanded",
    "searched",
    "ranked",
    "page_ready",
    "synthesized",
    "validated",
    "answer",
    "error",
]


@dataclass(frozen=True)
class AnswerEvent:
    stage: AnswerStage
    payload: dict
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class OrchestratorConfig:
    enable_query_expansion: bool = False
    crawl_concurrency: int = 4
    crawl_pages_max: int = 8
    wall_clock_s: float = 60.0
    llm_input_tokens: int = 32_000
    llm_output_tokens: int = 2_000
