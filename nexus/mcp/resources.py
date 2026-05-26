from __future__ import annotations

from time import monotonic

from .types import MCPConfig, StatusState


def read_status(*, config: MCPConfig, state: StatusState) -> dict:
    payload = {
        "uptime_s": int(monotonic() - state.started_at),
        "version": config.version,
        "requests_today": state.requests_today,
    }
    if config.reveal_cost:
        payload["daily_cost_usd"] = state.daily_cost_usd
    return payload


def read_roles(*, roles: dict[str, object]) -> dict:
    out: dict[str, dict] = {}
    for name, role in roles.items():
        out[name] = {
            "primary": getattr(role, "primary"),
            "fallback": list(getattr(role, "fallback")),
            "max_input_tokens": getattr(role, "max_input_tokens"),
            "max_output_tokens": getattr(role, "max_output_tokens"),
        }
    return out
