# Nexus Agentic Search — developer entrypoints
# Run `make help` for the full list.

.PHONY: help install lint format typecheck \
        test test-unit test-security test-integration test-load test-golden test-cov \
        check-secrets \
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

typecheck:  ## Run mypy (blocking in CI; nexus/ is clean).
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

check-secrets:  ## Verify secrets/nexus.env has the minimum required keys.
	@secrets_file=secrets/nexus.env; \
	example_file=secrets/nexus.env.example; \
	if [ ! -f "$$secrets_file" ]; then \
	  echo "❌  $$secrets_file missing — copy $$example_file and fill in values"; exit 1; \
	fi; \
	required_keys="NEXUS_HTTP_TOKEN NEXUS_MCP_TOKEN"; \
	missing=""; \
	for key in $$required_keys; do \
	  val=$$(grep -E "^$${key}=." "$$secrets_file" 2>/dev/null || true); \
	  if [ -z "$$val" ]; then missing="$$missing $$key"; fi; \
	done; \
	gemini_present=$$(grep -E "^(GEMINI_API_KEY|GOOGLE_API_KEY)=." "$$secrets_file" 2>/dev/null || true); \
	if [ -n "$$missing" ]; then \
	  echo "❌  Missing or empty required keys in $$secrets_file:$$missing"; exit 1; \
	fi; \
	if [ -z "$$gemini_present" ]; then \
	  echo "⚠️  No Gemini credential found in $$secrets_file. This is fine only if LM Studio or another configured provider is available."; \
	fi; \
	echo "✅  secrets/nexus.env looks complete"

clean:  ## Remove caches and build artefacts.
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
