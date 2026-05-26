# Spec 02 — Rerank Component

## Purpose
Re-score and re-order search results against the user query using a local cross-encoder; apply dedup and domain diversity before passing to crawl.

## Bounded context

**Does**
- Take `(query, list[Result])` and return `list[RankedResult]` sorted by relevance score, top-K.
- Score `(query, candidate_text)` pairs with `bge-reranker-v2-m3` (or pin-equivalent).
- Apply dedup beyond URL canonicalization (title-similarity + snippet-similarity).
- Apply domain diversity: cap pages-per-domain at `N` in the returned set.
- Run entirely local; no network calls.

**Does NOT**
- Fetch page bodies (rerank input is `title + snippet`; full-document rerank is deferred).
- Call any LLM.
- Persist scores beyond the request (rerank cache is by `(query_hash, url)` in Spec 09).

## Inputs / Outputs

```python
class RankedResult:
    result: Result            # from Spec 01
    score: float              # in [0.0, 1.0], cross-encoder sigmoid
    rerank_rank: int          # 0-based after rerank

def rerank(
    query: str,
    candidates: list[Result],
    top_k: int = 8,
    per_domain_cap: int = 2,
) -> list[RankedResult]: ...
```

## Algorithm

1. Build candidate text per `Result`: `f"{title}\n{snippet}"`.
2. Run cross-encoder over `(query, text)` pairs in a single batch.
3. Apply sigmoid to logits → score in `[0,1]`.
4. Sort descending by score.
5. Walk sorted list, skipping any result whose domain has already contributed `per_domain_cap` items.
6. Walk sorted list, dropping near-duplicates (token-Jaccard ≥ 0.85 over title+snippet).
7. Take top_k.

## Invariants

- Output length ≤ `top_k`.
- No two output items share the same canonical URL.
- No domain appears more than `per_domain_cap` times in the output.
- Scores are deterministic for fixed model weights and inputs (no nondeterministic ops on CPU path).
- Rerank latency is bounded: ≤ 3s on CPU for `len(candidates) ≤ 30`. Over budget → log warning and return un-reranked top_k by provider rank.

## Failure modes

| Failure | Required behavior |
|---|---|
| Model fails to load at startup | Fail-closed; service does not accept requests. |
| Model OOM mid-request | Log error, return provider-rank top_k as fallback, mark response `degraded=true`. |
| `candidates` empty | Return empty list; do NOT raise. |
| `candidates` len > 30 | Truncate to 30 before scoring; log warning. |
| Score computation timeout | Cancel scoring, fall back to provider rank. |

## Security requirements

- Model weights pinned by sha256 at load time. Mismatch → fail-closed.
- No network access from this module (enforced by container egress policy; see Spec 12).
- Inputs treated as untrusted text: NFC-normalized, control chars stripped, length-capped per field (title ≤ 256, snippet ≤ 1024).
- Model must run on CPU by default; GPU only when explicitly enabled, never auto-detected (resource fairness on shared home server).

## Telemetry contract

Span `rerank.bge`
- Attributes: `query_hash`, `candidate_count`, `top_k`, `per_domain_cap`, `kept_count`, `score_p50`, `score_p95`, `latency_ms`, `degraded` (bool).

Metrics
- `rerank_latency_ms` histogram.
- `rerank_input_count` histogram.
- `rerank_kept_count` histogram.
- `rerank_score_distribution` summary.

## Out of scope / deferred

- Full-document rerank after crawl (could re-score using extracted markdown — deferred until quality measurement justifies the latency cost).
- Hosted rerank (Cohere, Voyage) — only if local quality is insufficient.
- Embedding-based candidate retrieval.

## Open questions

- `bge-reranker-base` vs `bge-reranker-v2-m3` — choose at implementation time based on memory footprint vs quality benchmark on the golden set.
- `per_domain_cap` default: 2 chosen on intuition; revisit after diversity-vs-quality measurement.
