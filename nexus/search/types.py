from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ValidationError(ValueError):
    """Input failed contract validation."""


class SearchUnavailable(RuntimeError):
    """All search providers were unavailable."""


class SearchRequest(BaseModel):
    query: str
    freshness: Literal["any", "day", "week", "month", "year"] = "any"
    max_results: int = Field(default=20, ge=1, le=50)
    lang: str | None = None
    country: str | None = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        cleaned = "".join(ch for ch in value if ch == "\t" or ord(ch) >= 0x20)
        if not cleaned.strip():
            raise ValidationError("query must not be empty or whitespace")
        if len(cleaned) > 512:
            raise ValidationError("query must be <= 512 chars")
        return cleaned


class Result(BaseModel):
    url: str
    title: str
    snippet: str
    engine: Literal["brave", "searxng:google", "searxng:duckduckgo"]
    rank: int = Field(ge=0)
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RankedResult(BaseModel):
    result: Result
    score: float = Field(ge=0.0, le=1.0)
    rerank_rank: int = Field(ge=0)


class SearchResponse(BaseModel):
    results: list[Result] = Field(default_factory=list)
    provider: str
    query_sent: str
    latency_ms: int = Field(ge=0)
