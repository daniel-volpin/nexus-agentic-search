# LM Studio Local-First Plan

Goal: make the default LLM path prefer a locally loaded LM Studio model and cleanly fall back to cloud providers when the local model is unavailable.

Execution order:
1. Add failing tests for `lmstudio/...` model ids, local-model detection, and cloud fallback.
2. Implement LM Studio availability probing and request resolution inside the existing LLM client boundary.
3. Switch `config/llm.toml` defaults to LM Studio first while keeping current cloud fallbacks.
4. Document the minimal local run path with `SEARXNG_ENGINES=duckduckgo` and LM Studio on `127.0.0.1:1234`.

Acceptance:
- `lmstudio/...` model ids validate.
- If LM Studio reports the configured model, that model is used first.
- If LM Studio is down or the model is missing, the role falls through to existing cloud fallbacks.
- Existing tests, lint, and mypy stay green.
