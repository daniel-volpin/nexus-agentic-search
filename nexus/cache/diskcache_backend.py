"""Async diskcache wrapper.

Guarantees:

- Schema-version validation on read: mismatch ⇒ miss (no migration).
- Write timeout: writes that don't complete within
  :data:`_DEFAULT_WRITE_TIMEOUT_S` are dropped (do NOT block the request
  path). Logged at WARN, not raised.
- TTL upper bound respected even before eviction: ``get`` returns ``None``
  for any envelope past its ``expires_at``, regardless of LRU state.
- Disk full / corruption / any backend error: converted to miss /
  silently-dropped write. The cache MUST NOT fail user requests.
- ``set`` never persists secrets — values are stored as-is; the contract
  is that callers don't pass secrets in.

Concurrency: diskcache uses SQLite file locks, so multi-task and
multi-process callers serialize at the storage layer. The wrapper
defers all I/O to ``asyncio.to_thread`` so the event loop does not
block on it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Final

import diskcache

from .types import CacheDisabled

logger = logging.getLogger(__name__)

_DEFAULT_WRITE_TIMEOUT_S: Final[float] = 0.1
_DEFAULT_CULL_LIMIT: Final[int] = 10


class DiskCacheBackend:
    """Per-namespace disk cache.

    One instance per logical namespace. Each instance
    owns its own SQLite file inside ``root/<namespace>/`` with mode 0700 on
    the directory.
    """

    def __init__(
        self,
        *,
        root: Path,
        namespace: str,
        version: int,
        ttl_default_s: int,
        size_limit_bytes: int,
        write_timeout_s: float = _DEFAULT_WRITE_TIMEOUT_S,
    ) -> None:
        self._namespace = namespace
        self._version = version
        self._ttl_default_s = ttl_default_s
        self._write_timeout_s = write_timeout_s

        path = root / namespace
        cache: diskcache.Cache | None
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(path, 0o700)
            # JSONDisk (not the default pickle Disk) closes CVE-2025-69872:
            # diskcache's pickle serializer lets anyone with write access to
            # the cache dir achieve RCE on read. We only ever store
            # JSON-serializable envelopes, so JSON serialization is a clean
            # drop-in with no pickle deserialization anywhere.
            cache = diskcache.Cache(
                str(path),
                disk=diskcache.JSONDisk,
                disk_compress_level=0,
                size_limit=size_limit_bytes,
                cull_limit=_DEFAULT_CULL_LIMIT,
            )
        except Exception as exc:
            logger.warning(
                "cache_disabled",
                extra={"namespace": namespace, "reason": str(exc)},
            )
            cache = None

        self._cache = cache

    # ---------- public ----------

    @property
    def enabled(self) -> bool:
        return self._cache is not None

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def version(self) -> int:
        return self._version

    async def get(self, key: str) -> Any | None:
        """Return the stored value or ``None`` on miss / version drift /
        expiry / backend error."""
        if self._cache is None:
            return None
        try:
            envelope = await asyncio.to_thread(self._sync_get, key)
        except Exception as exc:
            logger.warning(
                "cache_get_error",
                extra={"namespace": self._namespace, "reason": str(exc)},
            )
            return None
        if envelope is None:
            return None
        if envelope.get("version") != self._version:
            return None
        if envelope.get("expires_at", float("inf")) <= time.time():
            return None
        return envelope.get("value")

    async def set(self, key: str, value: Any, *, ttl_s: int | None = None) -> None:
        """Best-effort write. Drops on timeout / backend error / disabled
        cache. Never raises to the caller."""
        if self._cache is None:
            return
        ttl = ttl_s if ttl_s is not None else self._ttl_default_s
        envelope = {
            "version": self._version,
            "value": value,
            "expires_at": time.time() + ttl,
        }
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._sync_set, key, envelope, ttl),
                timeout=self._write_timeout_s,
            )
        except TimeoutError:
            logger.warning(
                "cache_set_timeout",
                extra={"namespace": self._namespace, "timeout_s": self._write_timeout_s},
            )
        except Exception as exc:
            logger.warning(
                "cache_set_error",
                extra={"namespace": self._namespace, "reason": str(exc)},
            )

    async def delete(self, key: str) -> None:
        """Best-effort delete. Errors logged and swallowed."""
        if self._cache is None:
            return
        try:
            await asyncio.to_thread(self._sync_delete, key)
        except Exception as exc:
            logger.warning(
                "cache_delete_error",
                extra={"namespace": self._namespace, "reason": str(exc)},
            )

    async def incr(self, key: str, *, delta: int, ttl_s: int) -> int:
        """Atomically increment a numeric counter. Used by ``cost.daily``.

        Unlike ``get``/``set`` this is NOT best-effort: callers rely on the
        counter staying consistent, so any backend failure raises
        ``CacheDisabled`` rather than silently dropping the increment.
        """
        if self._cache is None:
            raise CacheDisabled(f"cache {self._namespace} is disabled")
        return await asyncio.to_thread(self._sync_incr, key, delta, ttl_s)

    def close(self) -> None:
        if self._cache is not None:
            self._cache.close()
            self._cache = None

    # ---------- synchronous internals (always called via to_thread) ----------

    def _sync_get(self, key: str) -> dict[str, Any] | None:
        assert self._cache is not None
        result = self._cache.get(key)
        if result is None or isinstance(result, dict):
            return result
        # Defensive: an unexpected non-dict value means a schema/version
        # mismatch we cannot interpret. Treat as miss.
        return None

    def _sync_set(self, key: str, envelope: dict[str, Any], ttl_s: int) -> None:
        assert self._cache is not None
        self._cache.set(key, envelope, expire=ttl_s)

    def _sync_delete(self, key: str) -> None:
        assert self._cache is not None
        self._cache.delete(key)

    def _sync_incr(self, key: str, delta: int, ttl_s: int) -> int:
        assert self._cache is not None
        with self._cache.transact():
            envelope = self._cache.get(key)
            if not isinstance(envelope, dict) or envelope.get("version") != self._version:
                envelope = {
                    "version": self._version,
                    "value": 0,
                    "expires_at": time.time() + ttl_s,
                }
            new_value = int(envelope["value"]) + delta
            envelope["value"] = new_value
            envelope["expires_at"] = time.time() + ttl_s
            self._cache.set(key, envelope, expire=ttl_s)
            return new_value
