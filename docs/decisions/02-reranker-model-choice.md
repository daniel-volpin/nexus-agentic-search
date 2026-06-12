# ADR 02 — Reranker model choice for Spec 02

Date: 2026-05-26
Updated: 2026-06-12
Status: Accepted (lexical scorer as permanent default)

## Decision

The default reranker is the **deterministic lexical scorer** (query/candidate
token overlap). The cross-encoder interface (`rerank_fn` on the orchestrator)
is preserved so a model-based reranker can be swapped in via config if the
deployment target changes.

`BAAI/bge-reranker-v2-m3` was the original model target but is **not wired
as a default** — the resource cost is incompatible with the primary deployment
environment.

## Context

The service runs on a mini PC home server alongside other containers, with
limited CPU and RAM. A cross-encoder reranker (torch + transformers) adds
~1–2 GB to the Docker image and ~1.5 GB resident RAM at runtime — a
disproportionate cost for a rerank stage when the LLM (Gemini free tier,
remote) handles quality in synthesis.

The lexical scorer is deterministic, zero-dependency, explainable, and
sufficient for current traffic. The interface seam means upgrading is a
config change, not a rewrite.

## Revisit criteria

Re-open only if:
- The deployment moves to hardware with spare GPU/RAM headroom.
- Quality metrics on a golden set show the lexical scorer is the bottleneck
  (not the LLM, not the search, not the crawl).
- A lightweight cross-encoder appears that doesn't require torch (~100 MB
  range, ONNX-only).
