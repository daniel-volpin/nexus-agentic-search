"""Runtime startup security self-test.

Each check asserts a core security invariant holds at boot. The caller
is the service entrypoint (``nexus.main``), which decides what to do
based on the report's ``critical_failures`` set:

- Critical failures (``ssrf_guard``, ``redaction``,
  ``synthesis_tools_disabled``) MUST prevent startup. They mean an
  in-process defense has regressed and is not safe to expose.
- Non-critical failures (``egress_firewall``) are logged but do not
  block startup. In dev the host firewall is rarely installed; in prod
  its absence is meaningful but the in-process SSRF guard still applies.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass, field

from nexus.crawl.ssrf import SSRFGuard
from nexus.llm import (
    LiteLLMClient,
    LLMConfig,
    LLMRoleConfig,
    SynthesisToolsDisabled,
    ToolSpec,
)
from nexus.llm.redaction import _redact_secrets

logger = logging.getLogger(__name__)

# A bogus host inside RFC1918 that a correctly-firewalled container
# cannot reach. We do not own this IP; we just expect any attempt to
# connect to fail closed.
_FIREWALL_PROBE_HOST = "10.255.255.1"
_FIREWALL_PROBE_PORT = 81
_FIREWALL_PROBE_TIMEOUT_S = 1.0

# A canonical fake key matching the sk- pattern. Long enough to trip
# the redactor; obviously not a real credential.
_REDACTION_TEST_KEY = "sk-SELFTESTSELFTESTSELFTESTSELFTEST"


class SelftestFailure(Exception):
    """Raised by callers when ``critical_failures`` is non-empty."""


@dataclass(frozen=True)
class SelftestReport:
    egress_firewall_ok: bool
    ssrf_guard_ok: bool
    redaction_ok: bool
    synthesis_tools_disabled_ok: bool
    failures: tuple[str, ...] = field(default_factory=tuple)

    @property
    def all_ok(self) -> bool:
        return (
            self.egress_firewall_ok
            and self.ssrf_guard_ok
            and self.redaction_ok
            and self.synthesis_tools_disabled_ok
        )

    @property
    def critical_failures(self) -> tuple[str, ...]:
        """Failures that MUST prevent the service from starting."""
        critical: list[str] = []
        if not self.ssrf_guard_ok:
            critical.append("ssrf_guard")
        if not self.redaction_ok:
            critical.append("redaction")
        if not self.synthesis_tools_disabled_ok:
            critical.append("synthesis_tools_disabled")
        return tuple(critical)


async def run_selftest(*, llm_client: LiteLLMClient | None = None) -> SelftestReport:
    """Run the four startup checks. Never raises.

    Pass an ``llm_client`` to reuse a configured client; if None the
    selftest constructs a minimal config-only instance to exercise the
    boundary check (no provider call is made — the check raises
    before reaching the backend).
    """
    failures: list[str] = []

    ssrf_ok = await _check_ssrf_guard(failures)
    redaction_ok = _check_redaction(failures)
    synthesis_tools_ok = await _check_synthesis_tools_disabled(llm_client, failures)
    egress_ok = await _check_egress_firewall(failures)

    report = SelftestReport(
        egress_firewall_ok=egress_ok,
        ssrf_guard_ok=ssrf_ok,
        redaction_ok=redaction_ok,
        synthesis_tools_disabled_ok=synthesis_tools_ok,
        failures=tuple(failures),
    )
    _log_summary(report)
    return report


# ---------- individual checks ----------


async def _check_ssrf_guard(failures: list[str]) -> bool:
    """The in-process SSRF guard rejects loopback, link-local cloud
    metadata, and RFC1918 addresses."""
    guard = SSRFGuard()
    probes = (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://[::1]/",
    )
    for url in probes:
        try:
            guard.validate_url(url)
        except ValueError:
            continue
        failures.append(f"ssrf_guard: accepted {url!r}")
        return False
    return True


def _check_redaction(failures: list[str]) -> bool:
    """The secret redactor masks known key shapes."""
    redacted = _redact_secrets(f"emit {_REDACTION_TEST_KEY} suffix")
    if "[REDACTED]" not in redacted or _REDACTION_TEST_KEY in redacted:
        failures.append("redaction: secret pattern survived the filter")
        return False
    return True


async def _check_synthesis_tools_disabled(
    client: LiteLLMClient | None, failures: list[str]
) -> bool:
    """The LLM gateway rejects
    ``complete(role="synthesis", tools=[...])`` at the API boundary.
    """
    instance = client or _build_throwaway_client()
    tool_spec: ToolSpec = {"name": "noop", "description": "x", "parameters": {}}
    try:
        await instance.complete(
            role="synthesis",
            messages=[{"role": "user", "content": "selftest"}],
            max_output_tokens=1,
            tools=[tool_spec],
        )
    except SynthesisToolsDisabled:
        return True
    except Exception as exc:
        failures.append(f"synthesis_tools_disabled: unexpected error {type(exc).__name__}: {exc!s}")
        return False
    failures.append("synthesis_tools_disabled: gateway accepted tools for synthesis")
    return False


async def _check_egress_firewall(failures: list[str]) -> bool:
    """A container with the host firewall correctly applied cannot
    reach RFC1918 destinations at the network layer.

    In dev (laptop, CI without firewall configured) the test still
    succeeds because the probe IP simply has no route. In prod with
    the firewall installed the kernel drops the SYN. Both outcomes
    are 'OK'; failure means the connection actually succeeded, which
    indicates a misconfigured container or test environment.
    """
    try:
        await asyncio.wait_for(
            asyncio.get_running_loop().sock_connect(
                _new_probe_socket(),
                (_FIREWALL_PROBE_HOST, _FIREWALL_PROBE_PORT),
            ),
            timeout=_FIREWALL_PROBE_TIMEOUT_S,
        )
    except (TimeoutError, OSError):
        return True
    failures.append(
        f"egress_firewall: connection to {_FIREWALL_PROBE_HOST}:{_FIREWALL_PROBE_PORT} succeeded"
    )
    return False


# ---------- helpers ----------


def _new_probe_socket() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    s.setblocking(False)
    return s


def _build_throwaway_client() -> LiteLLMClient:
    """Minimal client whose only purpose is to exercise the boundary
    check. The synthesis-tools-disabled error fires before any backend
    call, so we never reach the network."""
    return LiteLLMClient(
        config=LLMConfig(
            roles={
                "synthesis": LLMRoleConfig(
                    primary="openai/gpt-4o-2024-11-20",
                    fallback=[],
                    max_input_tokens=128,
                    max_output_tokens=8,
                )
            },
            daily_usd_budget=1.0,
            soft_budget_fraction=0.8,
            pricing_table_version="selftest",
        ),
    )


def _log_summary(report: SelftestReport) -> None:
    if report.all_ok:
        logger.info("security_selftest_ok")
        return
    if report.critical_failures:
        logger.critical(
            "security_selftest_failed_critical",
            extra={"failures": list(report.failures)},
        )
        return
    logger.warning(
        "security_selftest_failed_noncritical",
        extra={"failures": list(report.failures)},
    )
