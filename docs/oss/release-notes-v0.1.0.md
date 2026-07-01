# v0.1.0 Release Notes Draft

`nexus-agentic-search` is a self-hosted backend for agentic web search.
It exposes both an HTTP API and an MCP tool surface, then combines web search, guarded crawl, citation validation, and LLM synthesis into grounded answers.

## Highlights

- Grounded search pipeline with validated citations
- HTTP transport for direct integrations
- MCP transport for tool-based agent clients
- Conservative security defaults around auth, SSRF, redaction, and startup self-tests
- Self-hosted local-first setup with SearXNG and configurable LLM providers

## Included In v0.1.0

- Search routing with Brave-first and SearXNG fallback support
- Crawl protections including SSRF checks, redirect re-validation, robots handling, and rate limiting
- Citation validation against fetched document content
- LiteLLM-based provider routing with budget guards and redaction
- FastAPI HTTP API and FastMCP MCP server behind bearer auth
- Structured logs, Prometheus metrics, and OTel tracing hooks
- Test, lint, typecheck, and dependency-audit coverage in CI

## Intended Use

This release is suitable for:

- local evaluation
- self-hosted experimentation
- personal assistants and developer tooling that need grounded web answers

This release is not positioned as:

- a hosted search service
- a universal production deployment stack
- a general browser automation platform

## Known Limits

- deployment examples are intentionally not one-click production installers
- provider defaults may need adjustment for your own local or cloud setup
- advanced infra notes and deployment scripts are still operator-oriented

## Getting Started

See:

- [`README.md`](../../README.md)
- [`docs/oss/demo-snippets.md`](demo-snippets.md)
- [`docs/oss/launch-checklist.md`](launch-checklist.md)
