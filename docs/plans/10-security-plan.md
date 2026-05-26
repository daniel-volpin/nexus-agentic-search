# Plan 10 — Security (Cross-Cutting)

> Spec: [`docs/specs/10-security.md`](../specs/10-security.md) · spec wins on disagreement.

This plan does NOT introduce a new module; it consolidates the cross-cutting work that other plans cite. Each item below lives in the component listed and is built per that component's plan. The purpose of this plan is the **security build & test checklist** that gates every release.

## Code locations (consolidated)

| Concern | Lives in | Plan |
|---|---|---|
| SSRF guard | `nexus/crawl/ssrf.py` | 03 |
| Untrusted-source envelope (canonical) | `nexus/crawl/envelope.py` (re-export at `nexus/security/envelope.py`) | 03 |
| Synthesis system preamble | `nexus/orchestrator/prompts.py` | 06 |
| Synthesis tools-disabled enforcement | `nexus/llm/client.py` | 05 |
| Logger redaction filter | `nexus/llm/redact.py` | 05 |
| Egress firewall self-test (runtime) | `nexus/security/selftest.py` | this plan |
| Bearer auth (both transports) | `nexus/transport/auth.py` | 07/08 |
| Citation envelope-violation reject | `nexus/citations/validator.py` | 04 |
| Cost cap / budget enforcement | `nexus/llm/cost.py`, `nexus/orchestrator/budget.py` | 05/06 |

## New module: `nexus/security/selftest.py`

Performs runtime checks at startup. Called from `main.py` before `service_ready` flips to 1.

```python
async def run_selftest() -> SelftestReport: ...

@dataclass(frozen=True)
class SelftestReport:
    egress_firewall_ok: bool
    ssrf_guard_ok: bool
    redaction_ok: bool
    synthesis_tools_disabled_ok: bool
    failures: list[str]
```

Checks:

1. **Egress firewall self-test**: attempt outbound TCP connect to `10.255.255.1:81` (host that should be blocked by host firewall). Expect a connection refused / timeout within 1s. SUCCESS = connection failed; FAILURE = connection succeeded or hung > 1.5s. Logged + recorded; failure marks `egress_firewall_ok=False`.
2. **SSRF guard self-test**: call `safe_resolve("http://169.254.169.254/")` and assert it raises `SSRFViolation`. Repeat for `http://[::1]/`, `http://192.168.1.1/`.
3. **Redaction self-test**: emit a log record containing `sk-test123456789012345678901234567890` through the configured logger; capture stdout; assert `[REDACTED]` present, original key absent.
4. **Synthesis tools-disabled self-test**: call `llm.complete(role="synthesis", messages=[...], tools=[some_tool_spec])` and assert it raises with a clear error before any provider call.

The selftest runs at startup. Failures policy:

- `redaction_ok=False` → **fail-closed**, service does NOT start.
- `synthesis_tools_disabled_ok=False` → **fail-closed**.
- `ssrf_guard_ok=False` → **fail-closed**.
- `egress_firewall_ok=False` → **log CRITICAL, continue**, increment `egress_firewall_missing` gauge to 1; deployment may have skipped the iptables script and Spec 12 documents this is acceptable in dev. In compose-managed prod, the alert fires.

## Test plan (catalog enforced)

The full security test catalog is split across components but **must all be present** before release:

### From Plan 03
- `tests/security/test_ssrf.py` — full SSRF catalog (10 cases).
- `tests/security/test_envelope.py` — envelope-injection catalog.

### From Plan 04
- `tests/security/test_citations.py` — fabrication, envelope-violation, ReDoS, unicode confusables.

### From Plan 05
- `tests/security/test_redaction.py` — every key pattern + env-scrub.
- `tests/security/test_budget.py` — daily-cap, per-request, propagation.

### From Plan 06
- `tests/security/test_prompt_assembly.py` — no bearer token in messages, envelope wrap, synthesis-tools-rejected.

### From Plan 07/08
- `tests/security/test_mcp_surface.py` — exactly one tool, no internal tools, description clean.
- `tests/security/test_http_surface.py` — no openapi docs, no header injection, no traceback leak.

### From Plan 09
- `tests/security/test_cache_secrets.py` — schema assertion: no field in cached values matches secret patterns.

### From Plan 12
- `tests/integration/test_egress_firewall.py` — runs inside the deployed container; verifies the runtime self-test passes.

## Release-gate security checklist

Each release must produce a signed-off checklist:

- [ ] All security tests pass (each catalog above green).
- [ ] `pip-audit` reports no HIGH/CRITICAL CVE in pinned deps.
- [ ] `trivy image` reports no HIGH/CRITICAL CVE in image layers.
- [ ] `selftest` passes inside the container (`docker exec nexus-search python -m nexus.security.selftest`).
- [ ] `citations_envelope_violations_total` metric has been observed to fire in test (proof the detector works); is at zero in production observability for the last 7 days.
- [ ] No `BRAVE_API_KEY`/`OPENAI_API_KEY` etc. value appears in the container image (`docker run --rm <image> env | grep -iE 'sk-|AIza|api_key'` returns nothing).
- [ ] Bearer tokens rotated in the last 90 days (or marked-and-justified-otherwise).

## Adversarial review checklist (manual, per release)

- [ ] Read every `# TODO` and `# FIXME` in `nexus/` and confirm none defer a security control.
- [ ] Read every line touching `httpx.AsyncClient`, `playwright.async_api.Browser`, or `httpx.HTTPTransport` and confirm `safe_resolve()` precedes every outbound destination decision.
- [ ] Read every `LLMClient.complete` call site and confirm: never receives the bearer token, env values, or cache key plaintext in `messages`.
- [ ] Read every log site using `.exception()` or `traceback.format_exc()` and confirm redaction is applied.
- [ ] Read MCP tool descriptions; absent instruction-shaped text.

## Risks (residual)

- **Crawl4AI internal HTTP path** could change to bypass our wrapper. Mitigation: `test_ssrf_invoked_on_every_fetch` asserts our wrapper is the only fetch entry point; static import-graph check in CI.
- **A new LLM provider** added to fallback list ships with different prompt-injection vulnerability profile. Mitigation: tests run against each provider in slow optional suite.
- **A future code change** introduces a `read_env_for_debug` path that violates redaction. Mitigation: `tests/security/test_redaction.py::test_env_never_logged` covers; CI keeps it green.

## Done criteria
- [ ] `nexus/security/selftest.py` lands with all four checks.
- [ ] Selftest is called from `nexus/main.py` startup before transports begin accepting traffic.
- [ ] Release-gate checklist template lives at `docs/RELEASE_CHECKLIST.md` and is filled per release.
- [ ] All cross-cutting security tests cataloged in the table above exist and pass.
