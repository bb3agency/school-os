"""Small key-value store used by authz for the permission snapshot cache, idempotency records
and rate limits (docs/04 §10, docs/09 §2).

- staging/prod: Valkey (``SOS_REDIS_URL``), shared by every API task;
- local/CI/tests: a bounded in-process store with TTLs and an injectable clock.

Every key built by callers starts with ``sos:`` and includes the tenant id where the value is
tenant data (docs/04 §10: never build cache keys without tenant_id).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Protocol

import redis

from app.core.config import Settings, get_settings


class KVUnavailable(RuntimeError):
    """The backing store could not be reached."""


class KVStore(Protocol):
    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes, *, ttl_s: int, nx: bool = False) -> bool: ...

    def delete(self, *keys: str) -> None: ...

    def incr(self, key: str, *, ttl_s: int) -> int: ...


class InMemoryKV:
    """Per-process store for local/CI. Bounded; expired entries are dropped lazily."""

    def __init__(
        self, *, max_entries: int = 100_000, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._data: dict[str, tuple[bytes, float]] = {}
        self._lock = threading.Lock()
        self._max = max_entries
        self.clock = clock

    def _live(self, key: str, now: float) -> tuple[bytes, float] | None:
        item = self._data.get(key)
        if item is None:
            return None
        if item[1] <= now:
            del self._data[key]
            return None
        return item

    def _evict(self, now: float) -> None:
        if len(self._data) < self._max:
            return
        for k in [k for k, (_, exp) in self._data.items() if exp <= now]:
            del self._data[k]
        if len(self._data) >= self._max:
            raise KVUnavailable("in-memory store is full")

    def get(self, key: str) -> bytes | None:
        with self._lock:
            item = self._live(key, self.clock())
            return None if item is None else item[0]

    def set(self, key: str, value: bytes, *, ttl_s: int, nx: bool = False) -> bool:
        with self._lock:
            now = self.clock()
            if nx and self._live(key, now) is not None:
                return False
            self._evict(now)
            self._data[key] = (value, now + ttl_s)
            return True

    def delete(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._data.pop(key, None)

    def incr(self, key: str, *, ttl_s: int) -> int:
        with self._lock:
            now = self.clock()
            item = self._live(key, now)
            if item is None:
                self._evict(now)
                self._data[key] = (b"1", now + ttl_s)
                return 1
            count = int(item[0]) + 1
            self._data[key] = (str(count).encode(), item[1])
            return count

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class RedisKV:
    """Valkey-backed store. Every failure surfaces as :class:`KVUnavailable`."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, key: str) -> bytes | None:
        try:
            value = self._client.get(key)
        except redis.RedisError as exc:
            raise KVUnavailable("valkey get failed") from exc
        return None if value is None else bytes(value)

    def set(self, key: str, value: bytes, *, ttl_s: int, nx: bool = False) -> bool:
        try:
            return bool(self._client.set(key, value, ex=ttl_s, nx=nx))
        except redis.RedisError as exc:
            raise KVUnavailable("valkey set failed") from exc

    def delete(self, *keys: str) -> None:
        if not keys:
            return
        try:
            self._client.delete(*keys)
        except redis.RedisError as exc:
            raise KVUnavailable("valkey delete failed") from exc

    def incr(self, key: str, *, ttl_s: int) -> int:
        try:
            pipe = self._client.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl_s, nx=True)
            count, _ = pipe.execute()
        except redis.RedisError as exc:
            raise KVUnavailable("valkey incr failed") from exc
        return int(count)


def build_kv_store(settings: Settings) -> KVStore:
    if settings.is_production_like:
        client = redis.Redis.from_url(
            settings.redis_url.get_secret_value(), socket_timeout=0.5, socket_connect_timeout=0.5
        )
        return RedisKV(client)
    return InMemoryKV()


@lru_cache(maxsize=1)
def get_kv_store() -> KVStore:
    """Process-wide store (tests replace it with :func:`set_kv_store`)."""
    return build_kv_store(get_settings())


_override: list[KVStore] = []


def set_kv_store(store: KVStore | None) -> None:
    """Replace the process-wide store (tests). ``None`` restores the default."""
    _override.clear()
    if store is not None:
        _override.append(store)


def kv_store() -> KVStore:
    return _override[0] if _override else get_kv_store()
