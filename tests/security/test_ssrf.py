"""Adversarial tests for the SSRF guard (Spec 10).

The guard is the perimeter between the crawler and the network. Every URL
fed to the crawler MUST pass through it. These tests assert the deny-list
from Spec 10 §SSRF guard is enforced, scheme allowlist is enforced, and
IP literals are rejected by default.

DNS rebinding and redirect-chain re-validation are deeper invariants that
require a fixture DNS server and a fixture redirect server; those land
with the Spec 03 hardening follow-up. The cases here exercise the
synchronous validation surface that exists today.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nexus.crawl.ssrf import SSRFGuard

pytestmark = pytest.mark.security


@pytest.fixture
def guard() -> SSRFGuard:
    return SSRFGuard()


def _force_resolve(addresses: list[str]):
    """Patch socket.getaddrinfo so the guard sees the given resolved IPs."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(None, None, None, "", (ip, port or 0)) for ip in addresses]

    return patch("nexus.crawl.ssrf.socket.getaddrinfo", side_effect=fake_getaddrinfo)


# ---------- scheme allowlist ----------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ws://example.com/",
    ],
)
def test_rejects_non_http_schemes(guard: SSRFGuard, url: str) -> None:
    with pytest.raises(ValueError, match="scheme"):
        guard.validate_url(url)


def test_accepts_http_scheme(guard: SSRFGuard) -> None:
    with _force_resolve(["93.184.216.34"]):  # example.com
        result = guard.validate_url("http://example.com/")
    assert result == ["93.184.216.34"]


def test_accepts_https_scheme(guard: SSRFGuard) -> None:
    with _force_resolve(["93.184.216.34"]):
        result = guard.validate_url("https://example.com/path")
    assert result == ["93.184.216.34"]


# ---------- malformed URL handling ----------


def test_rejects_url_without_hostname(guard: SSRFGuard) -> None:
    with pytest.raises(ValueError, match="hostname"):
        guard.validate_url("http:///path")


def test_rejects_url_with_empty_string() -> None:
    with pytest.raises(ValueError, match=r"scheme|hostname"):
        SSRFGuard().validate_url("")


# ---------- IP-literal rejection (cloud metadata, RFC1918, loopback) ----------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata
        "http://169.254.170.2/",  # ECS metadata
        "http://[fe80::1]/",  # IPv6 link-local
        "http://127.0.0.1/",  # loopback v4
        "http://[::1]/",  # loopback v6
        "http://10.0.0.1/",  # RFC1918
        "http://192.168.1.1/",  # RFC1918
        "http://172.16.0.1/",  # RFC1918
        "http://[fc00::1]/",  # IPv6 ULA
        "http://100.64.0.1/",  # CGNAT
        "http://0.0.0.0/",  # current network
    ],
)
def test_rejects_ip_literal_hosts(guard: SSRFGuard, url: str) -> None:
    with pytest.raises(ValueError, match="ip literal"):
        guard.validate_url(url)


# ---------- DNS resolution into blocked ranges ----------


@pytest.mark.parametrize(
    "blocked_ip",
    [
        "169.254.169.254",
        "127.0.0.1",
        "127.0.0.53",
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.0.1",
        "100.64.0.1",
        "0.0.0.5",
    ],
)
def test_rejects_hostname_resolving_to_blocked_ipv4(guard: SSRFGuard, blocked_ip: str) -> None:
    with _force_resolve([blocked_ip]), pytest.raises(ValueError, match="disallowed address"):
        guard.validate_url("http://malicious.example.test/")


@pytest.mark.parametrize(
    "blocked_ip",
    [
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",  # multicast
        "::ffff:10.0.0.1",  # IPv4-mapped pointing into RFC1918
    ],
)
def test_rejects_hostname_resolving_to_blocked_ipv6(guard: SSRFGuard, blocked_ip: str) -> None:
    with _force_resolve([blocked_ip]), pytest.raises(ValueError, match="disallowed address"):
        guard.validate_url("http://malicious.example.test/")


def test_rejects_if_any_resolved_ip_is_blocked(guard: SSRFGuard) -> None:
    """Spec 10: reject if ANY resolved A/AAAA is private — not just the first."""
    with (
        _force_resolve(["93.184.216.34", "10.0.0.1"]),
        pytest.raises(ValueError, match="disallowed address"),
    ):
        guard.validate_url("http://dualstack-evil.example.test/")


def test_rejects_when_resolution_returns_no_addresses(guard: SSRFGuard) -> None:
    with _force_resolve([]), pytest.raises(ValueError, match="did not resolve"):
        guard.validate_url("http://nxdomain.example.test/")


# ---------- public allowlist escape hatch ----------


def test_public_ip_allowlist_permits_specific_literal() -> None:
    guard = SSRFGuard(public_ip_allow={"93.184.216.34"})
    with _force_resolve(["93.184.216.34"]):
        result = guard.validate_url("http://93.184.216.34/")
    assert result == ["93.184.216.34"]


def test_public_ip_allowlist_does_not_bypass_dns_block() -> None:
    """Allowlisting a literal does NOT permit DNS resolution into other
    blocked ranges; the resolved set still must be public."""
    guard = SSRFGuard(public_ip_allow={"93.184.216.34"})
    with _force_resolve(["10.0.0.1"]), pytest.raises(ValueError, match="disallowed address"):
        guard.validate_url("http://something.example.test/")


# ---------- happy path on public IPs ----------


def test_accepts_public_ipv4_resolution(guard: SSRFGuard) -> None:
    with _force_resolve(["8.8.8.8"]):
        result = guard.validate_url("http://dns.google/")
    assert "8.8.8.8" in result


def test_accepts_public_ipv6_resolution(guard: SSRFGuard) -> None:
    with _force_resolve(["2001:4860:4860::8888"]):
        result = guard.validate_url("http://dns.google/")
    assert "2001:4860:4860::8888" in result


# ---------- result shape ----------


def test_returns_sorted_unique_addresses(guard: SSRFGuard) -> None:
    with _force_resolve(["93.184.216.34", "93.184.216.34", "8.8.8.8"]):
        result = guard.validate_url("http://example.com/")
    assert result == sorted(set(result))
    assert len(result) == len(set(result))
