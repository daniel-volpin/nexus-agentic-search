"""Tests for the async diskcache wrapper (Spec 09 §Invariants + §Failure modes)."""

from __future__ import annotations

import asyncio
import stat
import time
from pathlib import Path
from unittest.mock import patch

import diskcache
import pytest

from nexus.cache.diskcache_backend import DiskCacheBackend
from nexus.cache.types import CacheDisabled

# ---------- fixtures ----------


@pytest.fixture
def cache_root(tmp_path: Path) -> Path:
    return tmp_path / "cache"


@pytest.fixture
def backend(cache_root: Path) -> DiskCacheBackend:
    return DiskCacheBackend(
        root=cache_root,
        namespace="test",
        version=1,
        ttl_default_s=60,
        size_limit_bytes=10 * 1024 * 1024,
    )


# ---------- basic CRUD ----------


def test_backend_uses_json_serializer_not_pickle(backend: DiskCacheBackend) -> None:
    """CVE-2025-69872: diskcache's default pickle Disk allows RCE when an
    app reads a cache file an attacker wrote. We must use JSONDisk so the
    cache never unpickles anything."""
    assert isinstance(backend._cache.disk, diskcache.JSONDisk)


async def test_get_returns_none_on_miss(backend: DiskCacheBackend) -> None:
    assert await backend.get("missing") is None


async def test_set_then_get_roundtrip(backend: DiskCacheBackend) -> None:
    await backend.set("k", {"hello": "world"})
    assert await backend.get("k") == {"hello": "world"}


async def test_set_handles_pydantic_compatible_values(backend: DiskCacheBackend) -> None:
    """Values may be any JSON-serializable structure: dicts, lists, primitives."""
    payload = {"a": [1, 2, 3], "b": {"nested": True}, "c": None}
    await backend.set("k", payload)
    assert await backend.get("k") == payload


async def test_delete_removes_entry(backend: DiskCacheBackend) -> None:
    await backend.set("k", "v")
    await backend.delete("k")
    assert await backend.get("k") is None


# ---------- schema versioning ----------


async def test_version_mismatch_returns_miss(cache_root: Path) -> None:
    """Writing under v1 then reading under v2 must yield a miss (Spec 09)."""
    writer = DiskCacheBackend(
        root=cache_root,
        namespace="ns",
        version=1,
        ttl_default_s=60,
        size_limit_bytes=1024 * 1024,
    )
    await writer.set("k", "value-v1")
    writer.close()

    reader = DiskCacheBackend(
        root=cache_root,
        namespace="ns",
        version=2,
        ttl_default_s=60,
        size_limit_bytes=1024 * 1024,
    )
    assert await reader.get("k") is None


async def test_version_round_trip_same_version_hits(cache_root: Path) -> None:
    writer = DiskCacheBackend(
        root=cache_root,
        namespace="ns",
        version=1,
        ttl_default_s=60,
        size_limit_bytes=1024 * 1024,
    )
    await writer.set("k", "value")
    writer.close()

    reader = DiskCacheBackend(
        root=cache_root,
        namespace="ns",
        version=1,
        ttl_default_s=60,
        size_limit_bytes=1024 * 1024,
    )
    assert await reader.get("k") == "value"


# ---------- TTL ----------


async def test_ttl_zero_returns_miss(backend: DiskCacheBackend) -> None:
    """TTL upper bound is honored even before eviction runs."""
    await backend.set("k", "v", ttl_s=0)
    # Allow time to advance past the zero-TTL boundary.
    await asyncio.sleep(0.01)
    assert await backend.get("k") is None


async def test_ttl_default_applied_when_not_overridden(cache_root: Path) -> None:
    backend = DiskCacheBackend(
        root=cache_root,
        namespace="ns",
        version=1,
        ttl_default_s=0,  # immediate expiry
        size_limit_bytes=1024 * 1024,
    )
    await backend.set("k", "v")
    await asyncio.sleep(0.01)
    assert await backend.get("k") is None


async def test_get_rejects_synthetic_expired_envelope(backend: DiskCacheBackend) -> None:
    """Spec 09 §Invariants: serve expired entries as misses even if the
    underlying diskcache hasn't culled them yet."""
    # Inject an envelope whose expires_at is in the past, bypassing set()'s
    # ttl recalculation.
    backend._cache.set(  # type: ignore[union-attr]
        "k",
        {"version": 1, "value": "v", "expires_at": time.time() - 1},
        expire=3600,
    )
    assert await backend.get("k") is None


# ---------- file permissions ----------


def test_namespace_directory_is_0700(cache_root: Path) -> None:
    DiskCacheBackend(
        root=cache_root,
        namespace="perms-test",
        version=1,
        ttl_default_s=60,
        size_limit_bytes=1024 * 1024,
    )
    mode = stat.S_IMODE((cache_root / "perms-test").stat().st_mode)
    assert mode == 0o700, f"expected 0o700, got {oct(mode)}"


# ---------- best-effort: errors converted to miss / silent skip ----------


async def test_get_returns_none_when_backend_raises(backend: DiskCacheBackend) -> None:
    with patch.object(backend, "_sync_get", side_effect=RuntimeError("disk failure")):
        assert await backend.get("k") is None


async def test_set_swallows_backend_errors(backend: DiskCacheBackend) -> None:
    with patch.object(backend, "_sync_set", side_effect=RuntimeError("disk full")):
        # Must not raise.
        await backend.set("k", "v")
    # Cache continues to work after the failure.
    await backend.set("k2", "v2")
    assert await backend.get("k2") == "v2"


