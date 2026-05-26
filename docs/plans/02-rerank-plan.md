# Plan 02 — Rerank Component

> Spec: [`docs/specs/02-rerank.md`](../specs/02-rerank.md) · spec wins on disagreement.

## Module layout

```
nexus/rerank/
├── bge.py              # model load, scorer
├── dedup.py            # token-Jaccard near-duplicate filter
├── diversity.py        # per-domain cap walker
└── __init__.py         # rerank() convenience entry

tests/unit/rerank/
├── test_bge_scorer.py
├── test_dedup.py
├── test_diversity.py
└── test_rerank_integration.py
```

## Public symbols

```python
# nexus/rerank/__init__.py
def rerank(
    query: str,
    candidates: list[Result],
    *,
    top_k: int = 8,
    per_domain_cap: int = 2,
    scorer: BgeScorer | None = None,
) -> list[RankedResult]: ...

# nexus/rerank/bge.py
class BgeScorer:
    def __init__(self, model_path: str, device: str = "cpu"): ...
    def score(self, pairs: list[tuple[str, str]]) -> list[float]: ...

# nexus/rerank/dedup.py
def near_duplicate_jaccard(a: str, b: str) -> float: ...
def drop_near_duplicates(items: list[RankedResult], threshold: float = 0.85) -> list[RankedResult]: ...

# nexus/rerank/diversity.py
def apply_per_domain_cap(items: list[RankedResult], cap: int) -> list[RankedResult]: ...
```

## External dependencies

| Package | Why |
|---|---|
| `FlagEmbedding` or `sentence-transformers` | bge-reranker-base inference. Pick `FlagEmbedding` (lighter, official from BAAI) at impl unless `sentence-transformers` is already present for embeddings. |
| `torch` | Required by model runtime. CPU build (`torch==X+cpu`) by default. |
| `tldextract` | Registrable-domain extraction for per-domain cap. |

Model weights downloaded once into the `nexus-models` volume at first startup. Pin by repo + revision SHA in config; verify file sha256 after download against pinned digest.

## Build order

1. **Type alias** `RankedResult` defined in `nexus/search/types.py` (small enough that it lives next to `Result`).
2. **BgeScorer** (`bge.py`). Wraps the model. Load at process startup (eager) so first request isn't slow. Batch all pairs in a single forward pass (Spec invariant: deterministic on CPU). Apply sigmoid to logits. ➜ `test_bge_scorer.py` tests with a tiny fake model (monkeypatch the forward call) and asserts deterministic output, batched call shape, score range [0,1].
3. **Dedup** (`dedup.py`). Tokenize on whitespace, lower, ASCII-fold (NFKD + strip diacritics) for the comparison only. Jaccard threshold 0.85 over title+snippet. ➜ `test_dedup.py` covers near-duplicates, synonyms (should NOT collapse), edge cases (empty strings).
4. **Diversity** (`diversity.py`). Walk sorted-by-score list, count `tldextract` registrable domain hits, skip when count reaches cap. ➜ `test_diversity.py` covers identical domain, subdomains under same eTLD+1 (should count together), edge cases.
5. **`rerank()` entry** (`__init__.py`). Compose: build pairs → score → sort → diversity → dedup → top_k. Telemetry span here. ➜ `test_rerank_integration.py` covers the full flow with a small fake scorer; asserts top_k bound, no domain over cap, no dupes.
6. **Fallback path**. If `BgeScorer.score` raises or model not loaded: return provider-rank top_k with `degraded=True` flag on the orchestrator response. Implemented as a try/except in `rerank()`. ➜ explicit test.

## Configuration loading

```python
class RerankConfig(BaseSettings):
    model_repo: str = "BAAI/bge-reranker-base"
    model_revision: str  # pinned commit SHA, required (no default — fail-closed)
    model_dir: Path = Path("/var/lib/nexus/models")
    device: Literal["cpu","cuda"] = "cpu"
    top_k_default: int = 8
    per_domain_cap_default: int = 2
    near_dup_threshold: float = 0.85
    max_candidates: int = 30
```

## Test plan (mapping to spec invariants)

| Spec invariant | Test |
|---|---|
| Output ≤ top_k | `test_rerank_integration::test_top_k_bound` |
| No duplicate canonical URLs | `test_dedup::test_url_dedup` (input pre-deduped by Plan 01; rerank still asserts) |
| No domain > cap | `test_diversity::test_per_domain_cap` |
| Deterministic on CPU | `test_bge_scorer::test_deterministic` |
| Latency ≤ 3s for ≤ 30 candidates | `test_rerank_integration::test_latency_budget` (with real model in optional slow suite) |
| Fail-closed on load failure | `test_bge_scorer::test_load_failure_raises` |
| OOM mid-request → fallback | `test_rerank_integration::test_oom_fallback` (monkeypatch scorer to raise) |
| `candidates` len > 30 truncated | `test_rerank_integration::test_truncates_oversized_input` |

## Risks & mitigations

- **Cold start latency** (model load adds 5–15s). Mitigation: load eagerly at `main.py` startup, expose `service_ready` gauge so transports refuse traffic until model is loaded.
- **Memory ceiling** (bge-reranker-base ≈ 280MB). Mitigation: container `mem_limit: 4g` covers; soft check at startup against `psutil.virtual_memory().available`.
- **CPU contention with crawl** on home server. Mitigation: rerank batch runs in `asyncio.to_thread` to release event loop; bge is CPU-bound and short (~1s) so blocking is acceptable. Document explicitly.
- **Model revision drift** if `model_revision` isn't pinned. Mitigation: config validator requires non-default value; image build script verifies sha256 against config.

## Done criteria
- [ ] All unit tests pass.
- [ ] `bge-reranker-base` loads at startup in container; `service_ready` becomes 1 within 30s.
- [ ] Rerank latency p95 ≤ 3s for 20 candidates on the target box (recorded baseline committed at `tests/perf/rerank_baseline.json`).
- [ ] Fallback path verified by load test (kill the model worker → rerank gracefully degrades).
- [ ] `mypy --strict` clean.
