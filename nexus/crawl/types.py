from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CrawlRequest(BaseModel):
    url: str
    render_js: bool = False
    timeout_s: float = Field(default=20.0, gt=0)
    max_bytes: int = Field(default=4_000_000, ge=1)
    respect_robots: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("url must not be empty")
        return cleaned


CrawlStatus = Literal[
    "ok",
    "blocked_by_ssrf_guard",
    "blocked_by_robots",
    "rate_limited",
    "timeout",
    "http_4xx",
    "http_5xx",
    "unsupported_content_type",
    "too_large",
    "extraction_failed",
]


class Document(BaseModel):
    url: str
    requested_url: str
    content_hash: str
    markdown: str
    content_type: str
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: CrawlStatus
    http_status: int | None
    bytes_in: int = Field(ge=0)
    render_ms: int = Field(ge=0)
    extraction_ms: int = Field(ge=0)
    redirect_chain: list[str] = Field(default_factory=list)
