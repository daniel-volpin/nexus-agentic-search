# Nexus Agentic Search — developer entrypoints
# Run `make help` for the full list.

.PHONY: help install lint format typecheck \
        test test-unit test-security test-integration test-load test-golden test-cov \
        clean

help:  ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install:  ## Install dev dependencies via uv.
	uv sync

lint:  ## Run ruff lint (no fixes).
	uv run ruff check nexus tests

format:  ## Apply ruff format + lint fixes.
	uv run ruff format nexus tests
	uv run ruff check --fix nexus tests

format-check:  ## Check formatting without modifying files.
	uv run ruff format --check nexus tests

typecheck:  ## Run mypy (advisory until baseline is clean).
	uv run mypy nexus

test: test-unit test-security  ## Run unit + security tests (default).

test-unit:  ## Run unit tests.
	uv run pytest -q tests/unit

test-security:  ## Run adversarial security tests (Spec 13).
	uv run pytest -q tests/security

test-integration:  ## Run integration tests.
	uv run pytest -q tests/integration

test-load:  ## Run load tests.
	uv run pytest -q tests/load

test-golden:  ## Run golden-quality suite (requires GOLDEN_LIVE=1 + API keys).
	GOLDEN_LIVE=1 uv run pytest -q tests/golden

test-cov:  ## Run unit + security tests with coverage.
	uv run pytest -q tests/unit tests/security \
	  --cov=nexus --cov-report=term-missing --cov-report=xml

clean:  ## Remove caches and build artefacts.
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
