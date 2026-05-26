# Plan 03 — Crawl Component

> Spec: [`docs/specs/03-crawl.md`](../specs/03-crawl.md) · spec wins on disagreement.

## Module layout

```
nexus/crawl/
├── ssrf.py             # SSRF guard — lands FIRST, never imports any HTTP client
├── envelope.py         # untrusted-source wrap (canonical impl)
├── browser_pool.py     # Playwright pool with hard cap
├── robots.py           # robotparser-backed evaluator + cache
├── rate_limit.py       # per-host token bucket + per-host/day budget
├── extract.py          # HTML → markdown with byte offsets; strip hidden CSS / scripts
├── crawler.py          # Crawl4AI wrapper composing all of the above
└── types.py            # CrawlRequest, Document, exceptions

tests/unit/crawl/
├── test_ssrf_guard.py
├── test_envelope.py
├── test_browser_pool.py
├── test_robots.py
├── test_rate_limit.py
├── test_extract.py
└── test_crawler.py
tests/security/test_ssrf.py
tests/security/test_envelope.py
tests/integration/test_crawl_e2e.py
```

## Public symbols

```python
# nexus/crawl/ssrf.py
class SSRFViolation(Exception): pass

@dataclass
class SafeURL:
    url: str            # original URL
    host: str
    resolved_ip: str    # the IP we will actually connect to
    family: Literal["v4","v6"]

async def safe_resolve(url: str) -> SafeURL: ...
def is_private_ip(ip: str) -> bool: ...
ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# nexus/crawl/envelope.py
def wrap_untrusted(url: str, content_hash: str, body: str) -> str: ...
def escape_body(body: str) -> str: ...   # makes closing tag un-forgeable from body

# nexus/crawl/crawler.py
class Crawler:
    def __init__(self, pool: BrowserPool, robots: RobotsClient, rate: RateLimiter, ...): ...
    async def fetch(self, req: CrawlRequest) -> Document: ...

# nexus/crawl/types.py
class CrawlRequest: ...
class Document: ...
```

## External dependencies

| Package | Why |
|---|---|
| `crawl4ai` | High-level browser + extraction orchestration. |
| `playwright` | Chromium driver (transitive via crawl4ai; pinned explicitly). |
| `httpx` | Non-JS path; also for robots fetch. |
| `protego` | Robots.txt parser (preferred over stdlib `robotparser` for correctness on modern syntax). |
| `aiodns` or stdlib `socket.getaddrinfo` via `asyncio.to_thread` | DNS resolution. |
| `tldextract` | Per-host domain key. |
| `markdownify` or crawl4ai's built-in extractor | HTML → markdown. |

## Build order

1. **`ssrf.py`** — lands first, zero deps on HTTP clients.
   - `is_private_ip()` covers all ranges in Spec 10.
   - `safe_resolve()` parses URL, checks scheme allowlist, rejects IP-literal hosts unless in `PUBLIC_IP_ALLOW` env, resolves hostname via async DNS, asserts NO returned A/AAAA is private. Returns the chosen IP literal.
   - Connect-with-IP pattern: caller uses `SafeURL.resolved_ip` and sets `Host` header to original hostname. SNI uses original hostname.
   - ➜ `tests/security/test_ssrf.py`: full Spec 13 SSRF catalog. Implement fixtures with a local DNS resolver (`aiohttp`-based UDP server bound to loopback) that returns crafted responses.
2. **`envelope.py`** — pure functions.
   - `escape_body()`: replace `</untrusted_source` (case-insensitive) with `<\/untrusted_source` (well-defined unescape rule: only the closing-tag form is escaped). Document the unescape contract.
   - `wrap_untrusted()`: build the envelope string with attribute-escaped url and sha256.
   - ➜ `tests/security/test_envelope.py`: covers nested envelopes, attribute injection (`"` in url), bidi-override chars, zero-width chars.
