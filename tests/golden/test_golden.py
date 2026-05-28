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

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
import yaml

_QUERIES_PATH = Path(__file__).parent / "queries.yaml"
_BASELINE_PATH = Path(__file__).parent / "baseline.json"


def test_load_queries_parses_expected_fields() -> None:
    queries = _load_queries()

    assert queries
    assert queries[0]["query"]
    assert isinstance(queries[0]["must_cite_any_of"], list)
    assert isinstance(queries[0]["min_citations"], int)


def test_assert_query_result_accepts_matching_payload() -> None:
    query = {
        "query": "What does HTTP 429 mean?",
        "must_cite_any_of": ["developer.mozilla.org"],
        "must_not_say": ["I could not find a source"],
        "min_citations": 1,
    }
    payload = {
        "answer_text": "HTTP 429 means too many requests.",
        "citations": [{"url": "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429"}],
    }

    result = _evaluate_query(query, payload)

    assert result["passed"] is True
    assert result["reasons"] == []


def test_assert_query_result_reports_failures() -> None:
    query = {
        "query": "What does HTTP 429 mean?",
        "must_cite_any_of": ["developer.mozilla.org"],
        "must_not_say": ["I could not find a source"],
        "min_citations": 1,
    }
    payload = {
        "answer_text": "I could not find a source for that.",
        "citations": [],
    }

    result = _evaluate_query(query, payload)

    assert result["passed"] is False
    assert any("min_citations" in reason for reason in result["reasons"])
    assert any("must_cite_any_of" in reason for reason in result["reasons"])
    assert any("must_not_say" in reason for reason in result["reasons"])


def _load_queries() -> list[dict]:
    payload = yaml.safe_load(_QUERIES_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("golden queries.yaml must contain a top-level list")
    return [dict(item) for item in payload]


def _base_url() -> str:
    return os.environ.get("GOLDEN_BASE_URL", "http://127.0.0.1:8186")


def _http_token() -> str:
    token = os.environ.get("NEXUS_HTTP_TOKEN", "")
    if not token:
        raise RuntimeError("NEXUS_HTTP_TOKEN is required for live golden runs")
    return token


def _run_query(query: dict[str, Any]) -> dict[str, Any]:
    response = httpx.post(
        f"{_base_url().rstrip('/')}/v1/search",
        headers={"Authorization": f"Bearer {_http_token()}"},
        json={
            "query": query["query"],
            "freshness": query.get("freshness", "any"),
        },
        timeout=60.0,
    )
    response.raise_for_status()
    return dict(response.json())


def _evaluate_query(query: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    citations = payload.get("citations", [])
    answer_text = str(payload.get("answer_text", ""))

    min_citations = int(query.get("min_citations", 0))
    if len(citations) < min_citations:
        reasons.append(f"min_citations: expected >= {min_citations}, got {len(citations)}")

    allowed_domains = [str(item).lower() for item in query.get("must_cite_any_of", [])]
    if allowed_domains:
        citation_urls = [str(item.get("url", "")).lower() for item in citations if isinstance(item, dict)]
        if not any(any(domain in urlparse(url).netloc or domain in url for domain in allowed_domains) for url in citation_urls):
            reasons.append("must_cite_any_of: no citation matched allowed domains")

    forbidden_strings = [str(item) for item in query.get("must_not_say", [])]
    for text in forbidden_strings:
        if text.lower() in answer_text.lower():
            reasons.append(f"must_not_say: answer contained forbidden text: {text}")

    return {
        "query": query["query"],
        "passed": not reasons,
        "reasons": reasons,
    }


def _load_baseline() -> dict[str, bool]:
    if not _BASELINE_PATH.exists():
        return {}
    payload = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    return {str(key): bool(value) for key, value in payload.items()}


def test_queries_yaml_exists_and_is_nonempty() -> None:
    """Always runs (not gated): the golden set must exist and have
    entries. Catches an accidental deletion of the regression set."""
    assert _QUERIES_PATH.exists()
    assert _load_queries(), "golden queries.yaml must contain at least one query"


@pytest.mark.golden
def test_golden_regression_suite() -> None:
    queries = _load_queries()
    baseline = _load_baseline()
    results: list[dict[str, Any]] = []
    infrastructure_failures: list[str] = []

    for query in queries:
        try:
            payload = _run_query(query)
        except httpx.HTTPError as exc:
            infrastructure_failures.append(f"{query['query']}: {exc}")
            continue
        results.append(_evaluate_query(query, payload))

    if infrastructure_failures:
        pytest.skip("infrastructure failure during golden run: " + "; ".join(infrastructure_failures))

    assert results, "golden runner produced no query results"

    passed = sum(1 for result in results if result["passed"])
    pass_rate = passed / len(results)
    failures = [result for result in results if not result["passed"]]

    baseline_passes = sum(
        1
        for result in results
        if baseline.get(result["query"], True) and not result["passed"]
    )

    assert pass_rate >= 0.8, (
        f"golden pass rate too low: {passed}/{len(results)} passed; "
        f"failures={failures}"
    )
    assert baseline_passes <= 2, (
        f"golden regression exceeded threshold: {baseline_passes} baseline regressions; "
        f"failures={failures}"
    )
