# nexus-agentic-search

Minimal setup for local development uses **uv**.

## Quick start

```bash
uv sync --dev
uv run pytest -q
```

## Run locally

Bearer tokens must be at least 32 characters:

```bash
export NEXUS_HTTP_TOKEN=$(openssl rand -base64 24)
export NEXUS_MCP_TOKEN=$(openssl rand -base64 24)
uv run python -m nexus.main
```

## Run it free (no paid APIs)

The default profile costs nothing:

- **Search** — leave `BRAVE_API_KEY` unset; the router uses the
  self-hosted SearXNG sidecar (google + duckduckgo).
- **LLM** — `config/llm.toml` points every role at Gemini's free tier;
  set `GEMINI_API_KEY` from <https://aistudio.google.com/apikey>.

The full stack (service + SearXNG) comes up with:

```bash
cp secrets/nexus.env.example secrets/nexus.env   # fill in GEMINI_API_KEY + tokens
cp secrets/searxng.env.example secrets/searxng.env
docker compose up -d
curl -s localhost:8186/v1/health
```

## Common commands

- Run search + rerank unit tests:

  ```bash
  uv run pytest tests/unit/search tests/unit/rerank -q
  ```

- Add a dependency:

  ```bash
  uv add <package>
  ```

- Add a dev dependency:

  ```bash
  uv add --dev <package>
  ```


## Reranking

The current default reranker is a deterministic **lexical** scorer
(query/candidate token overlap) — real and explainable, with no heavy
model dependency. The interface matches a cross-encoder, so swapping in
`BAAI/bge-reranker-v2-m3` (open weights, multilingual) later is a config
change, not a rewrite. Decision record: `docs/decisions/02-reranker-model-choice.md`.
