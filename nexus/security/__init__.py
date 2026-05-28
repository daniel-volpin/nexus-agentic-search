"""Security cross-cutting module.

Most security primitives live in their component owners:

- SSRF guard → :mod:`nexus.crawl.ssrf`
- Untrusted-source envelope → :mod:`nexus.crawl.envelope`
- Secret redaction → :mod:`nexus.llm.redaction`
- Bearer auth → :mod:`nexus.http.auth` / :mod:`nexus.mcp` server
- Citation envelope-violation reject → :mod:`nexus.citations.engine`
- Budget / cost cap → :mod:`nexus.llm.budget`, :mod:`nexus.llm.client`

This package adds only the things that span all of those:

- :mod:`nexus.security.selftest` — runtime startup checks asserting the
  invariants hold at boot. Called from the service entrypoint before
  transports begin accepting traffic.
"""

from .selftest import (
    SelftestFailure,
    SelftestReport,
    run_selftest,
)

__all__ = [
    "SelftestFailure",
    "SelftestReport",
    "run_selftest",
]
