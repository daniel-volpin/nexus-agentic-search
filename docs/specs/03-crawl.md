# Spec 03 — Crawl Component

## Purpose
Fetch web pages safely, extract clean markdown, and return `Document` records suitable for LLM ingestion. The crawler is the system's primary egress and primary attack surface.

## Bounded context

**Does**
- Single-URL fetch (no link-following for v1).
- Pre-fetch SSRF guard (mandatory, defense-in-depth with container egress).
- Browser-pool management for JS-rendered pages.
- Robots.txt evaluation; per-domain rate limiting; per-domain crawl budget.
- HTML → markdown extraction with byte-offset preservation.
- Content hashing and untrusted-source envelope wrapping.

**Does NOT**
- Follow links (no deep crawl).
- Render PDF, audio, video, or non-text content (returns `unsupported_content_type`).
- Persist crawled content beyond the cache layer (Spec 09).
- Maintain login sessions, cookies-jar, or paywall bypass.

## Inputs / Outputs

```python
class CrawlRequest:
    url: str                          # caller passes already-canonical URL
    render_js: bool = False           # opt-in per request
    timeout_s: float = 20.0
    max_bytes: int = 4_000_000        # 4 MB cap on body
    respect_robots: bool = True

class Document:
    url: str                          # final URL after allowed redirects
    requested_url: str
    content_hash: str                 # sha256(markdown), hex
    markdown: str
    content_type: str
    fetched_at: datetime
    status: Literal[
        "ok",
        "blocked_by_ssrf_guard",
        "blocked_by_robots",
        "rate_limited",
        "timeout",
        "http_4xx",
        "http_5xx",
        "unsupported_content_type",
        "too_large",
        "extraction_failed",
    ]
    http_status: int | None
    bytes_in: int
    render_ms: int
    extraction_ms: int
    redirect_chain: list[str]         # each entry passed SSRF guard
```

## SSRF guard (P0 — mandatory)

Pre-fetch, applied to the seed URL AND every redirect target:

1. Scheme allowlist: `{http, https}`.
2. Reject IP-literal hosts unless the literal is in an explicit `PUBLIC_IP_ALLOW` list (empty by default).
3. Resolve hostname; reject if ANY resolved A/AAAA falls in:
   - IPv4: `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `100.64.0.0/10`, `0.0.0.0/8`, `224.0.0.0/4`, `240.0.0.0/4`.
   - IPv6: `::1/128`, `fc00::/7`, `fe80::/10`, `::ffff:0:0/96` (IPv4-mapped), multicast `ff00::/8`.
4. Re-resolve immediately before connect; connect to the resolved IP literal with `Host:` header set (defeats DNS rebinding).
5. Redirect chain capped at 5 hops; each hop re-runs steps 1–4.
6. Container egress firewall enforces the same rules at the network layer (Spec 12) — app-layer guard is one of two defenses, never the only one.

## Robots.txt handling

- Fetch `https://<host>/robots.txt` (with the same SSRF guard); cache result for 24h per host.
- If `User-agent: *` or our specific UA is disallowed for the target path, return `status=blocked_by_robots`.
- If robots fetch fails: default-allow with a logged warning (do not fail-closed on missing robots — common case for many sites).
- Crawl-delay directive respected as a per-host minimum interval.

## Per-domain rate limiting

- Per-host token bucket: default 1 request / 1.5 seconds, burst 2.
- Bucket key = registrable domain (eTLD+1).
- Exceeded → `status=rate_limited`; caller may retry later (orchestrator decides).
- Crawl budget per (domain, day): default 50 successful fetches; over budget → `rate_limited`.

## Browser pool

- Hard cap: 4 concurrent browser contexts process-wide (configurable).
- Contexts created from a single Playwright `Browser` shared across requests.
- Each context is single-use, then closed.
- `render_js=False` path bypasses Playwright entirely (httpx + readability).
- Memory ceiling: container `--memory` enforces a hard ceiling; crawl module respects a soft ceiling 70% of that and refuses new contexts above it.

## Content extraction

- HTML → markdown via Crawl4AI's extractor, with byte-offset preserved per output paragraph.
- Strip: `<script>`, `<style>`, `<noscript>`, hidden-CSS text (`display:none`, `visibility:hidden`, `opacity:0`, zero-size font), HTML comments, ARIA labels marked `aria-hidden`.
- Preserve: visible headings, paragraphs, list items, table cells (rendered as markdown), code blocks.
- Output wrapped in `<untrusted_source url="…" sha256="…">…</untrusted_source>` envelope before being passed to any LLM call (Spec 05 enforces; Spec 10 defines).

## Invariants

- Every URL passed to the network stack has passed the SSRF guard at least once for the current redirect hop.
- `Document.markdown` is bounded by `max_bytes` × extraction ratio; oversize → `too_large`.
- `Document.status != "ok"` ⇒ `markdown` is empty string, not partial.
- `redirect_chain` final entry equals `Document.url`.
- No raw HTML, no `<script>` content, and no hidden-CSS text appears in `markdown`.
- `content_hash` is `sha256(markdown.encode("utf-8"))` hex.

## Failure modes

| Failure | `status` | Behavior |
|---|---|---|
| SSRF guard reject | `blocked_by_ssrf_guard` | Do not connect; log structured event. |
| Robots disallow | `blocked_by_robots` | Do not connect; log. |
| Per-host rate exceeded | `rate_limited` | Return immediately; log. |
| Browser pool full > 5s wait | `timeout` | Return; log. |
| `timeout_s` exceeded | `timeout` | Cancel; release context. |
| HTTP 4xx | `http_4xx` | Capture `http_status`. |
| HTTP 5xx (after one retry) | `http_5xx` | Capture `http_status`. |
| Content-Type not text/html or text/markdown or application/xhtml+xml | `unsupported_content_type` | Return. |
| `max_bytes` exceeded mid-stream | `too_large` | Cancel download. |
| Extractor exception | `extraction_failed` | Return; log with stack. |

## Security requirements

- See SSRF guard above (P0).
- Per Spec 12, container runs with no IAM credentials, no service-account token mount, ephemeral writable FS, egress firewall.
- `render_js=True` runs Chromium with `--disable-dev-shm-usage --no-zygote`, no extension load, no profile reuse.
- User-Agent identifies the service (`NexusAgenticSearch/<version> (+contact)`) — no UA spoofing.
- TLS verification always on; no `verify=False`.
- Cookies: never sent across hosts; per-request cookie jar discarded after fetch.

## Telemetry contract

Span `crawl.fetch`
- Attributes: `requested_url`, `final_url`, `render_js`, `http_status`, `status`, `redirect_count`, `bytes_in`, `render_ms`, `extraction_ms`, `domain`, `robots_blocked` (bool).
- Records exceptions with type only.

Metrics
- `crawl_latency_ms{render_js}` histogram.
- `crawl_status_total{status}` counter.
- `crawl_bytes_in` histogram.
- `crawl_domain_budget_remaining{domain}` gauge.
- `crawl_browser_pool_in_use` gauge.

## Out of scope / deferred

- Link-following / BFS deep crawl.
- PDF / DOCX / EPUB ingestion.
- Authenticated crawls (cookies-jar, login flows).
- Headless screenshotting.
- Anti-bot bypass / proxy rotation.

## Open questions

- Default UA contact address.
- Whether to add a per-request "fast path" that skips robots for cached robots responses < 5 min old (latency optimization).
