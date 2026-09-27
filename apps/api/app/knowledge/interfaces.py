"""Cross-package contracts of the knowledge module (K0). Each later package implements one.

- ``EmbeddingsProvider``: ``embeddings`` (offline fake) and ``gateway`` (network providers);
  used by ``embeddings``' ``TenantEmbedder``.
- ``TenantEmbedder``: ``embeddings``; used by ``ingestion`` and ``service``.
- ``Chunker``: ``chunking``; used by ``ingestion``.
- ``Retriever``: ``retrieval``; used by ``tools`` (``search_documents``) and ``service``.
- ``LlmGateway``: ``gateway``; used by ``service`` (ask loop) and ``ingestion`` (metadata).
- ``RecordTool``: ``tools``; used by ``service`` (ask loop).
- ``IngestionPipeline``: ``ingestion``; run by worker tasks.
- ``KnowledgeService``: ``service`` (later); used by routes and other modules.

Implementations are wired together in ``knowledge.service`` (the composition root), so no
package needs to import a sibling it is not allowed to (import-linter ``knowledge-layers``).
Signatures here change only with every implementing package: treat this file as shared.

Sessions are passed in, never opened here: a request's ``tenant_session`` (RLS context set,
invariant 1) or a worker job's. Nothing in these contracts carries a raw Aadhaar number
(invariant 4) or reaches a model except through ``LlmGateway``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.knowledge.domain import (
    AclKeys,
    AskEvent,
    AskRequest,
    Chunk,
    ConversationItem,
    DocumentContext,
    ExtractedDocument,
    InputType,
    Metering,
    ModelRole,
    ModelTurn,
    RankedChunk,
    RetrievalQuery,
    SearchFilters,
    ToolOutcome,
    ToolSpec,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext


@runtime_checkable
class EmbeddingsProvider(Protocol):
    """ADR-0006: ``embed(texts, input_type) -> list[vector]`` for one provider and model."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str:
        """Stored in ``kb.document_chunks.embedding_model`` for every chunk."""
        ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        """One vector per text, in order. Texts are already Aadhaar-redacted."""
        ...


@runtime_checkable
class TenantEmbedder(Protocol):
    """Batching, retries and the per-tenant ``sha256(text)`` cache around a provider."""

    @property
    def model(self) -> str: ...

    def embed(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        texts: Sequence[str],
        input_type: InputType,
    ) -> list[list[float]]: ...


@runtime_checkable
class Chunker(Protocol):
    def chunk(self, document: ExtractedDocument, context: DocumentContext) -> list[Chunk]:
        """Deterministic: the same input always gives the same chunks (idempotent stages)."""
        ...


@runtime_checkable
class Retriever(Protocol):
    def search(self, session: Session, acl: AclKeys, query: RetrievalQuery) -> list[RankedChunk]:
        """Best first, at most ``query.k``; every candidate list filtered by ``acl`` in SQL."""
        ...


@runtime_checkable
class LlmGateway(Protocol):
    def run_turn(
        self,
        metering: Metering,
        role: ModelRole,
        system: str,
        conversation: Sequence[ConversationItem],
        tools: Sequence[ToolSpec],
    ) -> ModelTurn:
        """One model turn: text segments with citations, and/or tool calls to run.

        Raises when the tenant's budget is exhausted or the provider is unavailable; the caller
        degrades to search-only (FR-KB-011, NFR-AVL-004).
        """
        ...

    def generate_json(
        self,
        metering: Metering,
        role: ModelRole,
        system: str,
        text: str,
        schema: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Strict JSON output validated against ``schema`` (metadata, translation, extraction)."""
        ...


@runtime_checkable
class RecordTool(Protocol):
    @property
    def spec(self) -> ToolSpec: ...

    def run(
        self, session: Session, ctx: UserContext, call_id: str, arguments: Mapping[str, object]
    ) -> ToolOutcome:
        """Validate ``arguments``, check ``spec.permission``, read through module services."""
        ...


@runtime_checkable
class IngestionPipeline(Protocol):
    """Reacts to documents' outbox events; each call is an idempotent worker job."""

    def ingest_version(
        self, tenant_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> None:
        """A scanned (``ready``) version: extract, chunk, embed, index, flip ``is_latest``."""
        ...

    def refresh_acl(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """Rewrite the ``acl_*`` copies on the document's chunks (docs/05 §6)."""
        ...

    def remove_document(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """Delete chunks (target <= 5 min) and flag citing verified answers (docs/06 §4.8)."""
        ...


@runtime_checkable
class KnowledgeService(Protocol):
    """What routes and other modules call. Callers have checked ``kb.ask`` (routes) or
    ``document.read`` (search); implementations re-check and audit in the same transaction."""

    def ask(self, session: Session, ctx: UserContext, request: AskRequest) -> Iterator[AskEvent]:
        """Stream the SSE events of docs/06 §5.1 for one question."""
        ...

    def search_documents(
        self,
        session: Session,
        ctx: UserContext,
        query: str,
        *,
        filters: SearchFilters | None = None,
        k: int = 12,
    ) -> list[RankedChunk]:
        """Search-only results (also the fallback mode), filtered by the caller's ACL keys."""
        ...


__all__ = [
    "Chunker",
    "EmbeddingsProvider",
    "IngestionPipeline",
    "KnowledgeService",
    "LlmGateway",
    "RecordTool",
    "Retriever",
    "TenantEmbedder",
]
