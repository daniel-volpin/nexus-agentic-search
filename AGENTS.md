# Nexus Agentic Search Agent Guide

Self-hosted grounded web search backend with HTTP API and Model Context Protocol (MCP) server endpoints.

## Quick Reference & Local Surfaces

- **HTTP API**: `http://127.0.0.1:8186` (routes: `/v1/search`, `/v1/search/stream`, `/health`)
- **MCP Server**: `http://127.0.0.1:8185/mcp`
- **SearXNG Backend**: `http://127.0.0.1:8080`
- **Configuration**: `config/llm.toml`, `config/search.toml`, `compose.yaml`

## Commands

```bash
uv sync                                    # Install dependencies
uv run ruff check nexus tests              # Lint (or `make lint`)
uv run ruff format --check nexus tests     # Formatting check
uv run mypy nexus                          # Typecheck (or `make typecheck`)
uv run pytest -q tests/unit tests/security # Unit + security tests (or `make test`)
uv run pytest -q tests/integration         # Integration tests (requires running services)
```

## Hard Rules & Security Invariants

- **Token Protection**: Never print, log, or commit tokens or `.env` files. Both `NEXUS_HTTP_TOKEN` and `NEXUS_MCP_TOKEN` are required for startup.
- **SSRF & Crawl Guardrails**: Preserve all SSRF validation, private IP blocking, domain blocklists, and size limits on web crawl.
- **LLM Synthesis & Budgets**: Keep token and context budgets bounded to prevent runaway synthesis loops.
- **Citation Integrity**: Ensure citation markers `[^claim-X]` and URL anchors match validated crawl snippets.

## Working Style & Verification

- Before committing changes, run:
  ```bash
  make lint && make typecheck && make test
  ```
- Keep diffs focused on search pipeline, MCP handlers, or provider adapters without altering external security contracts.
