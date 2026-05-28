from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict

# The finish reasons a provider can report, normalized to a closed set.
FinishReason = Literal["stop", "length", "tool_calls", "content_filter", "error"]


class Message(TypedDict):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ToolSpec(TypedDict):
    name: str
    description: str
    parameters: dict


class ToolCall(TypedDict):
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class CompletionResult:
    text: str
    finish_reason: FinishReason
    input_tokens: int
    output_tokens: int
    cost_usd: float
    tool_calls: list[ToolCall]
    model_id: str
    role: str
    fallback_used: bool = False
    model_drift: bool = False
    cost_authoritative: bool = True


@dataclass(frozen=True)
class StreamChunk:
    text_delta: str
    finish_reason: FinishReason | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model_id: str
    role: str
    fallback_used: bool = False
    model_drift: bool = False


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""


class LiteLLMBackend(Protocol):
    async def acompletion(self, **kwargs: object) -> ProviderResponse | AsyncIterator[dict]: ...

    def token_counter(self, *, model: str, messages: list[Message]) -> int: ...

    @property
    def pricing_table_version(self) -> str: ...
