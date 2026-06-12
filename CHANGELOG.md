# Changelog

## v0.1.0 — 2026-06-12

First tagged release. Self-hosted agentic web-search MCP backend for
home-server containers.

### Features

- **Search router** — Brave-first with SearXNG fallback (google +
  duckduckgo). Circuit breaker and captcha detection on SearXNG.
  Runs fully free with SearXNG only (no Brave key required).
- **Crawl** — async httpx with SSRF connect-with-IP defense, per-hop
  redirect re-validation, robots.txt, per-domain rate limiting, and
  hidden-content / script stripping before LLM synthesis.
- **Citations** — span-bound validation against crawled markdown;
  envelope-violation rejection. Untrusted-source enveloping.
- **LLM gateway** — LiteLLM with role-pinned dated model IDs, daily
  USD budget guard, secret redaction, and synthesis tools disabled at
  the API boundary. Default profile: Gemini free tier.
- **Cache** — per-namespace diskcache with JSONDisk (no pickle / RCE),
  schema-version envelopes, 100 ms write timeout, atomic counters.
- **Security** — fail-closed runtime self-test (SSRF guard, redaction,
  synthesis tools disabled — critical), untrusted-source enveloping,
  secret redaction filter.
- **Transports** — FastAPI HTTP + FastMCP MCP, both behind bearer auth.
- **Observability** — Prometheus metrics (populated via
  PrometheusTelemetrySink), OTel tracing, structured JSON logs, metrics
  on a separate port.
- **Deployment** — multi-stage Dockerfile (non-root, read-only root FS),
  Docker Compose with SearXNG sidecar, token rotation script, SSRF
  egress firewall, build-and-pin / rollback scripts.
- **CI** — ruff, mypy (0 errors), pytest (515 tests), pip-audit (all
  blocking).
- **Reranker** — deterministic lexical scorer (zero-dep, explainable).
  Cross-encoder interface preserved for future upgrade if hardware
  permits.
