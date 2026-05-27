"""Unit tests for ``nexus.security.selftest`` (Spec 10)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nexus.crawl.ssrf import SSRFGuard
from nexus.security.selftest import (
    SelftestFailure,
    SelftestReport,
    _check_egress_firewall,
    _check_redaction,
    _check_ssrf_guard,
    _check_synthesis_tools_disabled,
    run_selftest,
)

# ---------- SelftestReport ----------


def test_all_ok_when_every_check_true() -> None:
    r = SelftestReport(
        egress_firewall_ok=True,
        ssrf_guard_ok=True,
        redaction_ok=True,
        synthesis_tools_disabled_ok=True,
    )
    assert r.all_ok is True
    assert r.critical_failures == ()


def test_critical_failures_does_not_include_egress() -> None:
    r = SelftestReport(
        egress_firewall_ok=False,
        ssrf_guard_ok=True,
        redaction_ok=True,
        synthesis_tools_disabled_ok=True,
        failures=("egress_firewall: connection succeeded",),
    )
    assert r.all_ok is False
    assert r.critical_failures == ()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (dict(ssrf_guard_ok=False), ("ssrf_guard",)),
        (dict(redaction_ok=False), ("redaction",)),
        (dict(synthesis_tools_disabled_ok=False), ("synthesis_tools_disabled",)),
        (
            dict(ssrf_guard_ok=False, redaction_ok=False),
            ("ssrf_guard", "redaction"),
        ),
    ],
)
def test_critical_failures_match_invariant(
    kwargs: dict[str, bool], expected: tuple[str, ...]
) -> None:
    base = dict(
        egress_firewall_ok=True,
        ssrf_guard_ok=True,
        redaction_ok=True,
        synthesis_tools_disabled_ok=True,
    )
    base.update(kwargs)
    r = SelftestReport(**base)
    assert r.critical_failures == expected


def test_selftest_failure_is_raisable() -> None:
    with pytest.raises(SelftestFailure):
        raise SelftestFailure("test")


# ---------- _check_ssrf_guard ----------


async def test_ssrf_check_passes_when_guard_rejects() -> None:
    failures: list[str] = []
    ok = await _check_ssrf_guard(failures)
    assert ok is True
    assert failures == []


async def test_ssrf_check_fails_when_guard_accepts() -> None:
    """Simulate a regressed guard by patching validate_url to accept."""
    failures: list[str] = []
    with patch.object(SSRFGuard, "validate_url", return_value=["1.2.3.4"]):
        ok = await _check_ssrf_guard(failures)
    assert ok is False
    assert failures
    assert "ssrf_guard" in failures[0]


# ---------- _check_redaction ----------


def test_redaction_check_passes() -> None:
    failures: list[str] = []
    ok = _check_redaction(failures)
    assert ok is True
    assert failures == []


def test_redaction_check_fails_when_patterns_neutered() -> None:
    failures: list[str] = []
    with patch(
        "nexus.security.selftest._redact_secrets",
        side_effect=lambda s: s,  # bypass redaction
    ):
        ok = _check_redaction(failures)
    assert ok is False
    assert "redaction" in failures[0]


# ---------- _check_synthesis_tools_disabled ----------


async def test_synthesis_tools_check_passes() -> None:
    failures: list[str] = []
    ok = await _check_synthesis_tools_disabled(client=None, failures=failures)
    assert ok is True
    assert failures == []


async def test_synthesis_tools_check_fails_if_enforcement_removed() -> None:
    """If the gateway stopped raising on tools-for-synthesis, the check
    must surface that as a critical failure (here simulated by
    swallowing the exception inside complete)."""
    from unittest.mock import AsyncMock

    failures: list[str] = []
    # Patch the throwaway client construction to return a stub that
    # silently completes instead of raising.
    fake = AsyncMock()
    fake.complete = AsyncMock(return_value=None)
    with patch("nexus.security.selftest._build_throwaway_client", return_value=fake):
        ok = await _check_synthesis_tools_disabled(client=None, failures=failures)
    assert ok is False
    assert "synthesis_tools_disabled" in failures[0]


async def test_synthesis_tools_check_distinguishes_unexpected_errors() -> None:
    from unittest.mock import AsyncMock

    failures: list[str] = []
    fake = AsyncMock()
    fake.complete = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("nexus.security.selftest._build_throwaway_client", return_value=fake):
        ok = await _check_synthesis_tools_disabled(client=None, failures=failures)
    assert ok is False
    assert "unexpected error" in failures[0]


# ---------- _check_egress_firewall ----------


async def test_egress_check_passes_when_probe_times_out() -> None:
    """Default behavior on a host with no route to 10.255.255.1: the
    connection times out. Selftest must treat that as success (the
    firewall did its job, or the route is simply absent)."""
    failures: list[str] = []
    ok = await _check_egress_firewall(failures)
    assert ok is True
    assert failures == []


async def test_egress_check_fails_when_probe_succeeds() -> None:
    """Simulate a misconfigured environment by mocking sock_connect
    to return synchronously without raising."""
    from unittest.mock import AsyncMock

    failures: list[str] = []
    loop = __import__("asyncio").get_running_loop()
    with patch.object(loop, "sock_connect", new=AsyncMock(return_value=None)):
        ok = await _check_egress_firewall(failures)
    assert ok is False
    assert "egress_firewall" in failures[0]


# ---------- run_selftest ----------


async def test_run_selftest_returns_all_ok_by_default() -> None:
    report = await run_selftest()
    assert report.all_ok is True
    assert report.critical_failures == ()


async def test_run_selftest_surfaces_critical_failure() -> None:
    """If a single critical check regresses, the report reflects it
    and ``critical_failures`` is non-empty so the entrypoint can
    fail-closed."""
    with patch.object(SSRFGuard, "validate_url", return_value=["1.2.3.4"]):
        report = await run_selftest()
    assert report.ssrf_guard_ok is False
    assert report.critical_failures == ("ssrf_guard",)


async def test_run_selftest_does_not_raise_on_failure() -> None:
    """Selftest must never raise — entrypoints inspect the report."""
    with patch.object(SSRFGuard, "validate_url", return_value=["1.2.3.4"]):
        report = await run_selftest()  # must not raise
    assert isinstance(report, SelftestReport)
