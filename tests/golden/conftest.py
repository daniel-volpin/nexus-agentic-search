"""Gate the golden suite behind GOLDEN_LIVE=1 (Spec 13 §Golden suite).

The golden suite makes real LLM calls and crawls live web pages — it
costs money and needs API keys + a hardened search/crawl path. It is
skipped by default so `make test` stays free and offline.

The hook receives the FULL collected item set, so we scope the skip
to items located under this directory AND carrying the `golden`
marker — never the rest of the suite.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_GOLDEN_DIR = Path(__file__).parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("GOLDEN_LIVE") == "1":
        return
    skip = pytest.mark.skip(
        reason="golden suite requires GOLDEN_LIVE=1 + API keys + hardened search path"
    )
    for item in items:
        if _GOLDEN_DIR in Path(str(item.fspath)).parents and item.get_closest_marker("golden"):
            item.add_marker(skip)


def pytest_runtest_setup(item: pytest.Item) -> None:
    if _GOLDEN_DIR not in Path(str(item.fspath)).parents:
        return
    if not item.get_closest_marker("golden"):
        return
    if os.environ.get("GOLDEN_LIVE") != "1":
        return
    if not os.environ.get("NEXUS_HTTP_TOKEN"):
        pytest.skip("golden suite requires NEXUS_HTTP_TOKEN when GOLDEN_LIVE=1")
