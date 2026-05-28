"""Central process-wide configuration.

Each component owns its own typed config object (``LLMConfig``,
``HTTPConfig``, ``MCPConfig``, …). This module composes them and
loads from environment variables, applying validation that's
required at startup (token length, paths, etc.).

Environment variables read here mirror ``.env.example`` in the repo
root. The container's :file:`secrets/nexus.env` is loaded by
docker-compose via ``env_file:`` — this module just reads ``os.environ``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nexus.http.types import HTTPConfig
from nexus.llm.config import LLMConfig
from nexus.mcp.types import MCPConfig


class _Env(BaseSettings):
    """Top-level env reader. Each field maps to one env var.

    pydantic-settings reads from os.environ at instantiation and
    surfaces a clear ValidationError if a required field is missing.
    """

    model_config = SettingsConfigDict(
        env_file=None,  # docker-compose supplies env_file; we just read env
        case_sensitive=False,
        extra="ignore",
    )

    # Provider keys (optional at startup; per-role failover handles absence)
    brave_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")

    # SearXNG
    searxng_base_url: str = "http://searxng:8080"
    searxng_engines: str = "duckduckgo"
    searxng_api_key: SecretStr = SecretStr("")

    # Bearer tokens (mandatory; service refuses to start without)
    nexus_http_token: SecretStr = SecretStr("")
    nexus_mcp_token: SecretStr = SecretStr("")

    # Budget
    daily_usd_budget: float = 10.0

    # Logging / telemetry
    log_level: str = "INFO"
    json_logs: bool = True
    metrics_port: int = 9090

    # Cache
    cache_root: Path = Path("/var/lib/nexus/cache")
    cache_total_size_gb: float = 2.0

    # Transport bind — container-internal; the firewall / docker network
    # boundary enforces edge isolation.
    bind_host: str = "0.0.0.0"  # noqa: S104  # container-internal bind only
    http_port: int = 8186
    mcp_port: int = 8185

    # Toggles
    enable_query_expansion: bool = False
    status_reveal_cost: bool = True

    # LLM config file
    llm_config_path: Path = Path("config/llm.toml")

    @field_validator("nexus_http_token", "nexus_mcp_token")
    @classmethod
    def _token_length(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw and len(raw) < 32:
            raise ValueError("bearer token must be at least 32 characters")
        return value

    @field_validator("searxng_engines")
    @classmethod
    def _searxng_engine_allowlist(cls, value: str) -> str:
        engines = [e.strip() for e in value.split(",") if e.strip()]
        allowed = {"google", "duckduckgo"}
        bad = set(engines) - allowed
        if bad:
            raise ValueError(
                f"SEARXNG_ENGINES contains disallowed engines: {sorted(bad)}; "
                f"allowed: {sorted(allowed)}"
            )
        if not engines:
            raise ValueError("SEARXNG_ENGINES must list at least one engine")
        return ",".join(engines)


@dataclass(frozen=True)
class Config:
    """Composed runtime config. Built by :func:`load_config`."""

    env: _Env
    llm: LLMConfig
    http: HTTPConfig
    mcp: MCPConfig
    cache_root: Path
    cache_total_size_gb: float
    bind_host: str
    http_port: int
    mcp_port: int
    metrics_port: int
    log_level: str
    json_logs: bool
    enable_query_expansion: bool
    searxng_engines: tuple[str, ...] = field(default_factory=tuple)
    searxng_base_url: str = ""
    searxng_api_key: str = ""

    @property
    def llm_role_views(self) -> dict[str, object]:
        """Role → LLMRoleConfig map suitable for HTTP/MCP `llm_config_roles`."""
        return dict(self.llm.roles)


def load_config(*, require_tokens: bool = True) -> Config:
    """Read env + LLM config TOML, validate, compose.

    Pass ``require_tokens=False`` only for tests that don't need
    transports — defaults to fail-closed if tokens are missing.
    """
    env = _Env()
    if require_tokens:
        for name in ("nexus_http_token", "nexus_mcp_token"):
            secret = getattr(env, name).get_secret_value()
            if not secret:
                raise RuntimeError(f"{name.upper()} is required; service cannot start without it")

    llm_config = LLMConfig.from_file(env.llm_config_path)
    http_config = HTTPConfig(
        token=env.nexus_http_token.get_secret_value() or "dev-token-32+chars-do-not-use",
        version=_version(),
        reveal_cost=env.status_reveal_cost,
    )
    mcp_config = MCPConfig(
        token=env.nexus_mcp_token.get_secret_value() or "dev-token-32+chars-do-not-use",
        version=_version(),
        reveal_cost=env.status_reveal_cost,
    )

    return Config(
        env=env,
        llm=llm_config,
        http=http_config,
        mcp=mcp_config,
        cache_root=env.cache_root,
        cache_total_size_gb=env.cache_total_size_gb,
        bind_host=env.bind_host,
        http_port=env.http_port,
        mcp_port=env.mcp_port,
        metrics_port=env.metrics_port,
        log_level=env.log_level,
        json_logs=env.json_logs,
        enable_query_expansion=env.enable_query_expansion,
        searxng_engines=tuple(e.strip() for e in env.searxng_engines.split(",")),
        searxng_base_url=env.searxng_base_url,
        searxng_api_key=env.searxng_api_key.get_secret_value(),
    )


def _version() -> str:
    """Service version. Sourced from package __version__ when wired;
    placeholder until the build pipeline injects a real value."""
    from nexus import __version__ as v

    return v


__all__ = ["Config", "load_config"]
