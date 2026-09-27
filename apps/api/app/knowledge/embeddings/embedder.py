"""``TenantEmbedder``: batching, retries and caches around one ``EmbeddingsProvider``.

``embed(session, tenant_id, texts, input_type)`` returns one vector per text, in order:

1. Each text is keyed by ``sha256(utf-8 bytes)``; duplicates in one call are embedded once.
2. ``document`` texts are looked up in the per-tenant :class:`EmbeddingCache`
   (``kb.embedding_cache``); ``query`` texts in the in-process :class:`QueryEmbeddingCache`
   (``query_cache_ttl_s``, docs/06 §12). Keys always include the tenant and the model.
3. Misses are sent to the provider in batches of at most ``batching.max_texts`` texts and
   ``batching.max_chars`` characters (a longer single text goes alone).
4. Transient failures are retried here, and only here, with capped exponential backoff and
   jitter. Transient means: :class:`TimeoutError`, :class:`ConnectionError`, or any exception
   with ``retryable = True`` (network providers in ``knowledge/gateway`` raise such errors,
   optionally with ``retry_after_s``; this package cannot import the gateway). Anything else
   is raised at once.
5. Every vector is checked: count equals the batch, length equals ``storage.dimensions``
   (the ``halfvec`` column), values are finite. A mismatch raises
   :class:`EmbeddingDimensionError` and nothing is cached.

Texts are already Aadhaar-redacted by the caller (invariant 4). Logs carry the tenant ID,
counts, attempt numbers and error types only, never text or digests (invariant 5).
"""

from __future__ import annotations

import hashlib
import math
import random
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.knowledge.config.embeddings import EmbeddingsConfig
from app.knowledge.domain import InputType
from app.knowledge.embeddings.cache import EmbeddingCache, QueryEmbeddingCache
from app.knowledge.interfaces import EmbeddingsProvider

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

_INPUT_TYPES: frozenset[str] = frozenset({"document", "query"})


class EmbeddingDimensionError(ValueError):
    """A provider or cache produced vectors that do not fit ``storage.dimensions``."""


def content_sha256(text: str) -> bytes:
    """The cache key of a text (``kb.embedding_cache.content_sha256``)."""
    return hashlib.sha256(text.encode("utf-8")).digest()


def is_transient(exc: BaseException) -> bool:
    """The retry contract shared with network providers (see the module docstring)."""
    if isinstance(exc, TimeoutError | ConnectionError):
        return True
    return getattr(exc, "retryable", False) is True


def batches(texts: Sequence[str], *, max_texts: int, max_chars: int) -> Iterator[list[str]]:
    """Greedy, order-preserving split by count and total characters."""
    batch: list[str] = []
    chars = 0
    for text in texts:
        if batch and (len(batch) >= max_texts or chars + len(text) > max_chars):
            yield batch
            batch, chars = [], 0
        batch.append(text)
        chars += len(text)
    if batch:
        yield batch


