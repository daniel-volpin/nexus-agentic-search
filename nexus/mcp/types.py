from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic


@dataclass(frozen=True)
class MCPConfig:
    token: str
    version: str = "0.1.0"
    reveal_cost: bool = True
    answer_text_max_bytes: int = 16_000
    max_citations: int = 32
    max_documents: int = 16
    input_json_max_bytes: int = 4_096

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("token must not be empty")


@dataclass
class StatusState:
    started_at: float = field(default_factory=monotonic)
    daily_cost_usd: float = 0.0
    requests_today: int = 0
