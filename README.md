# nexus-agentic-search

Minimal setup for local development uses **uv**.

## Quick start

```bash
uv sync --dev
uv run pytest -q
```

## Run locally

```bash
export NEXUS_HTTP_TOKEN=secret
export NEXUS_MCP_TOKEN=secret
uv run python -m nexus.main
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


## Reranker model decision (Spec 02)

We are standardizing on **cross-encoder reranking** for top-K candidates.

Current default target model: `BAAI/bge-reranker-v2-m3` (open weights, multilingual, strong quality/latency tradeoff for local CPU-first deployments).

A short decision record with comparison criteria is in `docs/decisions/02-reranker-model-choice.md`.
