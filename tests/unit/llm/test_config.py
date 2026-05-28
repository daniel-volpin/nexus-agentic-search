from __future__ import annotations

from nexus.llm import LLMConfig


def test_load_config_from_toml() -> None:
    config = LLMConfig.from_toml(
        """
        daily_usd_budget = 10.0
        pricing_table_version = "2026-05-26"

        [role.synthesis]
        primary = "openai/gpt-4o-2024-11-20"
        fallback = ["anthropic/claude-sonnet-4-5-20250929"]
        max_input_tokens = 32000
        max_output_tokens = 2000
        """
    )

    assert config.daily_usd_budget == 10.0
    assert config.roles["synthesis"].primary == "openai/gpt-4o-2024-11-20"


def test_load_config_applies_daily_budget_env_override(monkeypatch) -> None:
    monkeypatch.setenv("DAILY_USD_BUDGET", "12.5")

    config = LLMConfig.from_toml(
        """
        daily_usd_budget = 10.0
        pricing_table_version = "2026-05-26"

        [role.synthesis]
        primary = "openai/gpt-4o-2024-11-20"
        fallback = []
        max_input_tokens = 32000
        max_output_tokens = 2000
        """
    )

    assert config.daily_usd_budget == 12.5


def test_role_config_accepts_lmstudio_model_ids() -> None:
    config = LLMConfig.from_toml(
        """
        daily_usd_budget = 10.0
        pricing_table_version = "2026-05-26"

        [role.synthesis]
        primary = "lmstudio/gpt-oss-20b"
        fallback = ["openai/gpt-4o-2024-11-20"]
        max_input_tokens = 32000
        max_output_tokens = 2000
        """
    )

    assert config.roles["synthesis"].primary == "lmstudio/gpt-oss-20b"


def test_role_config_accepts_vertex_alias_model_ids() -> None:
    config = LLMConfig.from_toml(
        """
        daily_usd_budget = 10.0
        pricing_table_version = "2026-05-26"

        [role.synthesis]
        primary = "vertex_ai/gemini-2.5-flash-lite"
        fallback = ["openai/gpt-4o-mini-2024-07-18"]
        max_input_tokens = 32000
        max_output_tokens = 2000
        """
    )

    assert config.roles["synthesis"].primary == "vertex_ai/gemini-2.5-flash-lite"
