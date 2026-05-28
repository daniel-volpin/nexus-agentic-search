from __future__ import annotations

from datetime import UTC, datetime

from nexus.citations import RawCitation, validate_citations
from nexus.crawl.types import Document


def make_document(
    *,
    markdown: str,
    content_hash: str = "doc-hash",
    enveloped_markdown: str | None = None,
) -> Document:
    return Document(
        url="https://example.com/article",
        requested_url="https://example.com/article",
        content_hash=content_hash,
        markdown=markdown,
        enveloped_markdown=enveloped_markdown or markdown,
        content_type="text/markdown",
        fetched_at=datetime.now(UTC),
        status="ok",
        http_status=200,
        bytes_in=len(markdown.encode("utf-8")),
        render_ms=1,
        extraction_ms=1,
        redirect_chain=["https://example.com/article"],
    )


def test_validate_citations_returns_byte_offsets_for_normalized_quote() -> None:
    document = make_document(markdown="Title\n\nCafe\u0301   society")
    raw = [
        RawCitation(
            url=document.url,
            content_hash=document.content_hash,
            quote="title cafe\u0301 society",
            claim_id="claim-1",
        )
    ]

    valid, rejected = validate_citations(
        answer_text="Answer with support [^claim-1]",
        raw=raw,
        documents={document.content_hash: document},
    )

    assert rejected == []
    assert len(valid) == 1
    citation = valid[0]
    assert citation.byte_start < citation.byte_end
    slice_text = document.markdown.encode("utf-8")[citation.byte_start : citation.byte_end].decode(
        "utf-8"
    )
    assert slice_text == document.markdown


def test_validate_citations_rejects_unknown_document() -> None:
    raw = [
        RawCitation(
            url="https://example.com/missing",
            content_hash="missing",
            quote="A sufficiently long quote",
            claim_id="claim-1",
        )
    ]

    valid, rejected = validate_citations(
        answer_text="Answer with support [^claim-1]",
        raw=raw,
        documents={},
    )

    assert valid == []
    assert [item.reason for item in rejected] == ["unknown_document"]


def test_validate_citations_rejects_short_and_long_quotes() -> None:
    document = make_document(markdown="A sufficiently long quote appears here for validation.")
    raw = [
        RawCitation(
            url=document.url,
            content_hash=document.content_hash,
            quote="too short",
            claim_id="claim-short",
        ),
        RawCitation(
            url=document.url,
            content_hash=document.content_hash,
            quote="x" * 801,
            claim_id="claim-long",
        ),
    ]

    valid, rejected = validate_citations(
        answer_text="Answer [^claim-short] [^claim-long]",
        raw=raw,
        documents={document.content_hash: document},
    )

    assert valid == []
    assert [item.reason for item in rejected] == ["quote_too_short", "quote_too_long"]


def test_validate_citations_rejects_unmatched_claim_id() -> None:
    document = make_document(markdown="A sufficiently long quote appears here for validation.")
    raw = [
        RawCitation(
            url=document.url,
            content_hash=document.content_hash,
            quote="A sufficiently long quote",
            claim_id="claim-1",
        )
    ]

    valid, rejected = validate_citations(
        answer_text="Answer without any supported marker",
        raw=raw,
        documents={document.content_hash: document},
    )

    assert valid == []
    assert [item.reason for item in rejected] == ["claim_unmatched"]


def test_validate_citations_rejects_quote_not_found() -> None:
    document = make_document(markdown="A sufficiently long quote appears here for validation.")
    raw = [
        RawCitation(
            url=document.url,
            content_hash=document.content_hash,
            quote="This quote does not appear",
            claim_id="claim-1",
        )
    ]

    valid, rejected = validate_citations(
        answer_text="Answer with support [^claim-1]",
        raw=raw,
        documents={document.content_hash: document},
    )

    assert valid == []
    assert [item.reason for item in rejected] == ["quote_not_found"]


def test_validate_citations_rejects_quote_outside_untrusted_envelope() -> None:
    document = make_document(
        markdown="A sufficiently long quote appears here for validation.",
        enveloped_markdown=(
            "system boilerplate A sufficiently long quote appears here for validation.\n"
            '<untrusted_source url="https://example.com/article" sha256="doc-hash">'
            "A sufficiently long quote appears here for validation."
            "</untrusted_source>"
        ),
    )
    raw = [
        RawCitation(
            url=document.url,
            content_hash=document.content_hash,
            quote="A sufficiently long quote appears here for validation.",
            claim_id="claim-1",
        )
    ]

    valid, rejected = validate_citations(
        answer_text="Answer with support [^claim-1]",
        raw=raw,
        documents={document.content_hash: document},
    )

    assert valid == []
    assert [item.reason for item in rejected] == ["envelope_violation"]
