"""Adversarial tests for the MCP tool surface (Spec 10 / Spec 07).

Goal: catch any future refactor that exposes internal components
(`crawl`, `search`, `llm`, `rerank`) as MCP tools — that would let a
calling LLM bypass the orchestrator's safety perimeter and drive the
crawler or LLM directly.
"""

from __future__ import annotations

import re

import pytest

from nexus.mcp.server import MCPTransport
from nexus.mcp.types import MCPConfig

pytestmark = pytest.mark.security


class _NullOrchestrator:
    async def search(self, _req):
        if False:
            yield  # pragma: no cover


@pytest.fixture
def transport() -> MCPTransport:
    return MCPTransport(
        orchestrator=_NullOrchestrator(),
        llm_config_roles={},
        config=MCPConfig(token="t" * 32),
    )


# ---------- tool inventory ----------


def test_exactly_one_tool_registered(transport: MCPTransport) -> None:
    """Spec 07 §Tool surface: only `agentic_search` is exposed."""
    tools = transport.tool_definitions()
    assert len(tools) == 1, f"expected 1 tool, got {[t['name'] for t in tools]}"


def test_only_agentic_search_tool(transport: MCPTransport) -> None:
    names = {t["name"] for t in transport.tool_definitions()}
    assert names == {"agentic_search"}


@pytest.mark.parametrize(
    "forbidden",
    ["crawl", "search", "rerank", "llm", "llm_complete", "fetch_url", "synthesize"],
)
def test_internal_tools_not_exposed(transport: MCPTransport, forbidden: str) -> None:
    """A future refactor that exposes a sub-component as a tool would
    let the calling LLM bypass the orchestrator's defense perimeter."""
    names = {t["name"] for t in transport.tool_definitions()}
    assert forbidden not in names


# ---------- tool description hygiene ----------

_INSTRUCTION_SHAPED = re.compile(
    r"\b(you are|you must|you should|always|never|do not|please follow)\b",
    re.IGNORECASE,
)


def test_tool_description_has_no_instruction_shaped_text(transport: MCPTransport) -> None:
    """MCP tool descriptions reach the calling LLM verbatim. If our
    description tells the LLM 'always …' / 'never …' we have given
    attackers a foothold to override our own preamble."""
    for tool in transport.tool_definitions():
        desc = tool.get("description", "")
        match = _INSTRUCTION_SHAPED.search(desc)
        assert match is None, (
            f"tool {tool['name']!r} description contains instruction-shaped text: {match.group()!r}"
        )


# ---------- resource inventory ----------


def test_status_resource_serves(transport: MCPTransport) -> None:
    data = transport.read_resource("nexus://status")
    assert isinstance(data, dict)


def test_roles_resource_serves(transport: MCPTransport) -> None:
    data = transport.read_resource("nexus://config/roles")
    assert isinstance(data, dict)


def test_unknown_resource_raises(transport: MCPTransport) -> None:
    with pytest.raises(KeyError):
        transport.read_resource("nexus://internal/debug")


# ---------- auth ----------


def test_validate_token_requires_bearer_prefix(transport: MCPTransport) -> None:
    assert transport.validate_token(None) is False
    assert transport.validate_token("") is False
    assert transport.validate_token("t" * 32) is False  # missing "Bearer "


def test_validate_token_rejects_wrong_token(transport: MCPTransport) -> None:
    assert transport.validate_token("Bearer wrong") is False


def test_validate_token_accepts_correct(transport: MCPTransport) -> None:
    assert transport.validate_token("Bearer " + "t" * 32) is True


# ---------- config invariants ----------


def test_config_rejects_empty_token() -> None:
    with pytest.raises(ValueError, match="token"):
        MCPConfig(token="")


def test_config_caps_input_size() -> None:
    """Spec 07 §Size limits: input JSON ≤ 4 KB."""
    cfg = MCPConfig(token="x" * 32)
    assert cfg.input_json_max_bytes <= 4096
