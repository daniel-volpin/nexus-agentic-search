# Spec 01 — Search Component

## Purpose
Issue a contractual web search and return a normalized, deduped result set.

## Bounded context

**Does**
- Translate `SearchRequest` to provider-specific calls (Brave Search API).
- Apply freshness, language, country filters where the provider supports them.
- Normalize results into a `Result` schema independent of provider.
- Dedup candidates by canonical URL before returning.

**Does NOT**
- Crawl pages.
- Rerank or score relevance beyond passing through provider rank.
- Scrape Google/Bing/Yahoo HTML SERPs.
- Issue queries from a non-allowlisted egress (provider HTTPS endpoints only).

## Inputs / Outputs

```python
class SearchRequest:
    query: str                          # 1..512 chars, validated
    freshness: Literal["any","day","week","month","year"] = "any"
    max_results: int = 20               # 1..50
    lang: str | None = None             # ISO 639-1
    country: str | None = None          # ISO 3166-1 alpha-2

class Result:
    url: str                            # canonicalized
    title: str
    snippet: str                        # provider-supplied; NEVER a citation source
    engine: Literal["brave","searxng:google","searxng:duckduckgo"]
    rank: int                           # 0-based, provider order
    published_at: datetime | None       # if provider returned it
    fetched_at: datetime                # when this search call ran

class SearchResponse:
    results: list[Result]
    provider: str
    query_sent: str                     # exact string sent to provider
    latency_ms: int
```

## URL canonicalization rules

- Scheme/host lower-cased.
- Default ports stripped (`:80` for http, `:443` for https).
- Path: percent-decode unreserved chars, collapse `//`, remove trailing `/` except for root.
- Fragment removed.
- Tracking query params stripped: `utm_*`, `gclid`, `fbclid`, `mc_*`, `ref`, `ref_src`, `_hsenc`, `_hsmi`, `igshid`, `vero_id`, `mkt_tok`, `yclid`.
- Remaining query params sorted lexicographically.

## Invariants

- Results are deduped by canonical URL before return; no two `Result` share `canonicalize(url)`.
- `snippet` is never used as a citation source downstream — only as a rerank / expansion hint.
- Provider API key is never present in any returned field, log line, exception message, or telemetry attribute.
- `query` is logged hashed (`sha256(query)[:8]`), never plaintext.
- `max_results` cap enforced before request; client-supplied value clamped, never trusted.
- All outbound requests use HTTPS with TLS verification.

## Failure modes

| Failure | Required behavior |
|---|---|
| Provider 429 | Exponential backoff with jitter (200ms, 800ms, 3.2s), max 3 retries; on final failure raise `SearchUnavailable`. |
| Provider 5xx | One retry after 500ms; on second failure raise `SearchUnavailable`. |
| Provider timeout (>10s) | Cancel and raise `SearchUnavailable`. |
| Zero results | Return empty `results` list; do NOT raise. |
| Invalid `query` (empty, >512 chars, all whitespace) | Raise `ValidationError` before any network call. |
| Provider schema drift | Log structured warning, return successfully parsed subset; missing fields → `None`. |
| `BRAVE_API_KEY` missing or empty | Fail-closed at startup; service does not accept requests. |

## Security requirements

- API key loaded from `BRAVE_API_KEY` env var. Never in code, image, or logs.
- HTTPS only. TLS verification enforced; no `verify=False`.
- Outbound restricted to `api.search.brave.com` at the egress firewall (see Spec 12).
- Query treated as untrusted input: length-checked, control characters (`\x00`–`\x1f` except `\t`) stripped.
- No URL fragments, `javascript:`, `data:`, `file:`, or non-http(s) schemes survive canonicalization.

## Telemetry contract

Span `search.brave`
- Attributes: `query_hash`, `freshness`, `max_results`, `lang`, `country`, `result_count`, `latency_ms`, `retry_count`.
- Records exceptions with type only (no API key, no query plaintext).

Metrics
- `search_latency_ms{provider="brave"}` histogram.
- `search_result_count{provider="brave"}` histogram.
- `search_errors_total{provider="brave",reason}` counter.

