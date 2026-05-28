"""Tests for the module-level namespace lifecycle (Spec 09)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.cache import namespaces


@pytest.fixture(autouse=True)
def _isolate_namespaces() -> None:
    """Ensure every test starts and ends with namespace handles cleared."""
    namespaces.shutdown_cache()
    yield
    namespaces.shutdown_cache()


def test_namespaces_are_none_before_setup() -> None:
    assert namespaces.SEARCH_BRAVE is None
    assert namespaces.SEARCH_SEARXNG is None
    assert namespaces.RERANK_BGE is None
    assert namespaces.CRAWL_DOCUMENT is None
    assert namespaces.CRAWL_ROBOTS is None
    assert namespaces.COST_DAILY is None


def test_setup_populates_all_namespaces(tmp_path: Path) -> None:
    namespaces.setup_cache(tmp_path, total_size_gb=0.01)

    assert namespaces.SEARCH_BRAVE is not None
    assert namespaces.SEARCH_SEARXNG is not None
    assert namespaces.RERANK_BGE is not None
    assert namespaces.CRAWL_DOCUMENT is not None
    assert namespaces.CRAWL_ROBOTS is not None
    assert namespaces.COST_DAILY is not None


def test_setup_assigns_expected_namespace_names(tmp_path: Path) -> None:
    namespaces.setup_cache(tmp_path, total_size_gb=0.01)
    assert namespaces.SEARCH_BRAVE.namespace == "search.brave"
    assert namespaces.SEARCH_SEARXNG.namespace == "search.searxng"
    assert namespaces.RERANK_BGE.namespace == "rerank.bge"
    assert namespaces.CRAWL_DOCUMENT.namespace == "crawl.document"
    assert namespaces.CRAWL_ROBOTS.namespace == "crawl.robots"
    assert namespaces.COST_DAILY.namespace == "cost.daily"


def test_setup_uses_distinct_subdirectories(tmp_path: Path) -> None:
    namespaces.setup_cache(tmp_path, total_size_gb=0.01)
    expected = {
        "search.brave",
        "search.searxng",
        "rerank.bge",
        "crawl.document",
        "crawl.robots",
        "cost.daily",
    }
    actual = {p.name for p in tmp_path.iterdir() if p.is_dir()}
    assert expected == actual


def test_setup_idempotent(tmp_path: Path) -> None:
    """Two setup calls replace the handles cleanly — tests rely on this."""
    namespaces.setup_cache(tmp_path, total_size_gb=0.01)
    first = namespaces.SEARCH_BRAVE
    namespaces.setup_cache(tmp_path, total_size_gb=0.01)
    second = namespaces.SEARCH_BRAVE
    assert first is not second  # new instance


def test_versions_match_namespace_types_module(tmp_path: Path) -> None:
    namespaces.setup_cache(tmp_path, total_size_gb=0.01)
    from nexus.cache.types import (
        COST_DAILY_VERSION,
        CRAWL_DOCUMENT_VERSION,
        CRAWL_ROBOTS_VERSION,
        RERANK_BGE_VERSION,
        SEARCH_BRAVE_VERSION,
        SEARCH_SEARXNG_VERSION,
    )

    assert namespaces.SEARCH_BRAVE.version == SEARCH_BRAVE_VERSION
    assert namespaces.SEARCH_SEARXNG.version == SEARCH_SEARXNG_VERSION
    assert namespaces.RERANK_BGE.version == RERANK_BGE_VERSION
    assert namespaces.CRAWL_DOCUMENT.version == CRAWL_DOCUMENT_VERSION
    assert namespaces.CRAWL_ROBOTS.version == CRAWL_ROBOTS_VERSION
    assert namespaces.COST_DAILY.version == COST_DAILY_VERSION


def test_shutdown_resets_handles_to_none(tmp_path: Path) -> None:
    namespaces.setup_cache(tmp_path, total_size_gb=0.01)
    namespaces.shutdown_cache()
    assert namespaces.SEARCH_BRAVE is None
    assert namespaces.SEARCH_SEARXNG is None
    assert namespaces.RERANK_BGE is None
    assert namespaces.CRAWL_DOCUMENT is None
    assert namespaces.CRAWL_ROBOTS is None
    assert namespaces.COST_DAILY is None


def test_shutdown_idempotent() -> None:
    namespaces.shutdown_cache()  # already nothing to close
    namespaces.shutdown_cache()  # must not raise


async def test_namespaces_are_functional_end_to_end(tmp_path: Path) -> None:
    namespaces.setup_cache(tmp_path, total_size_gb=0.01)
    assert namespaces.SEARCH_BRAVE is not None
    await namespaces.SEARCH_BRAVE.set("k", {"results": []})
    got = await namespaces.SEARCH_BRAVE.get("k")
    assert got == {"results": []}
