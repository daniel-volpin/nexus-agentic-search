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
    engine: Literal["brave"]            # extensible
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

## Out of scope / deferred

- SearXNG fallback (deferred; if added, API-engines-only, never HTML scrapers).
- Tavily integration (deferred; second provider via the same `SearchClient` interface).
- Semantic / embedding-based search.

## Open questions

- Brave freshness parameter exact mapping (`day` → `pd`, `week` → `pw`, etc.): confirm at implementation time against current Brave API docs.
- Whether `safesearch` is exposed to caller or hard-coded to `moderate`.
