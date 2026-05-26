from __future__ import annotations

import ipaddress
import socket
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


class SSRFGuard:
    def __init__(self, public_ip_allow: set[str] | None = None) -> None:
        self._public_ip_allow = public_ip_allow or set()

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
        return sorted({info[4][0] for info in infos})

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
