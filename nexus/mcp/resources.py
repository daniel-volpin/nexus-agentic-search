from __future__ import annotations

from collections.abc import Mapping
from time import monotonic
from typing import Protocol

from .types import MCPConfig, StatusState


class RoleConfigView(Protocol):
    """Structural view of an LLM role config — just the fields the
    ``nexus://config/roles`` resource exposes. Decouples the MCP layer
    from the concrete LLM config class."""

    primary: str
    fallback: list[str]
    max_input_tokens: int
    max_output_tokens: int


def read_status(*, config: MCPConfig, state: StatusState) -> dict:
    payload = {
        "uptime_s": int(monotonic() - state.started_at),
        "version": config.version,
        "requests_today": state.requests_today,
    }
    if config.reveal_cost:
        payload["daily_cost_usd"] = state.daily_cost_usd
    return payload


def read_roles(*, roles: Mapping[str, RoleConfigView]) -> dict:
    out: dict[str, dict] = {}
    for name, role in roles.items():
        out[name] = {
            "primary": role.primary,
            "fallback": list(role.fallback),
            "max_input_tokens": role.max_input_tokens,
            "max_output_tokens": role.max_output_tokens,
        }
    return out