3. **`rate_limit.py`** — pure asyncio token bucket per registrable domain. Daily budget counter (in SQLite via cache module; lookup keyed by `(domain, date_utc)`).
4. **`robots.py`** — `protego.Protego.parse()` per host, cache results in `crawl.robots` namespace with 24h TTL. Robots fetch goes through SSRF guard. Default-allow on robots fetch failure.
5. **`browser_pool.py`** — single shared `Browser` (Playwright). Semaphore-gated context creation, hard cap from config. Each context closed after use. Soft-memory check: refuse new context if RSS > 70% of `mem_limit`.
6. **`extract.py`** — HTML parsing with `lxml`/`selectolax`. Strip `<script>`, `<style>`, `<noscript>`. Pre-render walk: drop nodes with `display:none`, `visibility:hidden`, `opacity:0`, zero font-size; drop `aria-hidden="true"`. Emit markdown with a parallel offset table mapping output byte ranges to source DOM nodes (for Spec 04 citation byte-binding). HTML comments dropped.
7. **`crawler.py`** — composes: SSRF resolve → robots check → rate limit acquire → (non-JS via httpx OR JS via browser pool) → extract → envelope wrap → return Document.
   - Redirect handling: max 5 hops, each re-runs SSRF; capture `redirect_chain`.
   - Per-host concurrency: serialized within a host via rate limiter.
   - Crawl4AI is used for the JS render path; for non-JS we use httpx directly (faster, simpler).
8. **Wire to orchestrator** via a `Crawler` instance constructed once at startup.

## Configuration loading

```python
class CrawlConfig(BaseSettings):
    crawl_concurrency: int = 4
    crawl_pages_max: int = 8
    crawl_timeout_s: float = 20.0
    crawl_max_bytes: int = 4_000_000
    user_agent: str = "NexusAgenticSearch/0.1 (+contact)"
    public_ip_allow: list[str] = []   # IP literals exempt from SSRF guard; empty by default
    rate_qps_per_host: float = 0.667  # 1 req / 1.5s
    rate_burst_per_host: int = 2
    domain_daily_budget: int = 50
    robots_cache_ttl_s: int = 86400
    js_default: bool = False
```

## Test plan (mapping to spec invariants)

| Spec invariant | Test |
|---|---|
| Every outbound URL passes SSRF guard | `test_crawler::test_ssrf_invoked_on_every_fetch` (mock guard, assert call); `test_ssrf::*` |
| Redirect chain re-validated | `tests/security/test_ssrf::test_ssrf_redirect_to_internal` |
| DNS rebinding defeated | `tests/security/test_ssrf::test_ssrf_dns_rebinding` |
| `status != "ok"` ⇒ `markdown == ""` | `test_crawler::test_failed_documents_empty_markdown` |
| No raw HTML/script in markdown | `test_extract::test_strips_scripts_and_styles` |
| Hidden CSS dropped | `test_extract::test_strips_hidden_css` |
| `content_hash == sha256(markdown)` | `test_extract::test_content_hash_matches` |
| Envelope closing tag un-forgeable | `tests/security/test_envelope::test_closing_tag_escape` |
| Robots disallow → blocked | `test_robots::test_disallow_path` |
| Per-host rate limit holds | `test_rate_limit::test_token_bucket` |
| Per-day domain budget honored | `test_rate_limit::test_daily_budget` |
| Browser pool cap | `test_browser_pool::test_pool_cap` |
| Soft memory ceiling refuses new context | `test_browser_pool::test_memory_ceiling` |
| Max redirects enforced | `test_crawler::test_max_redirects` |

## Adversarial tests required

The full Spec 13 SSRF + envelope catalog lives in `tests/security/`:
- `test_ssrf.py`: cloud metadata, loopback v4/v6, RFC1918, link-local v6, ULA, redirect chain to internal, DNS rebinding (with fixture DNS server), IPv4-mapped-v6, file://, gopher://, javascript:, data:, egress-firewall self-test.
- `test_envelope.py`: closing-tag in body, attribute injection, nesting collision, bidi overrides, zero-width chars, NFC normalization edge cases.

## Risks & mitigations

- **Playwright resource leaks**: each context closed in `finally`; pool tracker asserts in tests that contexts are returned.
- **Slow DNS** stalling event loop: DNS resolution in `asyncio.to_thread`; per-resolve timeout 3s.
- **Crawl4AI API churn**: pin tight; wrap in our `Crawler` so a future migration touches one file.
- **robots-cache poisoning by SSRF**: robots fetch goes through the same SSRF guard. Tests cover.
- **markdownify dropping content**: extractor includes a "did we extract anything?" check; empty extraction with HTTP 200 → `extraction_failed`.

## Done criteria
- [ ] All unit + security tests pass.
- [ ] Egress self-test in `nexus/security/selftest.py` passes inside the deployed container.
- [ ] Integration test `test_crawl_e2e.py` (against a local fixture web server, NOT live web in default `make test`) green.
- [ ] No code path in `nexus/crawl/` calls `httpx.AsyncClient` / Playwright `goto` without first invoking `safe_resolve()` — enforced by an import-time test that grep-scans the package.
- [ ] `mypy --strict` clean.
