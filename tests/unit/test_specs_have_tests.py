"""Meta-test: every spec must have at least one corresponding test file.

Spec 13 §Invariants: "Each spec is paired with a test file referencing it by
name; the spec-to-test mapping is checked by a meta-test."

This test does NOT enforce per-invariant coverage (the spec's Invariants
section lists many bullets that map to multiple tests). It enforces the
weaker but mechanically-checkable property: every numbered spec doc has at
least one test file whose name matches the spec's slug.

When a spec is added, the test catalog must grow to match.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPECS_DIR = REPO_ROOT / "docs" / "specs"
TESTS_DIR = REPO_ROOT / "tests"

SPEC_FILE_PATTERN = re.compile(r"^(\d{2})-([a-z0-9-]+)\.md$")

# Specs that intentionally do not require a code-level test file because they
# describe orthogonal cross-cutting concerns audited by per-component tests
# and dedicated security tests rather than a single named module.
SPECS_WITHOUT_DIRECT_TEST_FILE = {
    "00-overview",  # system overview — no module to test
    "13-testing",  # this file IS its enforcement
}

# Specs whose implementation has not landed yet. Each entry is a tracked
# gap, not a permanent exemption — remove the slug from this set when its
# implementation PR merges.
SPECS_NOT_YET_IMPLEMENTED: set[str] = set()

# Slug → list of acceptable test-file slug fragments. The spec's name doesn't
# always equal the implementation slug (e.g., 05-llm spec → llm/redaction.py
# test, llm/client.py test, etc.). The mapping below is the source of truth
# for what counts as "a test exists for this spec."
SPEC_TEST_KEYWORDS: dict[str, tuple[str, ...]] = {
    "01-search": ("search",),
    "02-rerank": ("rerank", "bge", "dedup", "diversity"),
    "03-crawl": ("crawl", "ssrf", "envelope"),
    "04-citations": ("citations", "envelope"),
    "05-llm": ("llm", "redaction", "budget"),
    "06-orchestrator": ("orchestrator",),
    "07-mcp": ("mcp",),
    "08-http": ("http",),
    "09-cache": ("cache",),
    "10-security": ("security_selftest", "redaction", "envelope", "ssrf"),
    "11-observability": ("telemetry", "logging"),
    "12-deployment": ("central_config", "main"),
}


def _all_test_file_paths() -> list[Path]:
    return [p for p in TESTS_DIR.rglob("test_*.py") if p.is_file()]


def _spec_slugs() -> list[str]:
    slugs = []
    for path in sorted(SPECS_DIR.iterdir()):
        m = SPEC_FILE_PATTERN.match(path.name)
        if not m:
            continue
        slugs.append(path.stem)
    return slugs


def test_specs_dir_is_populated() -> None:
    slugs = _spec_slugs()
    assert len(slugs) >= 13, f"expected ≥ 13 specs, found {len(slugs)}: {slugs}"


@pytest.mark.parametrize("spec_slug", _spec_slugs())
def test_spec_has_test_file(spec_slug: str) -> None:
    if spec_slug in SPECS_WITHOUT_DIRECT_TEST_FILE:
        pytest.skip(f"{spec_slug} is exempt (cross-cutting; tested elsewhere)")
    if spec_slug in SPECS_NOT_YET_IMPLEMENTED:
        pytest.skip(f"{spec_slug} not yet implemented (tracked gap)")

    keywords = SPEC_TEST_KEYWORDS.get(spec_slug)
    if keywords is None:
        pytest.fail(
            f"spec {spec_slug} has no entry in SPEC_TEST_KEYWORDS — "
            f"add one mapping it to its test slugs"
        )

    test_paths = _all_test_file_paths()
    matching = [
        p
        for p in test_paths
        if any(kw in p.parts for kw in keywords) or any(kw in p.stem for kw in keywords)
    ]
    assert matching, (
        f"spec {spec_slug} has no test files matching any of {keywords}. "
        f"Add at least one test in tests/unit/<area>/ or tests/security/."
    )


def test_security_catalog_is_present() -> None:
    """Spec 13 §Release gate: adversarial catalog must exist."""
    required = {
        "tests/security/test_redaction.py",
        "tests/security/test_envelope.py",
        "tests/security/test_ssrf.py",
    }
    actual = {
        str(p.relative_to(REPO_ROOT)) for p in _all_test_file_paths() if "security" in p.parts
    }
    missing = required - actual
    assert not missing, f"missing required security tests: {missing}"


def test_skips_ledger_exists() -> None:
    ledger = TESTS_DIR / "SKIPS.md"
    assert ledger.exists(), "tests/SKIPS.md is required by Spec 13"