Log lines structured JSON only.

## SearXNG fallback provider (engine-locked: Google + DuckDuckGo only)

A self-hosted SearXNG sidecar container (Spec 12) provides a second-tier provider invoked when Brave is unavailable or returns thin coverage. Engines other than `google` and `duckduckgo` are DISABLED in `searxng/settings.yml` and MUST stay disabled.

### Activation policy

The `SearchClient.search()` default returns Brave results. SearXNG is consulted exactly when:

1. Brave returns `SearchUnavailable` after its own retry chain, OR
2. Brave returns `len(results) < 3` for a non-empty query (thin coverage), OR
3. Caller passes `provider="searxng"` explicitly (debug / power-user path; orchestrator does NOT expose this on either transport).

SearXNG output flows through the same `Result` normalization, canonicalization, and dedup. The `engine` field records `"searxng:google"` or `"searxng:duckduckgo"` per row so downstream observability can distinguish sources.

### Abuse / ban-risk controls (mandatory)

Scraping Google from a residential IP can trigger an IP-level CAPTCHA on the entire home network for hours-to-days. The following controls bound but do not eliminate that risk.

- Per-engine sustained QPS cap (token bucket, applied client-side BEFORE the SearXNG call):
  - `google`: 0.2 QPS sustained, burst 1.
  - `duckduckgo`: 0.5 QPS sustained, burst 2.
- Global SearXNG QPS cap: ≤ 0.5.
- CAPTCHA / abuse-page detection: a SearXNG response containing any of `sorry/index`, `recaptcha`, `g-recaptcha`, `unusual traffic`, or HTTP 429 from a named engine trips the per-engine circuit breaker.
- Circuit breaker cool-down: `min(2^n × 30 min, 64 h)` where `n` = consecutive trips on the current UTC day for that engine. Auto-reset at UTC midnight.
- A tripped engine is removed from the engine list passed to SearXNG for subsequent calls; if both engines are tripped, `SearchClient` falls back to Brave-only.
- All trips emit a structured WARN log and increment `searxng_engine_tripped_total{engine}` (Spec 11). Three consecutive trips in 24h for the same engine raise the `SearXNGEngineFlapping` alert.

### Configuration

```toml
[searxng]
base_url        = "http://searxng:8080"        # adjacent container
timeout_s       = 6.0
engines         = ["google", "duckduckgo"]
qps_per_engine  = { google = 0.2, duckduckgo = 0.5 }
captcha_circuit_breaker = true
```

Values are config, not request-time arguments.

### Additive invariants

- Outbound from the SearXNG container is restricted at the egress firewall (Spec 12) to `www.google.com` and `html.duckduckgo.com` (plus DNS).
- SearXNG never receives the user's bearer token. It sees only the search query.
- The SearXNG container runs read-only with dropped capabilities, no admin endpoint exposed, no public bind.
- SearXNG snippets, like Brave's, are NOT citation sources. Citation grounding always uses crawled documents.

### Additive failure modes

| Failure | Behavior |
|---|---|
| One engine tripped | Continue with the other; mark `Result.engine` accordingly. |
| Both engines tripped | `SearchClient` returns Brave-only results; if Brave also unavailable, raise `SearchUnavailable`. |
| SearXNG container unreachable | Same as both engines tripped; alert fires (Spec 11). |
| SearXNG returns malformed JSON | Log structured warning, drop the offending rows, return what parsed. |
| Engine returns zero results | Try the other engine in the same call; combine and dedup. |

## Out of scope / deferred

- Tavily integration (deferred; second contractual provider via the same `SearchClient` interface).
- Semantic / embedding-based search.
- SearXNG engines beyond `google` and `duckduckgo` (deliberately excluded).

## Open questions

- Brave freshness parameter exact mapping (`day` → `pd`, `week` → `pw`, etc.): confirm at implementation time against current Brave API docs.
- Whether `safesearch` is exposed to caller or hard-coded to `moderate`.
