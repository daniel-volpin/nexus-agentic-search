# Spec 00 — System Overview

## Purpose
Agentic web-search backend producing citation-grounded answers from live web sources, callable by an adjacent chat-agent container over MCP and plain HTTP.

## Bounded context

**This system does**
- Accept a natural-language query.
- Issue searches against contractual search APIs (Brave Search).
- Rerank candidates with a local cross-encoder.
- Fetch and extract content from a bounded set of pages.
- Bind every claim in the final answer to a verified text span in a fetched page.
- Stream partial results back to the caller.
- Enforce per-query token, time, and dollar budgets.

**This system does NOT**
- Operate a chat agent (adjacent container's responsibility).
- Maintain user accounts, sessions, or multi-tenant isolation (single-user home deployment).
- Train or fine-tune models.
- Persist crawled content beyond an ephemeral cache.
- Scrape SERPs against provider ToS (no Google/Bing HTML scraping).
- Run with privileged network access; egress is firewalled.

## Module graph

```
                ┌─────────────┐
                │  Transport  │  MCP (FastMCP v2)
                │   layer     │  HTTP (FastAPI)
                └──────┬──────┘
                       │ SearchRequest
                       ▼
                ┌─────────────────┐
                │  Orchestrator   │  stages, streaming, cancellation
                └──┬───┬───┬───┬──┘
                   │   │   │   │
        ┌──────────┘   │   │   └───────────┐
        ▼              ▼   ▼               ▼
   ┌────────┐    ┌────────┐ ┌────────┐ ┌────────────┐
   │ Search │    │ Rerank │ │ Crawl  │ │    LLM     │
   │ (Brave)│    │  (bge) │ │(Crawl4 │ │  Gateway   │
   └────────┘    └────────┘ │  AI)   │ │ (LiteLLM)  │
                            └────────┘ └────────────┘
                                 │           │
                                 ▼           ▼
                            ┌─────────────────────┐
                            │  Citations engine   │
                            │  (span binding +    │
                            │   quote validation) │
                            └─────────────────────┘

  Cross-cutting:  Cache  ·  Security  ·  Observability  ·  Cost meter
```

## Request lifecycle (happy path)

1. Caller sends `SearchRequest` via MCP tool or HTTP POST.
2. Orchestrator emits `stage=accepted`.
3. (Optional, role-gated) LLM expands query into 1–3 sub-queries.
4. Search component returns N candidates from Brave.
5. Rerank narrows to top-K, deduped and diversified.
6. Orchestrator emits `stage=ranked`.
7. Crawler fetches top-M pages in parallel under a semaphore.
8. Each completed page emits `stage=page_ready` with its `Document`.
9. LLM synthesis receives query + ranked documents (wrapped in untrusted-source envelopes) and returns answer + raw citation candidates.
10. Citations engine validates every citation against fetched documents (quote substring must match byte range).
11. Orchestrator emits final `stage=answer` carrying validated citations only.

## Glossary

| Term | Meaning |
|---|---|
| Document | Result of a single crawl: `(url, content_hash, markdown, fetched_at, status, content_type)`. |
| Citation | `(url, content_hash, byte_start, byte_end, quote)` produced by synthesis and validated against the matching `Document`. |
| Untrusted-source envelope | `<untrusted_source url sha256>…</untrusted_source>` wrapper around any crawled text passed to an LLM. |
| Role | A named LLM use case (`synthesis`, `rerank-decision`, `query-expansion`) mapped to a pinned model ID. |
| SearchRequest | Caller input: `(query, freshness, max_results, lang?, country?)`. |
| AnswerEvent | A single stream event emitted by the orchestrator: `(stage, payload)`. |
| Canonical URL | Lower-cased scheme/host, normalized path, query stripped of tracking params, no fragment. |

## System-wide quality gates

- **Citation validity** — every returned citation's quote exists verbatim at the recorded byte range in the document with the matching content hash. Invalid citations are dropped, never returned.
- **Reproducibility** — given identical inputs and a warm cache, the same answer is returned within bounded variance (model temperature applies).
- **Budget** — per-query token cap, per-query 60s wall-clock cap, per-day dollar cap. Over cap → degrade (cheaper model) or refuse with structured error.
- **Containment** — every fetched URL passes the SSRF guard; every LLM call uses key-redacted logging; every crawl result is enveloped before LLM ingestion.

## Versioning

- Each spec is a stable contract for a major version. Breaking changes require a new spec revision (filename suffixed `-vN.md`, predecessor archived).
- Implementation may exceed a spec but must not deviate. Spec wins on disagreement.

## Cross-references

Component specs: 01–09. Cross-cutting: 10 (security), 11 (observability), 12 (deployment), 13 (testing).
