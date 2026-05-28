"""Golden-quality regression suite (Spec 13 §Golden suite).

Skipped unless GOLDEN_LIVE=1 (see conftest.py). When live, each entry
in queries.yaml is run end-to-end against the service and asserted
for citation behavior.

This is a SCAFFOLD: the live runner is intentionally minimal until
Spec 01/02/03 hardening makes the end-to-end path return real
results. The structure (load YAML → per-query assertions → pass-rate
gate) is what lands now so subsequent hardening PRs have a regression
harness to fill in.
"""

from __future__ import annotations

import tomllib  # noqa: F401  # placeholder import kept for parity; yaml parsed below
from pathlib import Path

import pytest

_QUERIES_PATH = Path(__file__).parent / "queries.yaml"


def _load_queries() -> list[dict]:
    # Minimal YAML read without adding a yaml dependency: the file is a
    # simple list-of-mappings. When the live runner is implemented this
    # switches to a real parser. For now we only assert structure.
    text = _QUERIES_PATH.read_text(encoding="utf-8")
    # Count top-level list entries ("- query:") as a structural check.
    return [{"raw_block": block} for block in text.split("\n- ") if "query:" in block]


def test_queries_yaml_exists_and_is_nonempty() -> None:
    """Always runs (not gated): the golden set must exist and have
    entries. Catches an accidental deletion of the regression set."""
    assert _QUERIES_PATH.exists()
    assert _load_queries(), "golden queries.yaml must contain at least one query"


@pytest.mark.golden
def test_golden_regression_suite() -> None:
    """Live end-to-end golden run. Skipped unless GOLDEN_LIVE=1.

    TODO(spec-01/02/03 hardening): implement the live runner — POST
    each query to the running service, assert `must_cite_any_of`,
    `min_citations`, `must_not_say`; require ≥ 80% pass rate with no
    regression > 2 from tests/golden/baseline.json.
    """
    pytest.skip("live golden runner pending Spec 01/02/03 component hardening")
