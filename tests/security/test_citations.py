"""Adversarial tests for the citations validator (Spec 10 / Spec 04).

Complements ``tests/unit/citations/test_engine.py`` with explicit
attacker scenarios: fabrication, envelope violation, ReDoS resistance,
and length-cap enforcement.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nexus.citations import RawCitation, validate_citations
from nexus.crawl.envelope import wrap_untrusted
from nexus.crawl.types import Document

pytestmark = pytest.mark.security


def _document(
    *,
    markdown: str,
    content_hash: str = "doc-hash",
    envelope_url: str = "https://example.com/article",
) -> Document:
    enveloped = wrap_untrusted(envelope_url, content_hash, markdown)
    return Document(
        url=envelope_url,
        requested_url=envelope_url,
        content_hash=content_hash,
        markdown=markdown,
        enveloped_markdown=enveloped,
        content_type="text/markdown",
        fetched_at=datetime.now(UTC),
        status="ok",
        http_status=200,
        bytes_in=len(markdown.encode("utf-8")),
        render_ms=1,
        extraction_ms=1,
        redirect_chain=[envelope_url],
    )


# ---------- citation fabrication (unknown document) ----------


def test_citation_to_unknown_document_rejected() -> None:
    """An attacker (or hallucinating model) provides a content_hash
    that does not correspond to any document the orchestrator fetched."""
    doc = _document(markdown="Real article about Python.")
    raw = [
        RawCitation(
            url="https://attacker.example/",
            content_hash="never-fetched-hash",
            quote="this quote never existed anywhere at all",
            claim_id="c1",
        )
    ]
    valid, rejected = validate_citations("Some answer [^c1]", raw, {doc.content_hash: doc})
    assert valid == []
    assert rejected
    assert rejected[0].reason == "unknown_document"


# ---------- quote fabrication ----------


def test_citation_with_quote_not_in_document_rejected() -> None:
    doc = _document(markdown="The cat sat on the mat.")
    raw = [
        RawCitation(
            url=doc.url,
            content_hash=doc.content_hash,
            quote="this quote does not appear in the document",
            claim_id="c1",
        )
    ]
    valid, rejected = validate_citations("answer [^c1]", raw, {doc.content_hash: doc})
    assert valid == []
    assert rejected
    assert rejected[0].reason == "quote_not_found"


# ---------- length caps ----------


def test_quote_below_min_length_rejected() -> None:
    doc = _document(markdown="some text content here")
    raw = [
        RawCitation(
            url=doc.url,
            content_hash=doc.content_hash,
            quote="short",  # < 12 chars
            claim_id="c1",
        )
    ]
    _, rejected = validate_citations("answer [^c1]", raw, {doc.content_hash: doc})
    assert rejected
    assert rejected[0].reason == "quote_too_short"


def test_quote_above_max_length_rejected() -> None:
    long_quote = "A" * 801
    doc = _document(markdown=long_quote * 2)
    raw = [
        RawCitation(
            url=doc.url,
            content_hash=doc.content_hash,
            quote=long_quote,
            claim_id="c1",
        )
    ]
    _, rejected = validate_citations("answer [^c1]", raw, {doc.content_hash: doc})
    assert rejected
    assert rejected[0].reason == "quote_too_long"


# ---------- envelope violation: quote appears outside any untrusted_source tag ----------


def test_quote_matching_envelope_metadata_rejected() -> None:
    """A model that cites the literal URL attribute (which lives OUTSIDE
    the envelope body) is fabricating — it's quoting boilerplate, not
    the document content. Must be rejected as envelope_violation."""
    url = "https://example.com/article-with-very-distinctive-slug-zzz"
    doc = _document(
        markdown="Real document body here. Nothing about the URL.",
        envelope_url=url,
    )
    raw = [
        RawCitation(
            url=url,
            content_hash=doc.content_hash,
            quote=url,  # the URL appears in the envelope open tag, not body
            claim_id="c1",
        )
    ]
    _, rejected = validate_citations("answer [^c1]", raw, {doc.content_hash: doc})
    assert rejected
    reasons = {r.reason for r in rejected}
    # Either "quote_not_found" (quote isn't in markdown) or
    # "envelope_violation" (quote is in envelope metadata) — both
    # correctly refuse to surface a fabricated citation.
    assert reasons & {"envelope_violation", "quote_not_found"}, reasons


# ---------- ReDoS resistance: regex metacharacters in quote ----------


@pytest.mark.parametrize(
    "regex_payload",
    [
        "(.+)+",
        "(a|a)*",
        "(a*)*",
        "([a-z]+)*",
        ".*.*.*.*.*.*.*.*.*.*",
        "(?:" * 50 + "x" + ")?" * 50,
    ],
)
def test_regex_payload_in_quote_does_not_redos(regex_payload: str) -> None:
    """The validator MUST use literal substring search on `quote` — not
    regex compilation. ReDoS payloads as quotes must NOT cause runaway
    CPU."""
    doc = _document(markdown="benign document content")
    raw = [
        RawCitation(
            url=doc.url,
            content_hash=doc.content_hash,
            quote=regex_payload * 3,  # ensure > 12 chars
            claim_id="c1",
        )
    ]
    import time

    start = time.perf_counter()
    valid, _rejected = validate_citations("answer [^c1]", raw, {doc.content_hash: doc})
    elapsed = time.perf_counter() - start
    # Must complete in well under a second; ReDoS would take many s.
    assert elapsed < 0.5, f"ReDoS-shaped quote took {elapsed:.3f}s"
    assert valid == []  # quote doesn't appear in the doc, properly rejected


# ---------- claim id forgery ----------


def test_quote_matched_but_claim_not_in_answer_rejected() -> None:
    """Even if the quote is genuine, if the answer text doesn't contain
    the corresponding `[^cN]` marker the citation is dropped — the
    model is binding to a claim it never made."""
    doc = _document(markdown="The library was released in 2024.")
    raw = [
        RawCitation(
            url=doc.url,
            content_hash=doc.content_hash,
            quote="The library was released",  # ≥ 12 chars, real quote
            claim_id="c-no-such-claim",
        )
    ]
    valid, rejected = validate_citations(
        "answer without any claim markers", raw, {doc.content_hash: doc}
    )
    assert valid == []
    assert rejected
    assert rejected[0].reason == "claim_unmatched"


# ---------- empty inputs ----------


def test_empty_raw_returns_no_valid_no_rejected() -> None:
    doc = _document(markdown="text")
    valid, rejected = validate_citations("answer", [], {doc.content_hash: doc})
    assert valid == []
    assert rejected == []


def test_empty_documents_dict_rejects_everything() -> None:
    raw = [
        RawCitation(
            url="https://x/",
            content_hash="any",
            quote="some quote here",
            claim_id="c1",
        )
    ]
    valid, rejected = validate_citations("answer [^c1]", raw, {})
    assert valid == []
    assert len(rejected) == 1
    assert rejected[0].reason == "unknown_document"
