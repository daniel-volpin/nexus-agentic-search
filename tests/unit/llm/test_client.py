from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
import tempfile
import logging

import pytest

from nexus.llm import (
    BudgetExceeded,
    InMemoryTelemetrySink,
    InputTooLarge,
    LLMConfig,
    LLMRoleConfig,
    LiteLLMClient,
    Message,
    ProviderResponse,
    _redact_secrets,
)


@dataclass
class FakeProviderResponse:
    text: str
    finish_reason: str = "stop"
    input_tokens: int = 10
    output_tokens: int = 5
    cost_usd: float = 0.25
    tool_calls: list[dict] | None = None
    model: str = "openai/gpt-4o-2024-11-20"
    chunks: list[dict] | None = None


class FakeLiteLLMBackend:
    def __init__(
        self,
        *,
        token_count: int = 0,
        responses: list[FakeProviderResponse | Exception] | None = None,
    ) -> None:
        self.token_count = token_count
        self.responses = list(responses or [])
        self.calls: list[dict] = []
        self.stream_calls: list[dict] = []
        self.pricing_table_version = "test-pricing"

    async def acompletion(self, **kwargs: object) -> ProviderResponse | AsyncIterator[dict]:
        if kwargs.get("stream"):
            self.stream_calls.append(kwargs)
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return self._stream(response)

        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return ProviderResponse(
            text=response.text,
            finish_reason=response.finish_reason,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=response.cost_usd,
            tool_calls=response.tool_calls or [],
            model=response.model,
        )

    def token_counter(self, *, model: str, messages: list[Message]) -> int:
        return self.token_count

    async def _stream(self, response: FakeProviderResponse) -> AsyncIterator[dict]:
        for chunk in response.chunks or []:
            yield chunk


def make_config() -> LLMConfig:
    return LLMConfig(
        roles={
            "synthesis": LLMRoleConfig(
                primary="openai/gpt-4o-2024-11-20",
                fallback=["anthropic/claude-sonnet-4-5-20250929"],
                max_input_tokens=32,
                max_output_tokens=8,
            )
        },
        daily_usd_budget=10.0,
        soft_budget_fraction=0.8,
        pricing_table_version="test-pricing",
    )


def test_role_config_rejects_alias_model_ids() -> None:
    with pytest.raises(ValueError):
        LLMRoleConfig(
            primary="openai/gpt-4o",
            fallback=[],
            max_input_tokens=32,
            max_output_tokens=8,
        )


def test_redact_secrets_masks_known_patterns() -> None:
    text = "OPENAI=sk-abcdefghijklmnopqrstuvwxyz12345 Authorization: Bearer xyz"
    redacted = _redact_secrets(text)
    assert "sk-" not in redacted
    assert "Bearer xyz" not in redacted
    assert redacted.count("[REDACTED]") == 2


def test_complete_raises_input_too_large() -> None:
    backend = FakeLiteLLMBackend(token_count=64)
    client = LiteLLMClient(config=make_config(), backend=backend)

    with pytest.raises(InputTooLarge):
        asyncio.run(
            client.complete(
                role="synthesis",
                messages=[{"role": "user", "content": "hello"}],
                max_output_tokens=8,
            )
        )


def test_complete_falls_back_after_provider_error() -> None:
    backend = FakeLiteLLMBackend(
        token_count=4,
        responses=[
            RuntimeError("primary failed"),
            FakeProviderResponse(
                text="ok",
                model="anthropic/claude-sonnet-4-5-20250929",
            ),
        ],
    )
    client = LiteLLMClient(config=make_config(), backend=backend)

    result = asyncio.run(
        client.complete(
            role="synthesis",
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=8,
        )
    )

    assert result.text == "ok"
    assert result.model_id == "anthropic/claude-sonnet-4-5-20250929"
    assert result.fallback_used is True
    assert len(backend.calls) == 2


def test_complete_marks_model_drift_when_provider_returns_different_model() -> None:
    backend = FakeLiteLLMBackend(
        token_count=4,
        responses=[FakeProviderResponse(text="ok", model="openai/gpt-4o-mini-2024-07-18")],
    )
    client = LiteLLMClient(config=make_config(), backend=backend)

    result = asyncio.run(
        client.complete(
            role="synthesis",
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=8,
        )
    )

    assert result.model_drift is True
    assert result.model_id == "openai/gpt-4o-mini-2024-07-18"


def test_complete_raises_budget_exceeded_after_hard_cap() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        backend = FakeLiteLLMBackend(
            token_count=4,
            responses=[FakeProviderResponse(text="ok", cost_usd=10.0)],
        )
        client = LiteLLMClient(
            config=make_config(),
            backend=backend,
            budget_db_path=Path(tmpdir) / "budget.sqlite3",
        )

        first = asyncio.run(
            client.complete(
                role="synthesis",
                messages=[{"role": "user", "content": "hello"}],
                max_output_tokens=8,
            )
        )
        assert first.cost_usd == 10.0

        with pytest.raises(BudgetExceeded):
            asyncio.run(
                client.complete(
                    role="synthesis",
                    messages=[{"role": "user", "content": "hello again"}],
                    max_output_tokens=8,
                )
            )


def test_complete_logs_soft_budget_warning(caplog: pytest.LogCaptureFixture) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        backend = FakeLiteLLMBackend(
            token_count=4,
            responses=[FakeProviderResponse(text="ok", cost_usd=8.0)],
        )
        client = LiteLLMClient(
            config=make_config(),
            backend=backend,
            budget_db_path=Path(tmpdir) / "budget.sqlite3",
        )

        with caplog.at_level(logging.WARNING):
            asyncio.run(
                client.complete(
                    role="synthesis",
                    messages=[{"role": "user", "content": "hello"}],
                    max_output_tokens=8,
                )
            )

        assert "soft budget" in caplog.text


