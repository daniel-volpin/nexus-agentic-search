"""Adversarial SSRF tests for DNS rebinding (Spec 03 + Spec 10).

DNS rebinding: an attacker-controlled domain resolves first to a
public IP (passes the guard), then on a second resolution to RFC1918
(reached when the HTTP client connects). The Spec 10 §SSRF guard
defense is: resolve hostname once, connect to the resolved IP
literal, set ``Host:`` header to the original hostname so SNI/HTTP
routing still works.

Current state:
- ``SSRFGuard.validate_url`` correctly re-resolves on each call.
- ``CrawlClient.fetch`` does ONE guard call then hands the URL to
  ``urlopen``, which performs its own (second) DNS lookup.

The guard-level tests below verify the guard does the right thing in
isolation (re-evaluates on every call). The remaining integration gap
— the crawler should resolve once and connect to the pinned IP rather
than letting urlopen re-resolve — is a tracked Spec 03 hardening
follow-up. It is NOT exercised here with a live-network probe because
connecting to a reserved IP is slow and flaky; the gap is documented
in this module's docstring and the PR description instead.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from nexus.crawl.ssrf import SSRFGuard

pytestmark = pytest.mark.security


class _RebindingResolver:
    """Stateful fake getaddrinfo. Each call returns the next IP in
    sequence; runs out of sequence → repeats the last entry. The
    pattern mirrors a DNS server with TTL=0 returning different
    answers on successive queries."""

    def __init__(self, ips: list[str]) -> None:
        self._ips = list(ips)
        self._index = 0
        self.calls = 0

    def __call__(self, host, port, *args, **kwargs):
        self.calls += 1
        ip = self._ips[min(self._index, len(self._ips) - 1)]
        self._index += 1
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, None, None, "", (ip, port or 0))]


# ---------- guard re-evaluates on each call ----------


def test_guard_rejects_when_second_resolution_returns_private() -> None:
    """If the same hostname resolves public, then private on a second
    call, the second call MUST be rejected. The guard re-resolves
    every call (no caching), so this passes today."""
    guard = SSRFGuard()
    resolver = _RebindingResolver(["93.184.216.34", "10.0.0.1"])
    with patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=resolver):
        first = guard.validate_url("http://rebind.example.test/")
        assert first == ["93.184.216.34"]
        with pytest.raises(ValueError, match="disallowed address"):
            guard.validate_url("http://rebind.example.test/")


def test_guard_rejects_when_first_resolution_returns_private() -> None:
    """Baseline: a hostname whose first lookup is already private is
    rejected on the first guard call."""
    guard = SSRFGuard()
    resolver = _RebindingResolver(["10.0.0.1"])
    with (
        patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=resolver),
        pytest.raises(ValueError, match="disallowed address"),
    ):
        guard.validate_url("http://always-private.example.test/")


def test_guard_accepts_when_both_resolutions_are_public() -> None:
    """Negative control: a hostname that consistently resolves to
    public IPs passes both calls."""
    guard = SSRFGuard()
    resolver = _RebindingResolver(["93.184.216.34", "8.8.8.8"])
    with patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=resolver):
        guard.validate_url("http://public.example.test/")
        guard.validate_url("http://public.example.test/")
    assert resolver.calls == 2


def test_guard_rejects_v6_rebinding_into_loopback() -> None:
    guard = SSRFGuard()
    resolver = _RebindingResolver(["2001:4860:4860::8888", "::1"])
    with patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=resolver):
        guard.validate_url("http://v6-public.example.test/")
        with pytest.raises(ValueError, match="disallowed address"):
            guard.validate_url("http://v6-public.example.test/")


def test_guard_call_count_proves_no_caching() -> None:
    """The guard MUST re-resolve on every call (no internal cache) so
    that a rebind between calls is caught. Two validate_url calls →
    two getaddrinfo calls."""
    guard = SSRFGuard()
    resolver = _RebindingResolver(["93.184.216.34", "93.184.216.34"])
    with patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=resolver):
        guard.validate_url("http://x.example.test/")
        guard.validate_url("http://x.example.test/")
    assert resolver.calls == 2
