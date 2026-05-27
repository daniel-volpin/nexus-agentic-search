"""Tests for the ``nexus.main`` entrypoint sequencing (Spec 12).

We do NOT spin up real uvicorn servers in unit tests — that's
integration territory. These tests verify the assembly order,
the selftest fail-closed path, and the placeholder search client's
contract.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.main import _PlaceholderSearchClient, amain
from nexus.search import SearchRequest

# ---------- placeholder search client ----------


async def test_placeholder_search_returns_empty_response() -> None:
    client = _PlaceholderSearchClient()
    req = SearchRequest(query="hello world", max_results=5)
    response = await client.search(req)
    assert response.results == []
    assert response.provider == "placeholder"
    assert response.query_sent == "hello world"


async def test_placeholder_search_emits_critical_log_on_construction(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    with caplog.at_level(logging.CRITICAL):
        _PlaceholderSearchClient()
    crit = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert crit, "placeholder must emit CRITICAL at construction"
    assert "placeholder" in crit[0].getMessage().lower()


# ---------- amain: config failure path ----------


async def test_amain_returns_ex_config_when_tokens_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NEXUS_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("NEXUS_MCP_TOKEN", raising=False)
    code = await amain()
    assert code == 78  # EX_CONFIG
    err = capsys.readouterr().err
    assert "config" in err.lower()


# ---------- amain: selftest fail-closed path ----------


async def test_amain_fails_closed_when_selftest_critical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NEXUS_HTTP_TOKEN", "t" * 32)
    monkeypatch.setenv("NEXUS_MCP_TOKEN", "m" * 32)
    monkeypatch.setenv("CACHE_ROOT", str(tmp_path))

    # Force the selftest to surface a critical failure.
    from nexus.security.selftest import SelftestReport

    bad_report = SelftestReport(
        egress_firewall_ok=True,
        ssrf_guard_ok=False,
        redaction_ok=True,
        synthesis_tools_disabled_ok=True,
        failures=("ssrf_guard: simulated regression",),
    )

    with (
        patch("nexus.main.setup_telemetry"),  # don't start a real server
        patch("nexus.main.run_selftest", AsyncMock(return_value=bad_report)),
        patch("nexus.main._start_http") as start_http,
        patch("nexus.main._start_mcp") as start_mcp,
    ):
        code = await amain()

    assert code == 70  # EX_SOFTWARE
    # Transports must NOT have been started.
    start_http.assert_not_called()
    start_mcp.assert_not_called()


# ---------- amain: full bootstrap reaches "service_ready" ----------


async def test_amain_starts_transports_when_selftest_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Patch transport bootstrap to no-op tasks; verify amain reaches
    service_ready and shuts down cleanly when signaled."""
    import asyncio

    monkeypatch.setenv("NEXUS_HTTP_TOKEN", "t" * 32)
    monkeypatch.setenv("NEXUS_MCP_TOKEN", "m" * 32)
    monkeypatch.setenv("CACHE_ROOT", str(tmp_path))

    async def _noop() -> None:
        await asyncio.Event().wait()

    fake_server = MagicMock()
    fake_server.should_exit = False
    fake_http_task = asyncio.create_task(_noop(), name="http")
    fake_mcp_task = asyncio.create_task(_noop(), name="mcp")

    def _stop_after_ready(*_args, **_kwargs):
        # Schedule a shutdown by triggering the stop_event indirectly:
        # we patch asyncio.Event.wait to return immediately.
        return (fake_server, fake_http_task)

    with (
        patch("nexus.main.setup_telemetry"),  # don't start a real server
        patch("nexus.main._start_http", side_effect=_stop_after_ready),
        patch("nexus.main._start_mcp", return_value=fake_mcp_task),
        patch("nexus.main.asyncio.Event.wait", new=AsyncMock(return_value=None)),
    ):
        code = await amain()

    assert code == 0
    # Tasks should have been cancelled during teardown.
    assert fake_http_task.cancelled() or fake_http_task.done()
    assert fake_mcp_task.cancelled() or fake_mcp_task.done()