class CachingTenantEmbedder:
    """:class:`app.knowledge.interfaces.TenantEmbedder` (docs/06 §4.6, ADR-0006)."""

    def __init__(
        self,
        provider: EmbeddingsProvider,
        config: EmbeddingsConfig,
        cache: EmbeddingCache,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if provider.dimensions != config.storage.dimensions:
            raise EmbeddingDimensionError(
                f"provider {provider.name!r} makes {provider.dimensions}-dim vectors but "
                f"storage is {config.storage.dimensions}: switching dimensions is a "
                "re-embedding migration (docs/06 §4.6)"
            )
        self._provider = provider
        self._config = config
        self._cache = cache
        self._queries = QueryEmbeddingCache(
            ttl_s=config.query_cache_ttl_s,
            max_entries=config.query_cache_max_entries,
            clock=clock,
        )
        self._sleep = sleep
        self._jitter = jitter

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def dimensions(self) -> int:
        return self._provider.dimensions

    def embed(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        texts: Sequence[str],
        input_type: InputType,
    ) -> list[list[float]]:
        if input_type not in _INPUT_TYPES:
            raise ValueError("input_type must be 'document' or 'query'")
        if not texts:
            return []
        if any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError("texts must be non-blank strings")

        digests = [content_sha256(t) for t in texts]
        unique: dict[bytes, str] = dict(zip(digests, texts, strict=True))
        found = self._lookup(session, tenant_id, input_type, list(unique))
        missing = [d for d in unique if d not in found]

        fresh: dict[bytes, list[float]] = {}
        if missing:
            vectors = self._embed_missing([unique[d] for d in missing], input_type)
            fresh = dict(zip(missing, vectors, strict=True))
            self._store(session, tenant_id, input_type, fresh)
        found.update(fresh)

        log.info(
            "kb.embeddings.embedded",
            tenant_id=str(tenant_id),
            resource_type=input_type,
            count=len(texts),
            outcome=f"cached:{len(unique) - len(missing)},embedded:{len(missing)}",
        )
        return [list(found[d]) for d in digests]

    # --- caches ---------------------------------------------------------------------------

    def _lookup(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        input_type: InputType,
        digests: list[bytes],
    ) -> dict[bytes, list[float]]:
        found: dict[bytes, list[float]] = {}
        if input_type == "query":
            for digest in digests:
                vector = self._queries.get(tenant_id, self.model, digest)
                if vector is not None:
                    found[digest] = vector
        else:
            wanted = set(digests)
            cached = self._cache.get_many(session, tenant_id, self.model, digests)
            found = {d: [float(x) for x in v] for d, v in cached.items() if d in wanted}
        for vector in found.values():
            self._check_vector(vector)
        return found

    def _store(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        input_type: InputType,
        vectors: dict[bytes, list[float]],
    ) -> None:
        if input_type == "query":
            for digest, vector in vectors.items():
                self._queries.put(tenant_id, self.model, digest, vector)
        else:
            self._cache.put_many(session, tenant_id, self.model, vectors)

    # --- provider calls ---------------------------------------------------------------------

    def _embed_missing(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        out: list[list[float]] = []
        shaping = self._config.batching
        for batch in batches(texts, max_texts=shaping.max_texts, max_chars=shaping.max_chars):
            vectors = self._call_with_retries(batch, input_type)
            if len(vectors) != len(batch):
                raise EmbeddingDimensionError(
                    f"provider returned {len(vectors)} vectors for {len(batch)} texts"
                )
            for vector in vectors:
                self._check_vector(vector)
            out.extend([float(x) for x in v] for v in vectors)
        return out

    def _call_with_retries(self, batch: list[str], input_type: InputType) -> list[list[float]]:
        policy = self._config.retry
        attempt = 1
        while True:
            try:
                return self._provider.embed(batch, input_type)
            except Exception as exc:
                if attempt >= policy.max_attempts or not is_transient(exc):
                    log.warning(
                        "kb.embeddings.failed",
                        action=self._provider.name,
                        attempt=attempt,
                        count=len(batch),
                        error_type=type(exc).__name__,
                    )
                    raise
                delay = self._delay(attempt, exc)
                log.warning(
                    "kb.embeddings.retry",
                    action=self._provider.name,
                    attempt=attempt,
                    count=len(batch),
                    duration_ms=round(delay * 1000),
                    error_type=type(exc).__name__,
                )
                self._sleep(delay)
                attempt += 1

    def _delay(self, attempt: int, exc: BaseException) -> float:
        policy = self._config.retry
        backoff = min(policy.max_delay_s, policy.base_delay_s * 2.0 ** (attempt - 1))
        delay: float = backoff * (0.5 + 0.5 * self._jitter())  # "equal jitter"
        retry_after = getattr(exc, "retry_after_s", None)
        if isinstance(retry_after, int | float) and math.isfinite(retry_after):
            delay = max(delay, min(float(retry_after), policy.max_delay_s))
        return delay

    def _check_vector(self, vector: Sequence[float]) -> None:
        expected = self._config.storage.dimensions
        if len(vector) != expected:
            raise EmbeddingDimensionError(
                f"got a {len(vector)}-dim vector from {self._provider.name!r}, storage is "
                f"{expected}"
            )
        if not all(math.isfinite(x) for x in vector):
            raise EmbeddingDimensionError(f"non-finite value from {self._provider.name!r}")
