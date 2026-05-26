# Spec 09 — Cache

## Purpose
Persist intermediate results (search, rerank, crawl, robots) on local disk to speed up repeated queries and bound external-API cost.

## Bounded context

**Does**
- Provide a key-value cache for search responses, rerank outputs, crawled documents, and robots.txt evaluations.
- TTLs per cache namespace.
- Bound cache size with LRU eviction.
- Store on a single SQLite database via diskcache.

**Does NOT**
- Cache LLM outputs (deferred — answer caching introduces staleness risks for citation grounding).
- Provide a content-addressable store for non-text artifacts.
- Replicate or back up. Cache is ephemeral by design.

## Namespaces

| Namespace | Key | Value | TTL | Notes |
|---|---|---|---|---|
| `search.brave` | `sha256(query \| freshness \| lang \| country \| max_results)` | `SearchResponse` JSON | 6h | Query fully hashed; never store plaintext query as key. |
| `rerank.bge` | `sha256(query \| sorted_canonical_urls)` | `list[RankedResult]` JSON | 24h | Score is deterministic; longer TTL OK. |
| `crawl.document` | `sha256(canonical_url) \| render_js \| max_bytes` | `Document` JSON | 24h (default), per-host configurable | Markdown stored UTF-8. |
| `crawl.robots` | `host` | `RobotsResult` | 24h | Required by Spec 03. |
| `cost.daily` | `YYYY-MM-DD \| role` | `cents_int` | 48h | Monotonic counter, never decremented except by daily-reset. |

Different cache namespaces are physically separated diskcache `Cache` instances or distinct table-namespaces depending on implementation choice — see plan doc.

## Layout

- Single SQLite file at `/var/lib/nexus/cache/cache.db` (mount point declared in Spec 12).
- Volume is a Docker named volume; data is **ephemeral**: on container recreation it MAY be wiped without warning.
- Max cache size: 2 GB. LRU eviction when over.
- Each entry stored with a `version` field; cache reads validate version and treat mismatch as a miss.

## Invariants

- Cache key is always a hex digest (or hex+`|`+small int), never raw query / URL / token.
- Cache value never contains API keys, bearer tokens, or PII beyond what was in the original payload (search snippet, crawled markdown).
- Reads validate `value.version == CURRENT_SCHEMA_VERSION`; mismatch → treat as miss.
- Writes are best-effort and never block the request path beyond 100ms; on write timeout the request proceeds without caching.
- A failed cache backend (disk full, corruption) MUST NOT fail user requests. Fall through to source-of-truth on every read; log warning.

## Failure modes

| Failure | Behavior |
|---|---|
| Disk full | All writes silently skipped (logged at WARN with rate-limit on log volume); reads continue working. |
| SQLite corruption | Read returns miss; module attempts to recreate the cache on startup once; if recreation fails, runs in cache-disabled mode. |
| Schema-version mismatch | Treat as miss; do not auto-migrate (no migrations in v1). |
| Concurrent write race | diskcache handles via file locks; ties broken by last-writer-wins. |

## Security requirements

- Cache file is mode 0600, owned by the service uid.
- The cache directory is NOT exposed via any HTTP endpoint, MCP resource, or LLM tool.
- Markdown stored in `crawl.document` MUST already be envelope-safe (escaped against closing-tag injection — see Spec 10), so a cache rehydration cannot bypass envelope hygiene.
- TTLs are upper bounds; the cache MUST NOT serve entries past TTL even if eviction has not yet run.

## Telemetry contract

Metrics
- `cache_hit_total{namespace}` counter.
- `cache_miss_total{namespace}` counter.
- `cache_size_bytes{namespace}` gauge.
- `cache_eviction_total{namespace}` counter.
- `cache_errors_total{namespace,reason}` counter.

Per-call attributes added to the parent component span (e.g., `search.brave`): `cache="hit|miss|disabled"`.

## Out of scope / deferred

- Redis backend (deferred to a future multi-host deployment).
- Answer cache (LLM output caching) — deferred until citation-grounding implications are evaluated.
- Cache warming / prefetch.

## Open questions

- Should `crawl.document` TTL be per-content-type (e.g., longer for Wikipedia, shorter for news)? Default uniform 24h; revisit after measurement.
- Whether to retain `crawl.document` entries longer if they were cited in an `answer`, so the citation byte-offsets remain resolvable. Lean yes (separate "cited" sub-namespace, TTL 7d).
