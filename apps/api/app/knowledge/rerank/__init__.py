"""Reranking: provider selection and the offline fake (docs/06 §6; FR-KB-001; PO 2026-09-30).

Responsibility: the :class:`app.knowledge.interfaces.Reranker` implementations that need no
network (:class:`FakeReranker`, deterministic, for local/CI and the offline evals) and
:func:`select_reranker` (``retrieval.yaml`` ``rerank.provider`` + ``SOS_KB_PROVIDER_MODE``).
Network providers live in ``knowledge/gateway`` (the one place with outbound model traffic,
e.g. ``gateway/rerank_voyage.py``) and are injected by the composition root as factories, like
the embeddings providers (ADR-0006 pattern).

Boundary: no SDK, no HTTP, no database. The retrieval package calls a reranker only with
candidates that already passed the caller's ACL predicate in SQL (invariant 8). Must not import
gateway, retrieval, tools, ingestion or service (import-linter ``knowledge-layers``).
"""

from app.knowledge.rerank.fake import FAKE_MODEL, FakeReranker
from app.knowledge.rerank.selection import (
    RerankerFactory,
    RerankerNotConfiguredError,
    select_reranker,
)

__all__ = [
    "FAKE_MODEL",
    "FakeReranker",
    "RerankerFactory",
    "RerankerNotConfiguredError",
    "select_reranker",
]
