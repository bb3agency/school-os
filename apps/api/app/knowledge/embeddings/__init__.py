"""Embeddings: provider interface, batching and the per-tenant cache (ADR-0006, docs/06 §4.6).

Responsibility: implement :class:`app.knowledge.interfaces.TenantEmbedder` on top of any
:class:`app.knowledge.interfaces.EmbeddingsProvider`: batching, retries, rate-limit handling,
the per-tenant cache keyed by ``sha256(text)`` in ``kb.embedding_cache`` (never shared across
tenants, LLM08), the query-embedding cache (``query_cache_ttl_s``) and a deterministic offline
fake provider for local/CI. Provider, model, dimensions and precision come from
``knowledge/config/embeddings.yaml`` and are chosen by evaluation, not preference.

Boundary: no provider SDK here. A network provider is implemented in ``knowledge/gateway`` (the
one place SDKs and outbound model traffic live) and injected by ``knowledge.service``. Must not
import gateway, retrieval, tools, ingestion or service (import-linter ``knowledge-layers``).

As built (K2): :class:`CachingTenantEmbedder` (``embedder``), the :class:`EmbeddingCache`
Protocol with an in-memory implementation and the query TTL cache (``cache``), the offline
:class:`FakeEmbeddingsProvider` (``fake``) and :func:`select_embeddings_provider`
(``selection``: fake mode, or the evaluated selection built by a gateway factory).
"""

from app.knowledge.embeddings.cache import (
    EmbeddingCache,
    InMemoryEmbeddingCache,
    QueryEmbeddingCache,
)
from app.knowledge.embeddings.embedder import (
    CachingTenantEmbedder,
    EmbeddingDimensionError,
    batches,
    content_sha256,
    is_transient,
)
from app.knowledge.embeddings.fake import FakeEmbeddingsProvider
from app.knowledge.embeddings.selection import (
    EmbeddingsNotConfiguredError,
    ProviderFactory,
    select_embeddings_provider,
)

__all__ = [
    "CachingTenantEmbedder",
    "EmbeddingCache",
    "EmbeddingDimensionError",
    "EmbeddingsNotConfiguredError",
    "FakeEmbeddingsProvider",
    "InMemoryEmbeddingCache",
    "ProviderFactory",
    "QueryEmbeddingCache",
    "batches",
    "content_sha256",
    "is_transient",
    "select_embeddings_provider",
]
