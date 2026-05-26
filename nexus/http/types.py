from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HTTPConfig:
    token: str
    version: str = "0.1.0"
    reveal_cost: bool = True
    body_limit_bytes: int = 4_096
    token_rate_limit_per_minute: int = 30
    ip_rate_limit_per_minute: int = 60
    max_concurrent_per_token: int = 5

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("token must not be empty")
