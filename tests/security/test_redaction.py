"""Adversarial tests for the secret-redaction filter (Spec 10).

The redaction primitive is the dominant defense against secret leakage in
logs, exception messages, and any propagated text. These tests assert that
every secret pattern documented in Spec 10 is caught, and that the redacted
output never contains the original secret in any form.
"""

from __future__ import annotations

import re

import pytest

from nexus.llm.redaction import _redact_secrets

pytestmark = pytest.mark.security


# ---------- positive coverage: every documented secret pattern is redacted ----------

REDACTED_TOKEN = "[REDACTED]"


@pytest.mark.parametrize(
    ("label", "secret"),
    [
        ("openai_key", "sk-1234567890abcdefghijABCDEFGHIJ"),
        ("openai_key_dashes", "sk-proj-abcdefghij1234567890ZZZZ"),
        ("anthropic_key", "sk-ant-1234567890abcdefghijABCDEF"),
        ("anthropic_key_long", "sk-ant-api03-" + "A" * 40),
        ("google_key", "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ1234567"),
        ("groq_key", "gsk_" + "x" * 50),
        ("bearer_header", "Authorization: Bearer abcdef.tok123.xyz"),
        ("bearer_lowercase_header", "authorization: bearer abcdef.tok123.xyz"),
    ],
)
def test_redacts_known_secret_pattern(label: str, secret: str) -> None:
    body = f"prefix value={secret} suffix"
    redacted = _redact_secrets(body)
    assert REDACTED_TOKEN in redacted, f"{label}: no [REDACTED] marker in output"
    assert secret not in redacted, f"{label}: original secret still present"


def test_redacts_multiple_secrets_in_one_string() -> None:
    blob = "openai=sk-1234567890abcdefghijABCDEFGHIJ anthropic=sk-ant-1234567890abcdefghijZZZZ"
    redacted = _redact_secrets(blob)
    assert "sk-1234567890" not in redacted
    assert "sk-ant-1234567890" not in redacted
    # Two redactions = at least two markers
    assert redacted.count(REDACTED_TOKEN) >= 2


def test_redaction_idempotent() -> None:
    once = _redact_secrets("token=sk-1234567890abcdefghijABCDEFGHIJ")
    twice = _redact_secrets(once)
    assert once == twice


def test_redaction_preserves_surrounding_text() -> None:
    redacted = _redact_secrets("before sk-1234567890abcdefghijABCDEFGHIJ after")
    assert redacted.startswith("before ")
    assert redacted.endswith(" after")


# ---------- negative coverage: non-secret strings are NOT touched ----------


@pytest.mark.parametrize(
    "innocuous",
    [
        "the quick brown fox",
        "sk- not a key just dash",
        "sk-short",  # too short
        "AIzaShort",  # too short for google pattern
        "user@example.com",
        "https://api.search.brave.com/res/v1/web/search?q=hello",
        "Authorization: Basic dXNlcjpwYXNz",  # basic auth not in catalog
    ],
)
def test_does_not_touch_innocuous_text(innocuous: str) -> None:
    assert _redact_secrets(innocuous) == innocuous


# ---------- pattern strictness: redaction MUST consume the entire token ----------


def test_no_partial_redaction_leak() -> None:
    """A redacted token must not leave fragments of the original >= 12 chars."""
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    redacted = _redact_secrets(f"k={secret}")
    # No 12-char-or-longer fragment of the secret survives.
    for length in range(12, len(secret) + 1):
        for start in range(len(secret) - length + 1):
            fragment = secret[start : start + length]
            assert fragment not in redacted, (
                f"fragment {fragment!r} of secret survived redaction: {redacted!r}"
            )


# ---------- safety: redactor must never raise on adversarial input ----------


@pytest.mark.parametrize(
    "weird_input",
    [
        "",
        " ",
        "\x00\x01\x02",
        "a" * 10_000,
        "🦀 sk-1234567890abcdefghijABCDEFGHIJ 🦀",
        "sk-" + "A" * 5000,  # super-long secret
        "\nsk-1234567890abcdefghijABCDEFGHIJ\n",
    ],
)
def test_robust_against_weird_input(weird_input: str) -> None:
    out = _redact_secrets(weird_input)
    # Must produce a string and not raise.
    assert isinstance(out, str)


# ---------- meta: the canonical patterns from Spec 10 are present ----------


def test_spec10_pattern_coverage() -> None:
    """Verify the module exports the patterns Spec 10 mandates.

    If a future refactor removes one, this test fires before adversarial coverage
    silently degrades.
    """
    from nexus.llm import redaction

    catalog = redaction._SECRET_PATTERNS
    pattern_sources = {p.pattern for p in catalog}

    required = {
        r"sk-[A-Za-z0-9_-]{20,}",
        r"sk-ant-[A-Za-z0-9_-]{20,}",
        r"AIza[0-9A-Za-z_-]{35}",
        r"gsk_[A-Za-z0-9]{40,}",
        r"Authorization:\s*Bearer\s+\S+",
    }
    missing = required - pattern_sources
    assert not missing, f"Spec 10 patterns missing from redaction catalog: {missing}"


def test_redacted_marker_is_safe_in_any_context() -> None:
    """The marker must not itself match any secret pattern (would cause loops)."""
    from nexus.llm import redaction

    for pat in redaction._SECRET_PATTERNS:
        assert not pat.search(REDACTED_TOKEN), (
            f"marker {REDACTED_TOKEN!r} matches redaction pattern {pat.pattern!r}"
        )


def test_redactor_uses_compiled_regexes() -> None:
    """Performance + safety: patterns must be precompiled regexes."""
    from nexus.llm import redaction

    for pat in redaction._SECRET_PATTERNS:
        assert isinstance(pat, re.Pattern), f"pattern {pat!r} is not re.Pattern"
