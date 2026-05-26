# Spec 04 — Citations Engine

## Purpose
Bind every claim in the synthesized answer to a verified text span in a fetched document; reject answers whose citations cannot be verified.

## Bounded context

**Does**
- Accept `(answer_text, raw_citations[], documents[])` from the orchestrator.
- Validate each raw citation against the named document.
- Return only verified citations; drop unverifiable ones.
- Re-rank citations within the response by document relevance and quote length.

**Does NOT**
- Generate citations (synthesis does).
- Synthesize natural-language answers (LLM Gateway does).
- Fetch documents (Crawl does).

## Inputs / Outputs

```python
class RawCitation:                       # produced by synthesis LLM
    url: str
    content_hash: str                    # document the model claims to cite
    quote: str                           # the exact text the model claims appears at that URL
    claim_id: str                        # opaque ID tying citation back to a sentence in answer_text

class Citation:                          # validated, ready to return
    url: str
    content_hash: str
    byte_start: int
    byte_end: int
    quote: str                           # normalized
    claim_id: str

def validate_citations(
    answer_text: str,
    raw: list[RawCitation],
    documents: dict[str, Document],      # keyed by content_hash
) -> tuple[list[Citation], list[CitationRejection]]: ...

class CitationRejection:
    raw: RawCitation
    reason: Literal[
        "unknown_document",
        "quote_not_found",
        "quote_too_short",
        "quote_too_long",
        "claim_unmatched",
        "envelope_violation",
    ]
```

## Validation algorithm

For each `RawCitation`:

1. Lookup `documents[raw.content_hash]`. If absent → reject `unknown_document`.
2. Reject if `len(raw.quote) < 12 chars` (`quote_too_short`) or `> 800 chars` (`quote_too_long`).
3. Normalize both `raw.quote` and `document.markdown` for matching:
   - NFC unicode normalization.
   - Collapse whitespace runs (incl. newlines) to single space.
   - Lower-case for matching only; preserve original case in stored `quote`.
4. Locate normalized quote substring in normalized document. If not found → reject `quote_not_found`.
5. Map the matched normalized span back to byte offsets in the **original** `document.markdown`.
6. Build `Citation` with `byte_start`, `byte_end`, original-case `quote`.
7. Verify `claim_id` exists in `answer_text` (e.g., footnote markers `[^claim_id]` or inline `<cite id="…">`). If not → reject `claim_unmatched`.

## Envelope-violation check

- Synthesis LLM operates on documents wrapped in `<untrusted_source url sha256>…</untrusted_source>` envelopes (Spec 10).
- If a `RawCitation.quote` matches text *outside* any envelope (e.g., system-prompt boilerplate), reject `envelope_violation`. Indicates either prompt-injection success or the model fabricating against its own prompt.

## Invariants

- Every returned `Citation` satisfies:
  `document.markdown.encode("utf-8")[byte_start:byte_end].decode("utf-8")` normalizes to the same string as `quote` normalizes to.
- `byte_start < byte_end ≤ len(document.markdown.encode("utf-8"))`.
- A citation's `content_hash` matches exactly one document in the input set.
- Rejected citations are never silently dropped — they are returned in `CitationRejection[]` for the orchestrator to log and the response envelope to record `rejected_count`.

## Failure modes

| Failure | Behavior |
|---|---|
| `documents` empty | Return `([], [reject all as unknown_document])`. |
| `raw` empty but `answer_text` non-empty | Orchestrator policy decides whether to return ungrounded answer or refuse; engine itself does not enforce. |
| All citations rejected | Engine returns `([], [...])`; orchestrator MUST mark the response `ungrounded=true` and either refuse or label clearly. |
| Validation timeout | Per-citation work is O(len(doc)); ceiling enforced at 200ms per citation; over → reject `quote_not_found` to fail-safe. |

## Security requirements

- Quote matching never executes regex from `raw.quote` (use literal substring search only — guard against ReDoS).
- `quote` length cap (800) bounds memory.
- Normalization step must not introduce new characters (no transliteration, no case-folding that changes string length asymmetrically without offset rebuild).
- Envelope-violation rejection is critical: it is the only signal that synthesis tried to "cite" instructions, which would indicate prompt injection or hallucination.

## Telemetry contract

Span `citations.validate`
- Attributes: `raw_count`, `valid_count`, `rejected_count`, `reject_reasons` (json map), `total_validation_ms`.

Metrics
- `citations_valid_total` counter.
- `citations_rejected_total{reason}` counter.
- `citations_validation_ms` histogram.
- `citations_envelope_violations_total` counter (security-sensitive — alert if > 0).

## Out of scope / deferred

- Multi-document attribution (citing a single claim to multiple docs).
- Fuzzy matching beyond whitespace/case normalization.
- Cross-language citation (translating quote ↔ document).
- Provenance metadata beyond URL + hash (e.g., DOI, ArXiv ID extraction).

## Open questions

- Should `claim_unmatched` be a soft reject (warn + accept) or hard reject? Default hard; may relax after measurement.
- Whether to support multi-quote-per-citation (e.g., a citation referencing two non-contiguous spans in the same document). Default no — split into two citations.
