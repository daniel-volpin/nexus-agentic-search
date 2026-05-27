"""Adversarial tests for bearer-auth primitives across transports (Spec 10)."""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from nexus.http import auth as http_auth
from nexus.http.auth import require_bearer_token
from nexus.mcp.server import MCPTransport
from nexus.mcp.types import MCPConfig

pytestmark = pytest.mark.security


class _NullOrchestrator:
    async def search(self, _req):
        if False:
            yield  # pragma: no cover


_TOKEN = "secret-token-of-sufficient-length-32+"


# ---------- HTTP bearer ----------


def test_http_require_bearer_rejects_missing() -> None:
    with pytest.raises(HTTPException) as exc:
        require_bearer_token(_TOKEN, None)
    assert exc.value.status_code == 401


def test_http_require_bearer_rejects_empty_header() -> None:
    with pytest.raises(HTTPException) as exc:
        require_bearer_token(_TOKEN, "")
    assert exc.value.status_code == 401


def test_http_require_bearer_rejects_wrong_scheme() -> None:
    for header in (f"Basic {_TOKEN}", f"Token {_TOKEN}", _TOKEN):
        with pytest.raises(HTTPException) as exc:
            require_bearer_token(_TOKEN, header)
        assert exc.value.status_code == 401


def test_http_require_bearer_rejects_wrong_value() -> None:
    with pytest.raises(HTTPException) as exc:
        require_bearer_token(_TOKEN, "Bearer wrong-value")
    assert exc.value.status_code == 401


def test_http_require_bearer_accepts_correct() -> None:
    assert require_bearer_token(_TOKEN, f"Bearer {_TOKEN}") == _TOKEN


def test_http_require_bearer_uses_constant_time_comparison() -> None:
    """Static source check: equality MUST go through hmac.compare_digest
    so a timing side-channel cannot leak prefix-match progress."""
    src = inspect.getsource(http_auth)
    assert "hmac.compare_digest" in src, (
        "require_bearer_token must use hmac.compare_digest for token equality"
    )
    # Negative: a bare `==` between token and expected_token is a smell.
    # We don't ban `==` outright (other comparisons are fine) — we just
    # require compare_digest is in use.


# ---------- MCP bearer ----------


@pytest.fixture
def mcp() -> MCPTransport:
    return MCPTransport(
        orchestrator=_NullOrchestrator(),
        llm_config_roles={},
        config=MCPConfig(token=_TOKEN),
    )


def test_mcp_validate_token_rejects_missing(mcp: MCPTransport) -> None:
    assert mcp.validate_token(None) is False
    assert mcp.validate_token("") is False


def test_mcp_validate_token_rejects_wrong_scheme(mcp: MCPTransport) -> None:
    assert mcp.validate_token(_TOKEN) is False  # missing "Bearer "
    assert mcp.validate_token(f"Basic {_TOKEN}") is False


def test_mcp_validate_token_rejects_wrong_value(mcp: MCPTransport) -> None:
    assert mcp.validate_token("Bearer wrong-value") is False


def test_mcp_validate_token_accepts_correct(mcp: MCPTransport) -> None:
    assert mcp.validate_token(f"Bearer {_TOKEN}") is True


# ---------- token shape ----------


def test_mcp_config_rejects_empty_token() -> None:
    with pytest.raises(ValueError, match="token"):
        MCPConfig(token="")


def test_no_default_token_in_mcp_config() -> None:
    """``token`` is a required dataclass arg — there is no default
    that would let a forgotten env var ship a known token."""
    import inspect

    sig = inspect.signature(MCPConfig)
    assert sig.parameters["token"].default is inspect.Parameter.empty


# ---------- whitespace / casing attacks ----------


def test_bearer_prefix_is_case_sensitive() -> None:
    """HTTP headers are case-insensitive for the name but values
    must be matched byte-for-byte. `bearer` lower-case must not
    be accepted (the spec uses `Bearer`)."""
    with pytest.raises(HTTPException):
        require_bearer_token(_TOKEN, f"bearer {_TOKEN}")


def test_extra_whitespace_in_bearer_rejected() -> None:
    """A trailing space or tab after the token MUST NOT match
    (constant-time comparison preserves byte order)."""
    with pytest.raises(HTTPException):
        require_bearer_token(_TOKEN, f"Bearer {_TOKEN} ")
