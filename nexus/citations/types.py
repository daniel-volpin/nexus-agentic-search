from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RawCitation(BaseModel):
    url: str
    content_hash: str
    quote: str
    claim_id: str


class Citation(BaseModel):
    url: str
    content_hash: str
    byte_start: int = Field(ge=0)
    byte_end: int = Field(ge=1)
    quote: str
    claim_id: str


CitationRejectionReason = Literal[
    "unknown_document",
    "quote_not_found",
    "quote_too_short",
    "quote_too_long",
    "claim_unmatched",
    "envelope_violation",
]


class CitationRejection(BaseModel):
    raw: RawCitation
    reason: CitationRejectionReason
