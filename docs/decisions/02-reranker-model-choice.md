# ADR 02 — Reranker model choice for Spec 02

Date: 2026-05-26
Status: Accepted

## Decision

For the Spec 02 rerank stage, we keep the **architecture choice** as a local **cross-encoder reranker** and set the default model target to:

- `BAAI/bge-reranker-v2-m3`

We do **not** use hosted reranking services by default, and we do **not** use a bi-encoder as the final rerank stage.

## Why this is the right choice for our use case

Our use case is Perplexity-style open-web QA search where first-stage search is noisy and the final context window is small. We need stronger precision at top-K than first-pass search can provide.

Cross-encoders are the right mechanism because they score query+document jointly and are routinely used as final-stage rerankers in modern retrieval stacks.

## Option comparison

### 1) `BAAI/bge-reranker-v2-m3` (chosen)
- Pros:
  - Open weights, Apache-2.0 license.
  - Multilingual and broadly adopted for local reranking.
  - Good quality/latency balance for CPU-first and optional GPU deployments.
- Cons:
  - Heavier than xsmall rerankers.

### 2) `BAAI/bge-reranker-base`
- Pros:
  - Mature and widely used baseline.
- Cons:
  - Older generation than v2; weaker multilingual profile and generally less favorable quality/efficiency tradeoff for new builds.

### 3) `mixedbread-ai/mxbai-rerank-xsmall-v1`
- Pros:
  - Very fast and lightweight.
- Cons:
  - Lower headroom on hard/noisy web queries compared with stronger cross-encoders. Better as a latency-first fallback, not our default quality target.

### 4) Hosted rerank APIs (e.g., Cohere/Jina, etc.)
- Pros:
  - Operationally simple, often strong quality.
- Cons:
  - External dependency, variable cost, privacy/egress concerns, and not aligned with our local-first constraints in Spec 02.

## Guardrails

- Keep rerank candidate set bounded (<=30) and top-k small.
- Keep CPU as default execution target.
- Add a deterministic fallback to provider rank if reranker fails or times out.
- Keep model selection configurable so we can benchmark alternates on a fixed golden set before changing defaults.

## Revisit criteria

Re-open this decision only if one of the following is true:
- p95 rerank latency exceeds budget on target hardware.
- quality metrics on the golden set regress below agreed threshold.
- a new open model demonstrates materially better quality-latency on our benchmark.
