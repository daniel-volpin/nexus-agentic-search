"""Tests for canonical cache key builders (Spec 09 §Invariants)."""

from __future__ import annotations

import re
from datetime import date

import pytest

from nexus.cache.keys import (
    cost_daily_key,
    crawl_doc_key,
    rerank_key,
    robots_key,
    search_key,
)

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


# ---------- shape: every key (except cost_daily) is a 32-char hex digest ----------


def test_search_key_is_hex() -> None:
    key = search_key(query="hello world", freshness="any", max_results=10, lang=None, country=None)
    assert _HEX32.match(key), f"not a 32-char hex digest: {key!r}"


def test_rerank_key_is_hex() -> None:
    key = rerank_key(query="hello", canonical_urls=["https://a", "https://b"])
    assert _HEX32.match(key)


def test_crawl_doc_key_is_hex() -> None:
    key = crawl_doc_key(canonical_url="https://example.com/", render_js=False, max_bytes=1)
    assert _HEX32.match(key)


def test_robots_key_is_hex() -> None:
    key = robots_key(host="example.com")
    assert _HEX32.match(key)


def test_cost_daily_key_is_role_plus_date() -> None:
    # cost_daily is intentionally plaintext: ISO date + role identifier.
    # Both fields are non-secret; operators inspect counters directly.
    key = cost_daily_key(role="synthesis", day=date(2026, 1, 15))
    assert key == "2026-01-15|synthesis"


# ---------- determinism: same inputs ⇒ same key ----------


def test_search_key_deterministic() -> None:
    a = search_key(query="q", freshness="any", max_results=10, lang=None, country=None)
    b = search_key(query="q", freshness="any", max_results=10, lang=None, country=None)
    assert a == b


def test_rerank_key_order_invariant() -> None:
    """URL list order MUST NOT affect the key — caller order is not a signal."""
    a = rerank_key(query="q", canonical_urls=["https://a", "https://b", "https://c"])
    b = rerank_key(query="q", canonical_urls=["https://c", "https://a", "https://b"])
    assert a == b


def test_robots_key_case_insensitive_host() -> None:
    assert robots_key(host="Example.COM") == robots_key(host="example.com")


# ---------- distinctness: different inputs ⇒ different key ----------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # Different query
        (
            dict(query="a", freshness="any", max_results=10, lang=None, country=None),
            dict(query="b", freshness="any", max_results=10, lang=None, country=None),
        ),
        # Different freshness
        (
            dict(query="q", freshness="any", max_results=10, lang=None, country=None),
            dict(query="q", freshness="day", max_results=10, lang=None, country=None),
        ),
        # Different max_results
        (
            dict(query="q", freshness="any", max_results=10, lang=None, country=None),
            dict(query="q", freshness="any", max_results=20, lang=None, country=None),
        ),
        # Different lang
        (
            dict(query="q", freshness="any", max_results=10, lang=None, country=None),
            dict(query="q", freshness="any", max_results=10, lang="en", country=None),
        ),
        # Different country
        (
            dict(query="q", freshness="any", max_results=10, lang=None, country=None),
            dict(query="q", freshness="any", max_results=10, lang=None, country="US"),
        ),
    ],
)
def test_search_key_distinct_for_distinct_inputs(a: dict, b: dict) -> None:
    assert search_key(**a) != search_key(**b)


def test_crawl_doc_key_distinguishes_render_js() -> None:
    a = crawl_doc_key(canonical_url="https://x/", render_js=False, max_bytes=1000)
    b = crawl_doc_key(canonical_url="https://x/", render_js=True, max_bytes=1000)
    assert a != b


def test_crawl_doc_key_distinguishes_max_bytes() -> None:
    a = crawl_doc_key(canonical_url="https://x/", render_js=False, max_bytes=1000)
    b = crawl_doc_key(canonical_url="https://x/", render_js=False, max_bytes=2000)
    assert a != b


# ---------- privacy: no query / URL plaintext appears in keys ----------


def test_search_key_does_not_contain_query_plaintext() -> None:
    distinctive = "ZZZ-distinctive-query-marker-ZZZ"
    key = search_key(query=distinctive, freshness="any", max_results=10, lang=None, country=None)
    assert distinctive not in key


def test_crawl_doc_key_does_not_contain_url_plaintext() -> None:
    url = "https://example.com/very-distinctive-path-ZZZ"
    key = crawl_doc_key(canonical_url=url, render_js=False, max_bytes=1)
    assert "example.com" not in key
    assert "distinctive" not in key


def test_robots_key_does_not_contain_host_plaintext() -> None:
    key = robots_key(host="distinctive-host.example.test")
    assert "distinctive-host" not in key
    assert "example" not in key


def test_rerank_key_does_not_contain_url_plaintext() -> None:
    key = rerank_key(query="q", canonical_urls=["https://distinctive-host-zzz.example.test/"])
    assert "distinctive-host-zzz" not in key
