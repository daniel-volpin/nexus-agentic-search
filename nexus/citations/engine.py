from __future__ import annotations

from nexus.crawl.types import Document

from .normalize import normalize_for_match, slice_to_byte_offsets
from .types import Citation, CitationRejection, RawCitation

_MIN_QUOTE_LENGTH = 12
_MAX_QUOTE_LENGTH = 800
_ENVELOPE_OPEN = "<untrusted_source"
_ENVELOPE_CLOSE = "</untrusted_source>"


def validate_citations(
    answer_text: str,
    raw: list[RawCitation],
    documents: dict[str, Document],
) -> tuple[list[Citation], list[CitationRejection]]:
    valid_rows: list[tuple[int, int, int, Citation]] = []
    rejected: list[CitationRejection] = []
    document_order = {content_hash: index for index, content_hash in enumerate(documents)}

    for item in raw:
        document = documents.get(item.content_hash)
        if document is None:
            rejected.append(CitationRejection(raw=item, reason="unknown_document"))
            continue

        quote_length = len(item.quote)
        if quote_length < _MIN_QUOTE_LENGTH:
            rejected.append(CitationRejection(raw=item, reason="quote_too_short"))
            continue
        if quote_length > _MAX_QUOTE_LENGTH:
            rejected.append(CitationRejection(raw=item, reason="quote_too_long"))
            continue

        normalized_quote = normalize_for_match(item.quote)
        normalized_document = normalize_for_match(document.markdown)
        match_start = normalized_document.text.find(normalized_quote.text)
        if match_start < 0:
            rejected.append(CitationRejection(raw=item, reason="quote_not_found"))
            continue

        match_end = match_start + len(normalized_quote.text)
        byte_start, byte_end = slice_to_byte_offsets(normalized_document, match_start, match_end)

        if not _claim_exists(answer_text, item.claim_id):
            rejected.append(CitationRejection(raw=item, reason="claim_unmatched"))
            continue

        if _has_outside_envelope_match(document, normalized_quote.text):
            rejected.append(CitationRejection(raw=item, reason="envelope_violation"))
            continue

        citation = Citation(
            url=item.url,
            content_hash=item.content_hash,
            byte_start=byte_start,
            byte_end=byte_end,
            quote=document.markdown.encode("utf-8")[byte_start:byte_end].decode("utf-8"),
            claim_id=item.claim_id,
        )
        claim_position = _claim_position(answer_text, item.claim_id)
        valid_rows.append(
            (
                claim_position,
                document_order.get(item.content_hash, len(document_order)),
                len(citation.quote),
                citation,
            )
        )

    valid = [row[-1] for row in sorted(valid_rows, key=lambda row: (row[0], row[1], row[2]))]
    return valid, rejected


def _claim_exists(answer_text: str, claim_id: str) -> bool:
    markers = (
        f"[^{claim_id}]",
        f'<cite id="{claim_id}">',
        f"<cite id='{claim_id}'>",
    )
    return any(marker in answer_text for marker in markers)


def _claim_position(answer_text: str, claim_id: str) -> int:
    markers = (
        f"[^{claim_id}]",
        f'<cite id="{claim_id}">',
        f"<cite id='{claim_id}'>",
    )
    positions = [answer_text.find(marker) for marker in markers if answer_text.find(marker) >= 0]
    return min(positions) if positions else len(answer_text)


def _has_outside_envelope_match(document: Document, normalized_quote: str) -> bool:
    text = document.enveloped_markdown
    if not text or _ENVELOPE_OPEN not in text:
        return False

    outside_parts: list[str] = []
    cursor = 0
    while True:
        start = text.find(_ENVELOPE_OPEN, cursor)
        if start < 0:
            outside_parts.append(text[cursor:])
            break

        outside_parts.append(text[cursor:start])
        tag_end = text.find(">", start)
        if tag_end < 0:
            outside_parts.append(text[start:])
            break

        body_end = text.find(_ENVELOPE_CLOSE, tag_end + 1)
        if body_end < 0:
            outside_parts.append(text[start:])
            break

        outside_parts.append(text[start : tag_end + 1])
        cursor = body_end

    outside_normalized = normalize_for_match("".join(outside_parts)).text
    return normalized_quote in outside_normalized
