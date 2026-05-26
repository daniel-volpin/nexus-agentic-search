from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import time


@dataclass
class RateLimiter:
    token_requests: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    ip_requests: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    token_reset_at: float = field(default_factory=time.time)
    ip_reset_at: float = field(default_factory=time.time)

    def allow(self, *, token: str, client_ip: str, token_limit: int, ip_limit: int) -> bool:
        now = time.time()
        if now - self.token_reset_at >= 60:
            self.token_requests.clear()
            self.token_reset_at = now
        if now - self.ip_reset_at >= 60:
            self.ip_requests.clear()
            self.ip_reset_at = now

        if self.token_requests[token] >= token_limit:
            return False
        if self.ip_requests[client_ip] >= ip_limit:
            return False

        self.token_requests[token] += 1
        self.ip_requests[client_ip] += 1
        return True
