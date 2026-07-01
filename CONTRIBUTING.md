# Contributing

## Setup

```bash
uv sync --dev
cp .env.example .env.local
```

Export variables before running the service:

```bash
set -a
source .env.local
set +a
```

## Expected Checks

```bash
uv run ruff check nexus tests
uv run ruff format --check nexus tests
uv run mypy nexus
uv run pytest -q tests/unit tests/security
```

## Scope

- Prefer focused fixes and small PRs.
- Avoid redesigning the system in drive-by contributions.
- Keep security behavior intact unless the change explicitly improves it.

## Secrets And Local Files

- Do not commit `.env`, `secrets/*.env`, keys, tokens, or machine-local paths.
- Use the example files in the repo as templates only.

## Documentation

- Update README or relevant docs when changing setup, transport behavior, or deployment assumptions.
- `docs/specs/` and `docs/plans/` are design artifacts; they are useful context but not every PR needs new spec work.