async def test_set_drops_on_write_timeout(backend: DiskCacheBackend) -> None:
    def slow_set(*_args: object, **_kwargs: object) -> None:
        time.sleep(0.5)  # >> 0.1s write timeout

    with patch.object(backend, "_sync_set", side_effect=slow_set):
        # Should return quickly, not block 500ms.
        start = time.monotonic()
        await backend.set("k", "v")
        elapsed = time.monotonic() - start
    assert elapsed < 0.3, f"write did not honor 100ms timeout: {elapsed:.3f}s"
    # Entry not actually written.
    assert await backend.get("k") is None


async def test_delete_swallows_backend_errors(backend: DiskCacheBackend) -> None:
    with patch.object(backend, "_sync_delete", side_effect=RuntimeError("disk")):
        await backend.delete("k")  # must not raise


# ---------- disabled-mode ----------


def test_backend_disabled_when_root_cannot_be_created(tmp_path: Path) -> None:
    # Force diskcache.Cache to raise to simulate disk failure.
    with patch("nexus.cache.diskcache_backend.diskcache.Cache", side_effect=OSError("readonly")):
        backend = DiskCacheBackend(
            root=tmp_path,
            namespace="ns",
            version=1,
            ttl_default_s=60,
            size_limit_bytes=1024 * 1024,
        )
    assert not backend.enabled


async def test_disabled_backend_get_returns_none(tmp_path: Path) -> None:
    with patch("nexus.cache.diskcache_backend.diskcache.Cache", side_effect=OSError):
        backend = DiskCacheBackend(
            root=tmp_path,
            namespace="ns",
            version=1,
            ttl_default_s=60,
            size_limit_bytes=1024 * 1024,
        )
    assert await backend.get("k") is None


async def test_disabled_backend_set_is_noop(tmp_path: Path) -> None:
    with patch("nexus.cache.diskcache_backend.diskcache.Cache", side_effect=OSError):
        backend = DiskCacheBackend(
            root=tmp_path,
            namespace="ns",
            version=1,
            ttl_default_s=60,
            size_limit_bytes=1024 * 1024,
        )
    await backend.set("k", "v")  # must not raise


async def test_disabled_backend_incr_raises(cache_root: Path) -> None:
    with patch("nexus.cache.diskcache_backend.diskcache.Cache", side_effect=OSError):
        backend = DiskCacheBackend(
            root=cache_root,
            namespace="ns",
            version=1,
            ttl_default_s=60,
            size_limit_bytes=1024 * 1024,
        )
    with pytest.raises(CacheDisabled):
        await backend.incr("counter", delta=1, ttl_s=60)


# ---------- atomic counters (cost.daily) ----------


async def test_incr_creates_counter_on_first_call(backend: DiskCacheBackend) -> None:
    value = await backend.incr("counter", delta=5, ttl_s=60)
    assert value == 5


async def test_incr_accumulates(backend: DiskCacheBackend) -> None:
    await backend.incr("counter", delta=2, ttl_s=60)
    await backend.incr("counter", delta=3, ttl_s=60)
    value = await backend.incr("counter", delta=1, ttl_s=60)
    assert value == 6


async def test_incr_is_atomic_under_concurrent_calls(backend: DiskCacheBackend) -> None:
    tasks = [backend.incr("counter", delta=1, ttl_s=60) for _ in range(50)]
    await asyncio.gather(*tasks)
    final = await backend.incr("counter", delta=0, ttl_s=60)
    assert final == 50


async def test_incr_resets_envelope_on_version_drift(cache_root: Path) -> None:
    """If a stale v=N envelope sits in the cache and v=N+1 reader increments,
    the counter restarts from delta — not from the stale value."""
    writer = DiskCacheBackend(
        root=cache_root,
        namespace="ns",
        version=1,
        ttl_default_s=60,
        size_limit_bytes=1024 * 1024,
    )
    await writer.incr("c", delta=99, ttl_s=60)
    writer.close()

    reader = DiskCacheBackend(
        root=cache_root,
        namespace="ns",
        version=2,
        ttl_default_s=60,
        size_limit_bytes=1024 * 1024,
    )
    value = await reader.incr("c", delta=1, ttl_s=60)
    assert value == 1


# ---------- non-blocking event loop ----------


async def test_set_does_not_block_event_loop(backend: DiskCacheBackend) -> None:
    # Schedule a 10ms sleep + a write at the same time; the write must not
    # serialise the sleep (i.e., total time ≈ max, not sum).
    start = time.monotonic()
    await asyncio.gather(
        asyncio.sleep(0.05),
        backend.set("k", "v"),
    )
    elapsed = time.monotonic() - start
    assert elapsed < 0.15, f"set serialised against asyncio.sleep: {elapsed:.3f}s"


# ---------- close ----------


def test_close_idempotent(backend: DiskCacheBackend) -> None:
    backend.close()
    backend.close()  # must not raise


async def test_get_after_close_returns_none(backend: DiskCacheBackend) -> None:
    backend.close()
    assert await backend.get("k") is None


# ---------- diskcache integration smoke ----------


def test_diskcache_dependency_available() -> None:
    """Trip-wire: if diskcache is removed from pyproject, this fails first."""
    assert hasattr(diskcache, "Cache")
