# nexus-agentic-search

Minimal setup for local development uses **uv**.

## Quick start

```bash
uv sync --dev
uv run pytest -q
```

## Run locally

```bash
export NEXUS_HTTP_TOKEN=0123456789abcdef0123456789abcdef
export NEXUS_MCP_TOKEN=fedcba9876543210fedcba9876543210
export SEARXNG_ENGINES=duckduckgo
export LMSTUDIO_BASE_URL=http://127.0.0.1:1234
uv run python -m nexus.main
```

The shipped LLM defaults are `LM Studio first, cloud fallback`. If your loaded local model names differ from the defaults in [`config/llm.toml`](/Users/pnl11e4o/Documents/GitHub/personal-github/nexus-agentic-search/config/llm.toml), update those `lmstudio/...` entries to match what LM Studio exposes from `GET /v1/models`.

For long-term cloud support, defaults prioritize `vertex_ai/gemini-2.5-flash-lite`, with `openai/gpt-4o-mini-2024-07-18` as cheap fallback.

For private SearXNG deployments, set `SEARXNG_BASE_URL` to your HTTPS endpoint and set `SEARXNG_API_KEY`; the client sends it as `X-Searx-Key`.

## Vertex AI setup (recommended)

1. Enable APIs in your GCP project:
   - Vertex AI API (`aiplatform.googleapis.com`)
   - IAM Service Account Credentials API (`iamcredentials.googleapis.com`)
2. Create a service account and grant least-privilege Vertex access.
3. Create a JSON key for that service account (or use ADC if preferred).
4. Set env vars:
   - `VERTEX_PROJECT=your-gcp-project-id`
   - `VERTEX_LOCATION=global`
   - `GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account-key.json`

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


## Reranker model decision (Spec 02)

We are standardizing on **cross-encoder reranking** for top-K candidates.

Current default target model: `BAAI/bge-reranker-v2-m3` (open weights, multilingual, strong quality/latency tradeoff for local CPU-first deployments).

A short decision record with comparison criteria is in `docs/decisions/02-reranker-model-choice.md`.