def test_complete_marks_cost_non_authoritative_on_pricing_drift(caplog: pytest.LogCaptureFixture) -> None:
    backend = FakeLiteLLMBackend(
        token_count=4,
        responses=[FakeProviderResponse(text="ok")],
    )
    backend.pricing_table_version = "different-pricing"
    client = LiteLLMClient(config=make_config(), backend=backend)

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(
            client.complete(
                role="synthesis",
                messages=[{"role": "user", "content": "hello"}],
                max_output_tokens=8,
            )
        )

    assert result.cost_authoritative is False
    assert "pricing table drift" in caplog.text


def test_complete_repairs_malformed_tool_call_json_once() -> None:
    backend = FakeLiteLLMBackend(
        token_count=4,
        responses=[
            FakeProviderResponse(
                text="",
                finish_reason="tool_calls",
                tool_calls=[{"id": "1", "name": "search", "arguments": {"raw": '{"query":'}}],
            ),
            FakeProviderResponse(
                text="",
                finish_reason="tool_calls",
                tool_calls=[{"id": "1", "name": "search", "arguments": {"query": "python"}}],
            ),
        ],
    )
    client = LiteLLMClient(config=make_config(), backend=backend)

    result = asyncio.run(
        client.complete(
            role="synthesis",
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=8,
        )
    )

    assert result.finish_reason == "tool_calls"
    assert result.tool_calls == [{"id": "1", "name": "search", "arguments": {"query": "python"}}]
    assert len(backend.calls) == 2
    assert backend.calls[1]["messages"][0]["role"] == "system"


def test_complete_returns_error_when_tool_call_repair_fails() -> None:
    backend = FakeLiteLLMBackend(
        token_count=4,
        responses=[
            FakeProviderResponse(
                text="",
                finish_reason="tool_calls",
                tool_calls=[{"id": "1", "name": "search", "arguments": {"raw": '{"query":'}}],
            ),
            FakeProviderResponse(
                text="",
                finish_reason="tool_calls",
                tool_calls=[{"id": "1", "name": "search", "arguments": {"raw": '{"query":'}}],
            ),
        ],
    )
    client = LiteLLMClient(config=make_config(), backend=backend)

    result = asyncio.run(
        client.complete(
            role="synthesis",
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=8,
        )
    )

    assert result.finish_reason == "error"
    assert result.tool_calls == []


def test_stream_complete_accumulates_usage_and_text() -> None:
    backend = FakeLiteLLMBackend(
        token_count=4,
        responses=[
            FakeProviderResponse(
                text="Hello world",
                model="openai/gpt-4o-2024-11-20",
                chunks=[
                    {
                        "choices": [{"delta": {"content": "Hello "}, "finish_reason": None}],
                        "model": "openai/gpt-4o-2024-11-20",
                    },
                    {
                        "choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                        "_response_cost": 0.1,
                        "model": "openai/gpt-4o-2024-11-20",
                    },
                ],
            )
        ],
    )
    client = LiteLLMClient(config=make_config(), backend=backend)

    async def collect() -> list[object]:
        rows = []
        async for item in client.stream_complete(
            role="synthesis",
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=8,
        ):
            rows.append(item)
        return rows

    chunks = asyncio.run(collect())

    assert [chunk.text_delta for chunk in chunks] == ["Hello ", "world"]
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].input_tokens == 4
    assert chunks[-1].output_tokens == 2
    assert chunks[-1].cost_usd == 0.1
    assert backend.stream_calls[0]["stream_options"] == {"include_usage": True}


def test_complete_emits_telemetry_contract() -> None:
    backend = FakeLiteLLMBackend(
        token_count=4,
        responses=[FakeProviderResponse(text="ok", input_tokens=4, output_tokens=2, cost_usd=0.1)],
    )
    telemetry = InMemoryTelemetrySink()
    client = LiteLLMClient(config=make_config(), backend=backend, telemetry=telemetry)

    result = asyncio.run(
        client.complete(
            role="synthesis",
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=8,
        )
    )

    assert result.text == "ok"
    assert telemetry.spans[0][0] == "llm.synthesis"
    assert telemetry.spans[0][1]["model_id_resolved"] == "openai/gpt-4o-2024-11-20"
    assert ("llm_input_tokens_total", 4, {"role": "synthesis", "model": "openai/gpt-4o-2024-11-20"}) in telemetry.counters
    assert ("llm_output_tokens_total", 2, {"role": "synthesis", "model": "openai/gpt-4o-2024-11-20"}) in telemetry.counters
    assert ("llm_cost_usd_total", 0.1, {"role": "synthesis"}) in telemetry.counters
    assert telemetry.gauges[-1][0] == "llm_budget_remaining_usd"


def test_budget_exhaustion_emits_error_telemetry() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        backend = FakeLiteLLMBackend(token_count=4, responses=[FakeProviderResponse(text="ok", cost_usd=10.0)])
        telemetry = InMemoryTelemetrySink()
        client = LiteLLMClient(
            config=make_config(),
            backend=backend,
            budget_db_path=Path(tmpdir) / "budget.sqlite3",
            telemetry=telemetry,
        )
        asyncio.run(
            client.complete(
                role="synthesis",
                messages=[{"role": "user", "content": "hello"}],
                max_output_tokens=8,
            )
        )

        with pytest.raises(BudgetExceeded):
            asyncio.run(
                client.complete(
                    role="synthesis",
                    messages=[{"role": "user", "content": "again"}],
                    max_output_tokens=8,
                )
            )

        assert ("llm_errors_total", 1, {"role": "synthesis", "reason": "budget_exhausted"}) in telemetry.counters
