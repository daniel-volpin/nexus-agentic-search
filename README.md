# nexus-agentic-search

`nexus-agentic-search` is a self-hosted backend for agentic web search.
It exposes:

- an HTTP API for grounded search and answer generation
- an MCP server for tool-based clients
- a search pipeline that combines web search, crawl, citation validation, and LLM synthesis

The repo is intentionally conservative about security: bearer auth on both transports, SSRF protections on crawl, startup self-tests, secret redaction, and bounded LLM budgets.

## Status

- Stable enough to evaluate locally:
  - HTTP and MCP transports
  - search + crawl + citation pipeline
  - security and regression test suites
- Experimental or operator-specific:
  - deployment scripts under `deploy/`
  - advanced infra notes under [`docs/deploy/`](docs/deploy/README.md)
  - model/provider defaults in [`config/llm.toml`](config/llm.toml)

## Architecture

Request flow:

1. authenticated HTTP or MCP request enters the service
2. search providers return candidate results
3. crawl fetches selected pages with SSRF and robots controls
4. reranking and deduplication choose grounded sources
5. the LLM synthesizes an answer with validated citations

Key modules:

- [`nexus/search`](nexus/search) for Brave + SearXNG routing
- [`nexus/crawl`](nexus/crawl) for guarded fetching and extraction
- [`nexus/citations`](nexus/citations) for citation validation
- [`nexus/llm`](nexus/llm) for provider routing, budget checks, and redaction
- [`nexus/http`](nexus/http) and [`nexus/mcp`](nexus/mcp) for public transports

## Local Setup

Requirements:

- Python 3.11
- `uv`
- one working search path:
  - a reachable SearXNG instance, or
  - a Brave API key
- one working LLM path:
  - local LM Studio models matching [`config/llm.toml`](config/llm.toml), or
  - a Gemini API key, or
  - another provider key after adjusting [`config/llm.toml`](config/llm.toml)

Quick start:

```bash
uv sync --dev
cp .env.example .env.local
```

Set at least:

```bash
NEXUS_HTTP_TOKEN=0123456789abcdef0123456789abcdef
NEXUS_MCP_TOKEN=fedcba9876543210fedcba9876543210
SEARXNG_BASE_URL=http://127.0.0.1:8080
GEMINI_API_KEY=your-key-here
GOOGLE_API_KEY=your-key-here
```

Then run:

```bash
set -a
source .env.local
set +a
uv run python -m nexus.main
```

Useful checks:

```bash
uv run pytest -q
uv run ruff check nexus tests
uv run mypy nexus
```

Notes:

- The service refuses to start without both bearer tokens.
- For search, you need either a reachable SearXNG endpoint or `BRAVE_API_KEY`.
- The default LLM profile is local-first (`lmstudio/...`) with cloud fallbacks.
- If you do not run LM Studio locally, provide cloud credentials or edit [`config/llm.toml`](config/llm.toml) to match your provider setup.

## Optional Compose Setup

The compose stack is mainly for operator testing and adjacent-container deployments.
It does not publish host ports by default.
It is not the default host-machine developer workflow.

Use the compose-specific env templates:

```bash
cp secrets/nexus.env.example secrets/nexus.env
cp secrets/searxng.env.example secrets/searxng.env
```

Then fill values and start:

```bash
docker compose up -d --build
```

See [`docs/deploy/README.md`](docs/deploy/README.md) before treating anything under `deploy/` as production-ready.

## Configuration

- [`.env.example`](.env.example): local shell environment example
- [`secrets/nexus.env.example`](secrets/nexus.env.example): compose env file example
- [`config/llm.toml`](config/llm.toml): model/provider routing and token limits

Search defaults:

- SearXNG sidecar for metasearch
- optional Brave API key for a higher-confidence primary provider

LLM defaults:

- LM Studio primary models
- Gemini / Vertex / OpenAI fallbacks, depending on available credentials

## Docs

- [`docs/specs/`](docs/specs): design specs
- [`docs/decisions/`](docs/decisions): architecture decisions
- [`docs/plans/`](docs/plans): historical implementation plans
- [`docs/deploy/`](docs/deploy/README.md): optional deployment patterns and operator notes

## Open Source Notes

- No real credentials should be committed; use local env files only.
- `deploy/` contains examples, not a supported universal deployment system.
- The documented public scope is local evaluation and self-hosted experimentation, not turnkey production.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).
