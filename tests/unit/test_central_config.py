"""Tests for the central ``nexus.config`` aggregator (Spec 12)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.config import load_config


def _env_baseline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Minimal env that lets load_config() succeed."""
    monkeypatch.setenv("NEXUS_HTTP_TOKEN", "t" * 32)
    monkeypatch.setenv("NEXUS_MCP_TOKEN", "m" * 32)
    monkeypatch.setenv("CACHE_ROOT", str(tmp_path))
    # Use the real default config that ships with the repo.
    monkeypatch.setenv("LLM_CONFIG_PATH", "config/llm.toml")


def test_load_config_with_baseline_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_baseline(monkeypatch, tmp_path)
    cfg = load_config()
    assert cfg.http.token
    assert cfg.mcp.token
    assert "synthesis" in cfg.llm.roles
    assert cfg.cache_root == tmp_path


def test_load_config_fails_without_http_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("NEXUS_HTTP_TOKEN", raising=False)
    monkeypatch.setenv("NEXUS_MCP_TOKEN", "m" * 32)
    monkeypatch.setenv("CACHE_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="NEXUS_HTTP_TOKEN"):
        load_config()


def test_load_config_fails_without_mcp_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NEXUS_HTTP_TOKEN", "t" * 32)
    monkeypatch.delenv("NEXUS_MCP_TOKEN", raising=False)
    monkeypatch.setenv("CACHE_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="NEXUS_MCP_TOKEN"):
        load_config()


def test_load_config_rejects_short_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NEXUS_HTTP_TOKEN", "too-short")
    monkeypatch.setenv("NEXUS_MCP_TOKEN", "m" * 32)
    monkeypatch.setenv("CACHE_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="32 characters"):
        load_config()


def test_load_config_rejects_disallowed_searxng_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env_baseline(monkeypatch, tmp_path)
    monkeypatch.setenv("SEARXNG_ENGINES", "bing,google")
    with pytest.raises(ValueError, match="disallowed engines"):
        load_config()


def test_load_config_accepts_engines_in_any_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env_baseline(monkeypatch, tmp_path)
    monkeypatch.setenv("SEARXNG_ENGINES", "duckduckgo,google")
    cfg = load_config()
    assert set(cfg.searxng_engines) == {"google", "duckduckgo"}


def test_load_config_rejects_empty_engines(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_baseline(monkeypatch, tmp_path)
    monkeypatch.setenv("SEARXNG_ENGINES", "")
    with pytest.raises(ValueError, match="at least one engine"):
        load_config()


def test_status_reveal_cost_defaults_true(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_baseline(monkeypatch, tmp_path)
    cfg = load_config()
    assert cfg.http.reveal_cost is True
    assert cfg.mcp.reveal_cost is True


def test_status_reveal_cost_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env_baseline(monkeypatch, tmp_path)
    monkeypatch.setenv("STATUS_REVEAL_COST", "false")
    cfg = load_config()
    assert cfg.http.reveal_cost is False
    assert cfg.mcp.reveal_cost is False


def test_load_config_propagates_bind_and_ports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env_baseline(monkeypatch, tmp_path)
    monkeypatch.setenv("HTTP_PORT", "9000")
    monkeypatch.setenv("MCP_PORT", "9001")
    monkeypatch.setenv("METRICS_PORT", "9002")
    cfg = load_config()
    assert cfg.http_port == 9000
    assert cfg.mcp_port == 9001
    assert cfg.metrics_port == 9002


def test_require_tokens_false_lets_missing_tokens_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Used by test fixtures that don't need real transports."""
    monkeypatch.delenv("NEXUS_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("NEXUS_MCP_TOKEN", raising=False)
    monkeypatch.setenv("CACHE_ROOT", str(tmp_path))
    cfg = load_config(require_tokens=False)
    # A dev placeholder token is substituted so HTTPConfig __post_init__ holds.
    assert cfg.http.token
    assert cfg.mcp.token


def test_load_config_uses_existing_llm_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env_baseline(monkeypatch, tmp_path)
    cfg = load_config()
    # The bundled config has these three roles.
    assert {"synthesis", "rerank-decision", "query-expansion"} <= set(cfg.llm.roles)


def test_load_config_defaults_to_duckduckgo_plus_lmstudio_primary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env_baseline(monkeypatch, tmp_path)
    cfg = load_config()
    assert "duckduckgo" in cfg.searxng_engines
    assert cfg.llm.roles["synthesis"].primary.startswith("lmstudio/")


def test_load_config_reads_optional_searxng_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env_baseline(monkeypatch, tmp_path)
    monkeypatch.setenv("SEARXNG_API_KEY", "searx-private-key")
    cfg = load_config()
    assert cfg.searxng_api_key == "searx-private-key"
