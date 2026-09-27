"""Embedding caches: the per-tenant document cache contract and the query TTL cache.

- :class:`EmbeddingCache` is what :class:`~app.knowledge.embeddings.embedder.CachingTenantEmbedder`
  needs from a persistent cache of **document** embeddings. Keys are
  ``(tenant_id, model, sha256(text))``: the ``kb.embedding_cache`` primary key (docs/05 §6). The
  tenant is part of every key and the database implementation additionally runs under the
  caller's ``tenant_session`` (RLS), so one school's cache never answers another's (LLM08).
  :class:`InMemoryEmbeddingCache` is the reference implementation for tests and local tools; the
  ``kb.embedding_cache`` repository (retrieval package) implements the same Protocol.
- :class:`QueryEmbeddingCache` keeps **query** embeddings in process memory for
  ``query_cache_ttl_s`` (docs/06 §12), bounded by ``query_cache_max_entries``. Queries are not
  written to the database: they are user questions, and providers embed them differently
  (``input_type``).

Neither cache stores text, only digests and vectors.
"""

from __future__ import annotations

import threading
import uuid
from array import array
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@runtime_checkable
class EmbeddingCache(Protocol):
    """Persistent per-tenant cache of document embeddings keyed by ``sha256(text)``."""

    def get_many(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        model: str,
        digests: Sequence[bytes],
    ) -> Mapping[bytes, Sequence[float]]:
        """The cached vectors among ``digests`` (32-byte sha256); missing digests are absent."""
        ...

    def put_many(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        model: str,
        vectors: Mapping[bytes, Sequence[float]],
    ) -> None:
        """Store vectors in the caller's transaction; an existing key is left unchanged."""
        ...


class InMemoryEmbeddingCache:
    """:class:`EmbeddingCache` in a dict (tests, local tools). Ignores ``session``."""

    def __init__(self) -> None:
        self._data: dict[tuple[uuid.UUID, str, bytes], tuple[float, ...]] = {}
        self._lock = threading.Lock()

    def get_many(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        model: str,
        digests: Sequence[bytes],
    ) -> Mapping[bytes, Sequence[float]]:
        del session
        with self._lock:
            return {
                d: self._data[(tenant_id, model, d)]
                for d in digests
                if (tenant_id, model, d) in self._data
            }

    def put_many(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        model: str,
        vectors: Mapping[bytes, Sequence[float]],
    ) -> None:
        del session
        with self._lock:
            for digest, vector in vectors.items():
                self._data.setdefault((tenant_id, model, digest), tuple(vector))

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


class QueryEmbeddingCache:
    """Thread-safe TTL + LRU cache of query vectors keyed by ``(tenant, model, digest)``."""

    def __init__(self, *, ttl_s: float, max_entries: int, clock: Callable[[], float]) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        self._data: OrderedDict[tuple[uuid.UUID, str, bytes], tuple[float, array[float]]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._ttl_s > 0 and self._max_entries > 0

    def get(self, tenant_id: uuid.UUID, model: str, digest: bytes) -> list[float] | None:
        if not self.enabled:
            return None
        key = (tenant_id, model, digest)
        now = self._clock()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, vector = entry
            if expires_at <= now:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return vector.tolist()

    def put(self, tenant_id: uuid.UUID, model: str, digest: bytes, vector: Sequence[float]) -> None:
        if not self.enabled:
            return
        key = (tenant_id, model, digest)
        expires_at = self._clock() + self._ttl_s
        with self._lock:
            self._data[key] = (expires_at, array("d", vector))
            self._data.move_to_end(key)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
