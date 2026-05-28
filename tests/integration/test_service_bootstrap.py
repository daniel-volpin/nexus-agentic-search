from __future__ import annotations

import pytest

from nexus.config import AppConfig, load_config
from nexus.main import build_app


def test_app_config_requires_http_and_mcp_tokens() -> None:
    with pytest.raises(ValueError):
        AppConfig(http_token="", mcp_token="")


def test_load_config_reads_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_HTTP_TOKEN", "http-secret")
    monkeypatch.setenv("NEXUS_MCP_TOKEN", "mcp-secret")

    config = load_config()

    assert config.http_token == "http-secret"
    assert config.mcp_token == "mcp-secret"


def test_build_app_constructs_asgi_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_HTTP_TOKEN", "http-secret")
    monkeypatch.setenv("NEXUS_MCP_TOKEN", "mcp-secret")

    app = build_app()

    assert callable(app)
