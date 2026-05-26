# Plan 01 — Search Component

> Spec: [`docs/specs/01-search.md`](../specs/01-search.md) · spec wins on disagreement.

## Module layout

```
nexus/search/
├── client.py           # SearchClient Protocol; provider routing; fallback policy
├── brave.py            # BraveProvider implementation
├── searxng.py          # SearXNGProvider + per-engine CircuitBreaker
├── canonical.py        # canonicalize(url) -> str
└── types.py            # SearchRequest, Result, SearchResponse, exceptions

tests/unit/search/
├── test_canonical.py
├── test_brave.py
├── test_searxng_provider.py
├── test_circuit_breaker.py
└── test_client_routing.py
tests/integration/test_search_fallback.py
```

## Public symbols

```python
# nexus/search/types.py
class SearchRequest: ...
class Result: ...
class SearchResponse: ...
class SearchUnavailable(Exception): ...
class ValidationError(ValueError): ...

# nexus/search/canonical.py
def canonicalize(url: str) -> str: ...

# nexus/search/client.py
class SearchClient(Protocol):
    async def search(self, req: SearchRequest) -> SearchResponse: ...

class DefaultSearchClient:
    """Routes to Brave; falls back to SearXNG per Spec 01 activation policy."""
    def __init__(self, brave: BraveProvider, searxng: SearXNGProvider, config: SearchConfig): ...
    async def search(self, req: SearchRequest) -> SearchResponse: ...

# nexus/search/brave.py
class BraveProvider:
    async def search(self, req: SearchRequest) -> SearchResponse: ...

# nexus/search/searxng.py
class SearXNGProvider:
    async def search(self, req: SearchRequest, engines: list[str]) -> SearchResponse: ...

class CircuitBreaker:
    def is_open(self, engine: str) -> bool: ...
    def record_success(self, engine: str) -> None: ...
    def record_trip(self, engine: str, reason: str) -> None: ...
```

## External dependencies

| Package | Why |
|---|---|
| `httpx` | Async HTTP client; pin TLS-verify on. |
| `pydantic` v2 | `SearchRequest`/`Result` validation, schema enforcement. |
| `tldextract` | Canonical eTLD+1 for dedup + per-domain caps. |
| `orjson` | Faster + safer JSON parsing of provider responses. |
| `tenacity` | Retry policy with jitter for 429/5xx. |

## Build order

1. **Types and exceptions** (`types.py`). Pydantic models for `SearchRequest`, `Result`, `SearchResponse`. Field validators enforce length caps and string sanitization. ➜ `tests/unit/search/test_types.py` covers boundary inputs.
2. **URL canonicalization** (`canonical.py`). Pure function. Strip tracking params allowlist (per spec), lower-case scheme/host, normalize path, drop fragment, drop default ports, sort remaining query params. ➜ `test_canonical.py` covers spec rules with table-driven cases incl. unicode IDN, ipv6 literal, query reordering, percent-decoding.
3. **BraveProvider** (`brave.py`). HTTPX `AsyncClient` with `BRAVE_API_KEY` from config, `https://api.search.brave.com/res/v1/web/search` endpoint, retry chain (200ms / 800ms / 3.2s, max 3 on 429; 1 retry on 5xx after 500ms), 10s overall timeout. Parse with `orjson`. Strip tracking params via canonicalizer. Dedup by canonical URL. ➜ `test_brave.py` mocks HTTPX transport; covers 200/429/5xx/timeout/malformed paths.
4. **CircuitBreaker** (`searxng.py`). In-memory dict keyed by engine. `record_trip` increments today's counter, computes cool-down as `min(2^n * 30 * 60, 64 * 3600)`, sets `disabled_until`. `is_open()` returns true while `now() < disabled_until`. Reset on UTC midnight via counter date check. ➜ `test_circuit_breaker.py` uses freezegun-style time control.
5. **SearXNGProvider** (`searxng.py`). HTTPX client to `SEARXNG_BASE_URL`, `/search?format=json&engines={list}&q={query}`. Engine list filtered by `not breaker.is_open(e)`. Per-engine response inspection: detect `sorry/index`, `recaptcha`, `g-recaptcha`, `unusual traffic`, HTTP 429 → `breaker.record_trip`. Per-engine QPS limiter (token bucket, asyncio.Lock + monotonic clock) inside the provider. ➜ `test_searxng_provider.py` covers happy path, captcha detection, per-engine QPS enforcement, both-engines-tripped.
6. **DefaultSearchClient** (`client.py`). Implements Spec 01 activation policy:
   - Call Brave.
   - If `SearchUnavailable` OR `len(results) < 3` → call SearXNG with active engines.
   - Merge + dedup; preserve Brave order, append SearXNG results not already seen.
   - Set `provider` field to `"brave"` or `"brave+searxng"` accordingly.
   - Bump `search_provider_used_total`.
   ➜ `test_client_routing.py` covers each branch; `test_search_fallback.py` is an integration test with both providers mocked.
