from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

BLOCKED_V4_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
]
BLOCKED_V6_NETWORKS = [
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:0:0/96"),
    ipaddress.ip_network("ff00::/8"),
]


@dataclass(frozen=True)
class PinnedTarget:
    """A validated connection target. The crawler connects to ``ip`` while
    presenting ``host`` for SNI / cert validation / the Host header — this
    is the connect-with-IP defense against DNS rebinding (Spec 10 §SSRF
    guard): httpx cannot re-resolve the hostname to a different (private)
    address between our check and the connect, because we hand it the
    pinned IP directly.
    """

    ip: str
    host: str
    scheme: str
    port: int
    family: int  # socket.AF_INET / AF_INET6

    @property
    def pinned_url_base(self) -> str:
        """``scheme://<ip-or-[ip]>:port`` — the netloc the crawler connects to."""
        literal = f"[{self.ip}]" if self.family == socket.AF_INET6 else self.ip
        return f"{self.scheme}://{literal}:{self.port}"


class SSRFGuard:
    def __init__(self, public_ip_allow: set[str] | None = None) -> None:
        self._public_ip_allow = public_ip_allow or set()

    def resolve_pinned(self, url: str) -> PinnedTarget:
        """Validate ``url`` and return a :class:`PinnedTarget` whose ``ip``
        is a verified-public address the crawler connects to directly.

        Same checks as :meth:`validate_url` (scheme allowlist, IP-literal
        rejection, every resolved A/AAAA must be public) plus selection of
        the first public IP to pin.
        """
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("url scheme must be http/https")
        if not parsed.hostname:
            raise ValueError("url must include hostname")

        host = parsed.hostname
        literal_ip = self._parse_ip(host)
        if literal_ip is not None and str(literal_ip) not in self._public_ip_allow:
            raise ValueError("ip literals are blocked by default")

        resolved = self.resolve_host(host)
        if not resolved:
            raise ValueError("hostname did not resolve")
        for raw_ip in resolved:
            self._ensure_public_ip(raw_ip)

        chosen = resolved[0]
        family = socket.AF_INET6 if ipaddress.ip_address(chosen).version == 6 else socket.AF_INET
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return PinnedTarget(ip=chosen, host=host, scheme=parsed.scheme, port=port, family=family)

    def validate_url(self, url: str) -> list[str]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("url scheme must be http/https")
        if not parsed.hostname:
            raise ValueError("url must include hostname")

        host = parsed.hostname
        literal_ip = self._parse_ip(host)
        if literal_ip is not None and str(literal_ip) not in self._public_ip_allow:
            raise ValueError("ip literals are blocked by default")

        resolved = self.resolve_host(host)
        if not resolved:
            raise ValueError("hostname did not resolve")
        for raw_ip in resolved:
            self._ensure_public_ip(raw_ip)
        return resolved

    def resolve_host(self, host: str) -> list[str]:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        return sorted({str(info[4][0]) for info in infos})

    def _ensure_public_ip(self, raw_ip: str) -> None:
        ip = ipaddress.ip_address(raw_ip)
        blocked_networks = BLOCKED_V4_NETWORKS if ip.version == 4 else BLOCKED_V6_NETWORKS
        if any(ip in network for network in blocked_networks):
            raise ValueError(f"resolved to disallowed address: {raw_ip}")

    @staticmethod
    def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
        try:
            return ipaddress.ip_address(value)
        except ValueError:
            return None
