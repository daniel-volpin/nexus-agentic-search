# nexus-agentic-search

Minimal setup for local development uses **uv**.

## Quick start

```bash
uv sync --dev
uv run pytest -q
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
