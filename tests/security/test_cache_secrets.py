"""Adversarial tests for the cache layer's secret-handling invariant
(Spec 10 / Spec 09).

The cache stores values verbatim — it cannot redact what it cannot
inspect. The invariant is structural: callers MUST NOT put secrets in
cached values. These tests assert (a) the cache key format itself
exposes nothing, and (b) the round-trip preserves the contract: if a
secret somehow ends up in a cached value, we'd catch it via the
redaction filter when that value is logged.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from nexus.cache import DiskCacheBackend, setup_cache, shutdown_cache
from nexus.cache.keys import (
    cost_daily_key,
    crawl_doc_key,
    rerank_key,
    robots_key,
    search_key,
)
from nexus.llm.redaction import _redact_secrets

pytestmark = pytest.mark.security


# ---------- key shape — keys themselves leak nothing ----------


_SECRET_INPUTS = [
    "sk-1234567890abcdefghijABCDEFGHIJ",
    "sk-ant-1234567890abcdefghijZZZZ",
    "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ1234567",
]


@pytest.mark.parametrize("secret", _SECRET_INPUTS)
def test_search_key_does_not_echo_secret_in_query(secret: str) -> None:
    """If a misbehaving caller passed a secret as the query, the cache
    key MUST NOT echo it (hex digest, not plaintext)."""
    key = search_key(
        query=f"how to use {secret}",
        freshness="any",
        max_results=10,
        lang=None,
        country=None,
    )
    assert secret not in key


@pytest.mark.parametrize("secret", _SECRET_INPUTS)
def test_rerank_key_does_not_echo_secret_in_urls(secret: str) -> None:
    key = rerank_key(query="q", canonical_urls=[f"https://x/?{secret}"])
    assert secret not in key


@pytest.mark.parametrize("secret", _SECRET_INPUTS)
def test_crawl_doc_key_does_not_echo_secret_in_url(secret: str) -> None:
    key = crawl_doc_key(canonical_url=f"https://x/?{secret}", render_js=False, max_bytes=1)
    assert secret not in key


@pytest.mark.parametrize("secret", _SECRET_INPUTS)
def test_robots_key_does_not_echo_secret_in_host(secret: str) -> None:
    key = robots_key(host=f"{secret.lower()}.example.com")
    assert secret.lower() not in key


def test_cost_daily_key_uses_only_role_and_date() -> None:
    """`cost_daily` is intentionally plaintext but contains ONLY the
    role identifier + ISO date — no query, no URL, no token."""
    key = cost_daily_key(role="synthesis", day=date(2026, 1, 15))
    assert key == "2026-01-15|synthesis"
    # Role names are short ASCII identifiers; tokens never go here.
    assert len(key) < 64


# ---------- value round-trip preserves caller's data verbatim ----------
#
# The cache stores values as-is. The defense is upstream: callers MUST
# NOT put secrets in cached values. We assert the round-trip preserves
# whatever we wrote so the contract is testable (no silent mutation),
# and we add a redaction-coverage check: if a secret DID land in a
# cached value and that value were logged, the redactor catches it.


async def test_roundtrip_preserves_safe_value(tmp_path: Path) -> None:
    backend = DiskCacheBackend(
        root=tmp_path,
        namespace="test",
        version=1,
        ttl_default_s=60,
        size_limit_bytes=1024 * 1024,
    )
    payload = {"results": [{"url": "https://example.com", "title": "ok"}]}
    await backend.set("k", payload)
    assert await backend.get("k") == payload


async def test_secret_in_value_would_be_redacted_on_log_emission(
    tmp_path: Path,
) -> None:
    """Defense-in-depth: even if a misbehaving caller put a secret in
    a cached value, when that value is serialised and logged the
    redaction filter catches it. This proves the layered defense holds."""
    backend = DiskCacheBackend(
        root=tmp_path,
        namespace="test",
        version=1,
        ttl_default_s=60,
        size_limit_bytes=1024 * 1024,
    )
    secret = "sk-1234567890abcdefghijABCDEFGHIJ"
    payload = {"key": secret}
    await backend.set("k", payload)
    got = await backend.get("k")
    # The cache returns the value verbatim (it's not the cache's job
    # to mutate). The redactor — applied when this is *logged* —
    # catches it.
    serialized = json.dumps(got)
    assert secret in serialized  # cache preserved fidelity
    assert secret not in _redact_secrets(serialized)  # log path would redact


# ---------- namespace lifecycle does not log secrets ----------


def test_setup_and_shutdown_do_not_emit_secret_patterns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with caplog.at_level(logging.DEBUG):
        setup_cache(tmp_path, total_size_gb=0.01)
        shutdown_cache()
    # No emitted log line matches any secret pattern.
    for record in caplog.records:
        text = record.getMessage()
        assert _redact_secrets(text) == text, f"would have redacted: {text!r}"
