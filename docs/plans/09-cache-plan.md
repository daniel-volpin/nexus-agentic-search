# Plan 09 — Cache

> Spec: [`docs/specs/09-cache.md`](../specs/09-cache.md) · spec wins on disagreement.

## Module layout

```
nexus/cache/
├── namespaces.py       # named diskcache.Cache instances
├── diskcache_backend.py # wrapper providing schema-version + redaction
├── keys.py             # canonical key builders
└── types.py            # CacheError, NamespaceConfig

tests/unit/cache/
├── test_namespaces.py
├── test_diskcache_backend.py
├── test_keys.py
└── test_ttl.py
tests/integration/test_cache_e2e.py
```

## Public symbols

```python
# nexus/cache/types.py
class CacheError(Exception): ...
class CacheDisabled(CacheError): ...

# nexus/cache/keys.py
def search_key(req: SearchRequest) -> str: ...
def rerank_key(query: str, urls: list[str]) -> str: ...
def crawl_doc_key(canonical_url: str, render_js: bool, max_bytes: int) -> str: ...
def robots_key(host: str) -> str: ...
def cost_daily_key(role: str, date_utc: date) -> str: ...

# nexus/cache/diskcache_backend.py
class DiskCacheBackend:
    def __init__(self, root: Path, namespace: str, ttl_default_s: int, version: int): ...
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl_s: int | None = None) -> None: ...
    async def incr(self, key: str, delta: int, ttl_s: int) -> int: ...   # for cost counter

# nexus/cache/namespaces.py
SEARCH_BRAVE: DiskCacheBackend       # ttl 6h
SEARCH_SEARXNG: DiskCacheBackend     # ttl 3h (shorter — engines flap)
RERANK_BGE: DiskCacheBackend         # ttl 24h
CRAWL_DOCUMENT: DiskCacheBackend     # ttl 24h
CRAWL_ROBOTS: DiskCacheBackend       # ttl 24h
COST_DAILY: DiskCacheBackend         # ttl 48h
```

## External dependencies

| Package | Why |
|---|---|
| `diskcache` | SQLite-backed disk cache, async-friendly via `to_thread`. |
| `orjson` | Serialization. |

## Build order

1. **`keys.py`** — pure functions. Each key builder produces `sha256(canonical_form)[:32]` to keep keys short. Canonical form is the documented in Spec 09 (e.g., `q|f|lang|country|max_results`). For `search_key` the query is hashed first (never in plaintext). ➜ `test_keys.py`: stability tests; same inputs → same key, different inputs → different key.
2. **`DiskCacheBackend`**.
   - Wraps a per-namespace `diskcache.Cache` directory: `/var/lib/nexus/cache/<namespace>/`.
   - `get(key)`: reads. Validates `value["version"] == self.version`; mismatch → None. Validates `value["expires_at"] > now()`; expired → None.
   - `set(key, value, ttl_s)`: writes `{value, version, expires_at}`. Wrapped in `asyncio.to_thread` with 100ms timeout — on timeout, log WARN and return.
   - `incr(key, delta, ttl_s)`: read-modify-write under diskcache's transaction. Used by cost daily counter.
   - Errors: any diskcache exception → log WARN + treat as miss / silently skip write.
3. **`namespaces.py`** — module-level constants, constructed in a `setup_cache(root: Path) -> None` function called from `main.py`. Schema versions:
   ```python
   SEARCH_BRAVE_VERSION = 1
   SEARCH_SEARXNG_VERSION = 1
   RERANK_BGE_VERSION = 1
   CRAWL_DOCUMENT_VERSION = 1
   CRAWL_ROBOTS_VERSION = 1
   COST_DAILY_VERSION = 1
   ```
   Bumping a version invalidates all existing entries.
4. **Integration with components**:
   - `nexus/search/brave.py`: check `SEARCH_BRAVE.get(search_key(req))`; on miss, fetch and `set`.
   - `nexus/search/searxng.py`: same with `SEARCH_SEARXNG`.
   - `nexus/rerank/__init__.py`: check `RERANK_BGE` keyed by `rerank_key(query, [r.url for r in candidates])`.
   - `nexus/crawl/crawler.py`: check `CRAWL_DOCUMENT` before fetch.
   - `nexus/crawl/robots.py`: check `CRAWL_ROBOTS` before fetch.
   - `nexus/llm/cost.py`: use `COST_DAILY` for daily counters.
5. **Self-test at startup**: `setup_cache` does a write+read+delete to verify the cache directory is writable. On failure, set `service_ready=1` anyway but log CRITICAL and run in cache-disabled mode.
6. **Eviction** — diskcache's built-in LRU with size cap. Configure `cull_limit` and `size_limit` per namespace.

## Configuration loading

```python
class CacheConfig(BaseSettings):
    root: Path = Path("/var/lib/nexus/cache")
    total_size_gb: float = 2.0
    enabled: bool = True
    search_brave_ttl_s: int = 6 * 3600
    search_searxng_ttl_s: int = 3 * 3600
    rerank_bge_ttl_s: int = 24 * 3600
    crawl_document_ttl_s: int = 24 * 3600
    crawl_robots_ttl_s: int = 24 * 3600
    cost_daily_ttl_s: int = 48 * 3600
```

## Test plan (mapping to spec invariants)

| Spec invariant | Test |
|---|---|
| Key is always a hex digest | `test_keys::test_keys_are_hex` (regex) |
| Value never contains API keys / tokens | `test_diskcache_backend::test_value_has_no_secrets` (writes a synthetic value with a fake key pattern and asserts redaction would catch it on read — actually: the cache stores values unredacted; the protection is that *we never put secrets in values*. Test asserts that the search/rerank/crawl values we *would* store match a schema with no secret fields.) |
| Schema-version mismatch → miss | `test_diskcache_backend::test_version_mismatch` |
| TTL upper bound respected even before eviction | `test_ttl.py` |
| Write timeout doesn't block request | `test_diskcache_backend::test_write_timeout_nonblocking` |
| Disk full → writes skipped, reads continue | `test_diskcache_backend::test_disk_full_simulated` (mock diskcache to raise) |
| Cache disabled mode → all ops no-op | `test_diskcache_backend::test_disabled_mode` |

## Risks & mitigations

- **diskcache locking on concurrent writes** — diskcache handles this; we test with 16 concurrent writers to same key.
- **Cache poisoning** if a hostile process writes to the cache file: file mode 0600, container owns the volume, container is the only writer. Documented.
- **Stale crawl docs being cited** — Spec 04 validates citations against the same `documents` dict the orchestrator passed; the document in that dict can come from cache, but the byte offsets are still consistent because the markdown is the same. No risk.
- **Cost-counter race** under concurrent calls — `incr()` uses diskcache transaction; assert with a stress test.

## Done criteria
- [ ] All unit + integration tests pass.
- [ ] Stress test: 100 concurrent search queries hit the cache layer; final entry count matches expected.
- [ ] Cache-disabled mode lets the system still serve requests (slower) — integration test asserts.
- [ ] `mypy --strict` clean.
