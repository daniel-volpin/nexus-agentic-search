from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class NormalizedText:
    text: str
    spans: list[tuple[int, int]]
    byte_offsets: list[int]


def normalize_for_match(text: str) -> NormalizedText:
    nfc_text = unicodedata.normalize("NFC", text)
    nfc_spans = _build_nfc_spans(text, nfc_text)
    byte_offsets = _build_byte_offsets(text)

    normalized_chars: list[str] = []
    normalized_spans: list[tuple[int, int]] = []

    for char, span in zip(nfc_text, nfc_spans, strict=False):
        if char.isspace():
            if normalized_chars and normalized_chars[-1] == " ":
                start, _ = normalized_spans[-1]
                normalized_spans[-1] = (start, span[1])
            else:
                normalized_chars.append(" ")
                normalized_spans.append(span)
            continue

        lowered = char.lower()
        for lowered_char in lowered:
            normalized_chars.append(lowered_char)
            normalized_spans.append(span)

    return NormalizedText(
        text="".join(normalized_chars),
        spans=normalized_spans,
        byte_offsets=byte_offsets,
    )


def slice_to_byte_offsets(normalized: NormalizedText, start: int, end: int) -> tuple[int, int]:
    start_char = normalized.spans[start][0]
    end_char = normalized.spans[end - 1][1]
    return normalized.byte_offsets[start_char], normalized.byte_offsets[end_char]


def _build_byte_offsets(text: str) -> list[int]:
    offsets = [0]
    total = 0
    for char in text:
        total += len(char.encode("utf-8"))
        offsets.append(total)
    return offsets


def _build_nfc_spans(original: str, normalized: str) -> list[tuple[int, int]]:
    matcher = SequenceMatcher(a=original, b=normalized, autojunk=False)
    spans: list[tuple[int, int]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(j2 - j1):
                spans.append((i1 + offset, i1 + offset + 1))
            continue

        if tag == "delete":
            continue

        start = i1
        end = i2
        if start == end and start < len(original):
            end = start + 1

        for _ in range(j1, j2):
            spans.append((start, end))

    if len(spans) != len(normalized):
        raise ValueError("failed to build normalization spans")

    return spans
