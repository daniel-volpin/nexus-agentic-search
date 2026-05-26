# Plan 04 — Citations Engine

> Spec: [`docs/specs/04-citations.md`](../specs/04-citations.md) · spec wins on disagreement.

## Module layout

```
nexus/citations/
├── validator.py        # validate_citations() entry
├── normalize.py        # text normalization + offset-preserving map
└── types.py            # Citation, RawCitation, CitationRejection

tests/unit/citations/
├── test_validator.py
├── test_normalize.py
├── test_byte_offset_roundtrip.py
└── test_envelope_violation.py
tests/security/test_citations.py
```

## Public symbols

```python
# nexus/citations/types.py
@dataclass(frozen=True)
class RawCitation:
    url: str
    content_hash: str
    quote: str
    claim_id: str

@dataclass(frozen=True)
class Citation:
    url: str
    content_hash: str
    byte_start: int
    byte_end: int
    quote: str         # original-case
    claim_id: str

class CitationRejection: ...

# nexus/citations/normalize.py
def normalize_for_match(s: str) -> tuple[str, list[int]]:
    """Return (normalized_string, original_byte_offset_per_char) so a
    matched substring in the normalized form can be mapped back to byte
    offsets in the original."""

# nexus/citations/validator.py
def validate_citations(
    answer_text: str,
    raw: list[RawCitation],
    documents: dict[str, Document],   # keyed by content_hash
) -> tuple[list[Citation], list[CitationRejection]]: ...
```

## External dependencies

| Package | Why |
|---|---|
| stdlib `unicodedata` | NFC normalization. |
| stdlib `re` | Tokenizer ONLY (NEVER for `raw.quote` matching — substring search is literal). |

No third-party deps. Citation engine is intentionally minimal.

## Build order

1. **Types** (`types.py`). `RawCitation`, `Citation`, `CitationRejection` as `dataclass(frozen=True)`. ➜ `test_types.py` covers immutability.
2. **Normalization with offset map** (`normalize.py`). Walk the original UTF-8 string char-by-char; emit `(normalized_char, original_byte_index)` pairs. Whitespace runs collapse to a single space; the offset recorded is the offset of the first whitespace char of the run. NFC pre-normalization. Lower-case for matching only. ➜ `test_normalize.py` covers:
   - ASCII roundtrip.
   - Multi-byte characters (Cyrillic, CJK).
   - Whitespace runs collapsing (newlines, tabs, mixed).
   - NFC decomposition (composed vs decomposed input → same normalized form).
   - Empty string.
3. **Quote location** (`validator.py` internal). Given normalized quote and normalized document, find first occurrence with `str.find()` (literal substring — NO regex on user-controlled `quote`). Map back via the offset table to original byte_start / byte_end. ➜ `test_byte_offset_roundtrip.py`:
   - For 50 random valid citations, assert `markdown.encode()[byte_start:byte_end].decode()` normalizes to the same form as `quote` normalizes to. Hypothesis-style property test.
4. **Envelope-violation detection**. Build a single "system text" pool containing the synthesis preamble and any boilerplate (Spec 10). Reject if the quote, after normalization, appears in that pool. ➜ `test_envelope_violation.py`.
5. **`validate_citations()` entry point**.
   - Iterate `raw`. For each:
     1. Lookup `documents[raw.content_hash]` → if absent: reject `unknown_document`.
     2. Length checks on `raw.quote` → reject too-short / too-long.
     3. Normalize quote + document.
     4. Run envelope-violation check.
     5. Locate substring. If not found → reject `quote_not_found`.
     6. Map back to byte offsets. Build `Citation` with original-case quote (extracted from `document.markdown[byte_start:byte_end]`).
     7. Verify `claim_id` appears in `answer_text` (search for `[^{claim_id}]` or `<cite id="{claim_id}">` patterns). If missing → reject `claim_unmatched`.
   - Telemetry span `citations.validate` with attributes (Spec 04).
   - Per-citation work bounded by 200ms; over → reject `quote_not_found`.
6. **Wire into orchestrator** (Plan 06).

## Test plan (mapping to spec invariants)

| Spec invariant | Test |
|---|---|
| `markdown.encode()[s:e].decode().normalize() == quote.normalize()` | `test_byte_offset_roundtrip.py` (property test) |
| `byte_start < byte_end ≤ len(markdown.encode())` | same |
| `content_hash` matches exactly one input doc | `test_validator::test_unknown_document` |
| Rejected citations returned, not silently dropped | `test_validator::test_rejections_returned` |
| Literal substring search (no regex on quote) | `test_validator::test_regex_chars_in_quote_no_redos` |
| Envelope-violation rejection | `test_envelope_violation::test_quote_in_preamble_rejected` |
| Length caps | `test_validator::test_quote_length_bounds` |
| Per-citation 200ms ceiling | `test_validator::test_per_citation_timeout` (synthetic large doc) |
| All-rejected case returns `([], [...])` | `test_validator::test_all_rejected` |

## Adversarial tests required (Spec 13)

`tests/security/test_citations.py` covers:
- ReDoS resistance with quotes containing `(.+)+`, `(a|a)*`, nested groups.
- Unicode confusables (NFKC vs NFC trip): citation that matches in NFKC but not NFC must be rejected (we use NFC).
- Bidi-override chars in the quote: stripped before matching; if quote becomes empty after stripping → rejected `quote_too_short`.
- Citation pointing to a fabricated `content_hash`: rejected `unknown_document`.
- Citation quoting boilerplate from the synthesis preamble: rejected `envelope_violation`.
- Citation quoting a multi-byte character split mid-byte (offset off-by-one in our table): asserted to not happen by the property test.

## Risks & mitigations

- **Off-by-one in offset map** breaks roundtrip. Mitigation: property-test with 100 random documents.
- **Whitespace-collapse asymmetry**: if quote has fewer whitespace chars than the document at the same location, we record the start of the run. Decode roundtrip: re-normalize the byte slice and compare to normalized quote — passes by construction if `normalize_for_match` is consistent.
- **Quote normalization changes length** (e.g., ligature → 2 chars): handled by the offset table being char-indexed in the *normalized* form pointing at the *original* byte index of each normalized char; the byte slice is from `offsets[first_match_idx]` to `offsets[last_match_idx] + bytes_of_last_original_char`.

## Done criteria
- [ ] All unit + security tests pass.
- [ ] Property test (`test_byte_offset_roundtrip`) runs 200 random cases with no failures.
- [ ] Envelope-violation rejection triggers an INFO log (security-sensitive) and increments `citations_envelope_violations_total`.
- [ ] `mypy --strict` clean.