7. **Wire into `nexus/main.py`**. `SearchClient` is constructed once at startup and injected into the orchestrator.

## Configuration loading

`nexus/config.py` exposes:

```python
class SearchConfig(BaseSettings):
    brave_api_key: SecretStr
    brave_endpoint: AnyHttpUrl = "https://api.search.brave.com/res/v1/web/search"
    brave_timeout_s: float = 10.0

    searxng_base_url: AnyHttpUrl = "http://searxng:8080"
    searxng_timeout_s: float = 6.0
    searxng_engines: list[str] = ["google", "duckduckgo"]
    searxng_qps_google: float = 0.2
    searxng_qps_duckduckgo: float = 0.5
    searxng_captcha_breaker: bool = True
```

Validation: engines list must be a subset of `{"google","duckduckgo"}` — startup fails otherwise. Defense against config drift introducing Bing/Yahoo.

## Test plan (mapping to spec invariants)

| Spec invariant | Test |
|---|---|
| Canonical-URL dedup | `test_canonical::test_dedup`, `test_brave::test_dedup_in_response` |
| `snippet` is never a citation source | Cross-cutting; tested at the citation engine (Plan 04). |
| API key never in returned field/log/trace | `test_brave::test_no_key_in_telemetry`, `test_redaction` from Plan 10. |
| `query` logged hashed only | `test_brave::test_query_logged_as_hash` |
| `max_results` clamped server-side | `test_brave::test_clamps_max_results` |
| HTTPS + TLS verify on | `test_brave::test_https_only`, `test_searxng::test_https_only` (SearXNG over plain HTTP on Docker bridge is OK; this test ensures the *outbound to upstream engines* is HTTPS). |
| SearXNG engine allowlist enforced at startup | `test_config::test_rejects_disallowed_engines` |
| CAPTCHA → trip | `test_circuit_breaker::test_captcha_redirect_trips` |
| Tripped engine excluded | `test_searxng::test_skips_tripped_engine` |
| Both tripped → Brave-only fallback path | `test_client_routing::test_both_tripped` |

## Adversarial tests required (Spec 13)

- `tests/security/test_search_url_canonicalization.py`: fuzz canonicalizer with hostile URLs (data:, javascript:, file:, idn-spoof, mixed-case scheme).
- `tests/security/test_searxng_egress.py`: assert SearXNG provider rejects (or fails closed) if the engine list contains anything outside the allowlist.

## Risks & mitigations

- **Brave schema drift** → defensive parsing; missing fields → `None`; log `model_drift` warning; passing tests assert robustness against minor key renames.
- **SearXNG response inconsistency across versions** → pin SearXNG image digest (Spec 12); compatibility re-tested at each update window.
- **Captcha-detection false positives** (a legitimate page mentioning "recaptcha") → trip on engine-banner matches only, not body content. Implement: only inspect the `engine_error` field in SearXNG JSON, or the HTTP redirect Location header, not the result snippets.
- **QPS limiter drift under restart** → token bucket is in-memory; resets on restart, which is acceptable (worst case: brief burst before steady state). Document explicitly; do not persist bucket state.

## Done criteria
- [ ] All unit tests pass (`pytest tests/unit/search`).
- [ ] Integration fallback test passes (`pytest tests/integration/test_search_fallback.py`).
- [ ] Adversarial tests pass.
- [ ] Telemetry: `search.brave` span emitted on every call with required attributes (Spec 01); `searxng.fetch` span emitted on fallback calls.
- [ ] `make lint` clean; `mypy --strict` clean for `nexus/search/`.
- [ ] No `query` plaintext appears anywhere in test log output (asserted by `test_redaction`).
