"""Adversarial tests for budget / cost-cap enforcement (Spec 10 / Spec 05)."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from nexus.llm import (
    BudgetExceeded,
    InputTooLarge,
    LiteLLMClient,
    LLMConfig,
    LLMRoleConfig,
    Message,
    ProviderResponse,
    SynthesisToolsDisabled,
)

pytestmark = pytest.mark.security


# ---------- minimal fakes ----------


@dataclass
class _FakeResp:
    text: str = "ok"
    finish_reason: str = "stop"
    input_tokens: int = 1
    output_tokens: int = 1
    cost_usd: float = 0.0
    tool_calls: list[dict] | None = None
    model: str = "openai/gpt-4o-2024-11-20"


class _FakeBackend:
    def __init__(self, *, token_count: int, responses: list[_FakeResp]) -> None:
        self.token_count = token_count
        self.responses = list(responses)
        self.pricing_table_version = "test-pricing"

    async def acompletion(self, **kwargs: object) -> ProviderResponse | AsyncIterator[dict]:
        resp = self.responses.pop(0)
        return ProviderResponse(
            text=resp.text,
            finish_reason=resp.finish_reason,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cost_usd=resp.cost_usd,
            tool_calls=resp.tool_calls or [],
            model=resp.model,
        )

    def token_counter(self, *, model: str, messages: list[Message]) -> int:
        return self.token_count


def _config() -> LLMConfig:
    return LLMConfig(
        roles={
            "synthesis": LLMRoleConfig(
                primary="openai/gpt-4o-2024-11-20",
                fallback=[],
                max_input_tokens=32,
                max_output_tokens=8,
            )
        },
        daily_usd_budget=5.00,
        soft_budget_fraction=0.8,
        pricing_table_version="test-pricing",
    )


def _user_message(text: str = "hello") -> list[Message]:
    return [{"role": "user", "content": text}]


# ---------- per-request input cap ----------


async def test_input_too_large_raises_before_provider_call() -> None:
    """Over-cap input MUST raise BEFORE any backend call (don't waste
    a provider request)."""
    backend = _FakeBackend(token_count=10_000, responses=[_FakeResp()])
    client = LiteLLMClient(config=_config(), backend=backend)
    with pytest.raises(InputTooLarge):
        await client.complete(role="synthesis", messages=_user_message(), max_output_tokens=8)
    # The backend's responses list still has its entry — no call was made.
    assert len(backend.responses) == 1


# ---------- daily $ cap (hard) ----------


async def test_daily_budget_hard_cap_enforced() -> None:
    """Once the daily counter passes the cap, subsequent calls MUST
    raise BudgetExceeded BEFORE any provider call."""
    with tempfile.TemporaryDirectory() as tmpdir:
        budget_path = Path(tmpdir) / "budget.sqlite3"
        backend = _FakeBackend(
            token_count=1,
            responses=[_FakeResp(cost_usd=5.0)],
        )
        client = LiteLLMClient(config=_config(), backend=backend, budget_db_path=budget_path)

        first = await client.complete(
            role="synthesis", messages=_user_message(), max_output_tokens=8
        )
        assert first.cost_usd == 5.0

        with pytest.raises(BudgetExceeded):
            await client.complete(role="synthesis", messages=_user_message(), max_output_tokens=8)


async def test_budget_exceeded_does_not_call_backend() -> None:
    """The budget check is at the gateway boundary — backend is
    never invoked when over cap."""
    with tempfile.TemporaryDirectory() as tmpdir:
        budget_path = Path(tmpdir) / "budget.sqlite3"
        backend = _FakeBackend(
            token_count=1,
            responses=[_FakeResp(cost_usd=5.0), _FakeResp()],
        )
        client = LiteLLMClient(config=_config(), backend=backend, budget_db_path=budget_path)

        await client.complete(role="synthesis", messages=_user_message(), max_output_tokens=8)
        # Second call must fail BEFORE consuming the second fake response.
        with pytest.raises(BudgetExceeded):
            await client.complete(role="synthesis", messages=_user_message(), max_output_tokens=8)
        assert len(backend.responses) == 1, "backend was called after budget exhausted"


# ---------- per-day isolation across roles ----------


async def test_budget_is_tracked_per_role() -> None:
    """One role exhausting its budget MUST NOT affect another role.
    Spec 05: budget is per-role."""
    with tempfile.TemporaryDirectory() as tmpdir:
        budget_path = Path(tmpdir) / "budget.sqlite3"
        config = LLMConfig(
            roles={
                "synthesis": LLMRoleConfig(
                    primary="openai/gpt-4o-2024-11-20",
                    fallback=[],
                    max_input_tokens=32,
                    max_output_tokens=8,
                ),
                "rerank-decision": LLMRoleConfig(
                    primary="openai/gpt-4o-mini-2024-07-18",
                    fallback=[],
                    max_input_tokens=32,
                    max_output_tokens=8,
                ),
            },
            daily_usd_budget=5.00,
            soft_budget_fraction=0.8,
            pricing_table_version="test-pricing",
        )
        backend = _FakeBackend(
            token_count=1,
            responses=[
                _FakeResp(cost_usd=5.0),  # exhausts synthesis
                _FakeResp(cost_usd=0.1, model="openai/gpt-4o-mini-2024-07-18"),
            ],
        )
        client = LiteLLMClient(config=config, backend=backend, budget_db_path=budget_path)

        await client.complete(role="synthesis", messages=_user_message(), max_output_tokens=8)
        # The other role should still succeed.
        await client.complete(role="rerank-decision", messages=_user_message(), max_output_tokens=8)


# ---------- synthesis tools-disabled ----------


async def test_synthesis_role_rejects_tools_at_api_boundary() -> None:
    """Spec 10 §Synthesis-role hardening: passing tools=[...] for
    role='synthesis' MUST raise — not silently strip, not just at the
    prompt level."""
    backend = _FakeBackend(token_count=1, responses=[_FakeResp()])
    client = LiteLLMClient(config=_config(), backend=backend)

    with pytest.raises(SynthesisToolsDisabled):
        await client.complete(
            role="synthesis",
            messages=_user_message(),
            max_output_tokens=8,
            tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
        )


async def test_synthesis_role_rejects_tools_before_input_token_check() -> None:
    """The tool-disabled check fires before any other validation —
    a request with both oversized input AND tools must surface
    SynthesisToolsDisabled, not InputTooLarge."""
    backend = _FakeBackend(token_count=10_000, responses=[_FakeResp()])
    client = LiteLLMClient(config=_config(), backend=backend)
    with pytest.raises(SynthesisToolsDisabled):
        await client.complete(
            role="synthesis",
            messages=_user_message(),
            max_output_tokens=8,
            tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
        )


async def test_non_synthesis_roles_may_still_use_tools() -> None:
    """The boundary check only fires for role='synthesis'. Other roles
    (query-expansion, rerank-decision) may still pass tools — that's a
    design choice, not a security invariant."""
    config = LLMConfig(
        roles={
            "rerank-decision": LLMRoleConfig(
                primary="openai/gpt-4o-mini-2024-07-18",
                fallback=[],
                max_input_tokens=32,
                max_output_tokens=8,
            )
        },
        daily_usd_budget=5.0,
        soft_budget_fraction=0.8,
        pricing_table_version="test-pricing",
    )
    backend = _FakeBackend(
        token_count=1,
        responses=[_FakeResp(model="openai/gpt-4o-mini-2024-07-18")],
    )
    client = LiteLLMClient(config=config, backend=backend)
    # Must not raise.
    await client.complete(
        role="rerank-decision",
        messages=_user_message(),
        max_output_tokens=8,
        tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
    )
