"""Database access for the knowledge tables (0021_kb_tables; docs/05 §6, docs/06 §4.7-4.8).

Callers inside ``app.knowledge`` only (ingestion through the composition root, the embeddings
cache, the service); other modules use ``app.knowledge.service``. Every function takes a
``core.db.tenant_session()`` (or a worker job's tenant session): RLS limits each statement to
that tenant and ``tenant_id`` is taken from the session's context, never from a caller.

Writing the index (the ``ChunkStore`` functions ingestion calls, docs/06 §4.7):

1. :func:`replace_version_chunks`: delete the version's chunks and insert the new ones with
   ``is_latest = false`` (idempotent: a re-run replaces, never duplicates). Nothing is visible
   to retrieval yet.
2. :func:`promote_version`: one UPDATE flips the document to that version (its chunks
   ``is_latest = true``, every other version's ``false``), in the same transaction that marks
   the version ``ready``.
3. :func:`refresh_acl`: rewrite the denormalised ACL copies (and optionally the document
   facets) on all chunks of a document when its ACL or metadata changes (docs/05 §6).
4. :func:`delete_document_chunks` / :func:`delete_version_chunks`: removal (deleting the
   ``kb.documents`` / ``kb.document_versions`` row also cascades). :func:`demote_document`
   hides a document (e.g. archived) without deleting its chunks.

ACL copies must be built exactly like the documents service's ``kb.document_acl`` rows: role
keys, section/class/membership UUIDs. An empty :class:`ChunkAcl` means "school-wide readers
only" at query time (fail closed), never public.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import column, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.knowledge.domain import Chunk, InputType
from app.knowledge.models import (
    EMBEDDING_DIMENSIONS,
    DocumentChunk,
    EmbeddingCacheEntry,
    LlmCall,
    Query,
    VerifiedAnswer,
)

_DOC_SOURCE_PREFIX = "sos://doc/"


@dataclass(frozen=True, slots=True)
class ChunkAcl:
    """Denormalised copy of a document's ``kb.document_acl`` rows (docs/05 §6)."""

    roles: frozenset[str] = frozenset()
    sections: frozenset[uuid.UUID] = frozenset()
    classes: frozenset[uuid.UUID] = frozenset()
    memberships: frozenset[uuid.UUID] = frozenset()

    def columns(self) -> dict[str, list[Any]]:
        return {
            "acl_roles": sorted(self.roles),
            "acl_sections": sorted(self.sections, key=str),
            "acl_classes": sorted(self.classes, key=str),
            "acl_memberships": sorted(self.memberships, key=str),
        }


@dataclass(frozen=True, slots=True)
class DocumentFacets:
    """Denormalised document metadata used by retrieval filters (docs/06 §6)."""

    doc_type: str
    sensitivity: str
    issued_on: dt.date | None = None
    academic_year_id: uuid.UUID | None = None

    def columns(self) -> dict[str, Any]:
        return {
            "doc_type": self.doc_type,
            "sensitivity": self.sensitivity,
            "issued_on": self.issued_on,
            "academic_year_id": self.academic_year_id,
        }


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    """A chunk from the chunker plus its document embedding (made from header + content)."""

    chunk: Chunk
    embedding: Sequence[float]


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set; use core.db.tenant_session()")
    return uuid.UUID(str(value))


# --- chunks (ingestion) --------------------------------------------------------------------


def replace_version_chunks(
    session: Session,
    *,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    chunks: Sequence[EmbeddedChunk],
    embedding_model: str,
    facets: DocumentFacets,
    acl: ChunkAcl,
) -> int:
    """Replace the version's chunks; the new ones are hidden until :func:`promote_version`.

    Returns the number of chunks written. Raises ``ValueError`` for duplicate chunk numbers or
    an embedding of the wrong dimension; the database refuses a version of another document or
    another school (composite FK).
    """
    numbers = [c.chunk.chunk_no for c in chunks]
    if len(set(numbers)) != len(numbers):
        raise ValueError("chunk numbers must be unique within a version")
    for c in chunks:
        if len(c.embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(f"embeddings must have {EMBEDDING_DIMENSIONS} dimensions")
    delete_version_chunks(session, version_id)
    if not chunks:
        return 0
    tenant_id = current_tenant_id(session)
    shared = {**facets.columns(), **acl.columns()}
    rows = [
        {
            "id": new_id(),
            "tenant_id": tenant_id,
            "document_id": document_id,
            "version_id": version_id,
            "chunk_no": c.chunk.chunk_no,
            "page_from": c.chunk.page_from,
            "page_to": c.chunk.page_to,
            "heading_path": list(c.chunk.heading_path),
            "context_header": c.chunk.context_header,
            "content": c.chunk.content,
            "language": c.chunk.language,
            "token_count": c.chunk.token_count,
            "is_table": c.chunk.is_table,
            "embedding": list(c.embedding),
            "embedding_model": embedding_model,
            "is_latest": False,
            **shared,
        }
        for c in chunks
    ]
    session.execute(insert(DocumentChunk), rows)
    return len(rows)


def promote_version(session: Session, *, document_id: uuid.UUID, version_id: uuid.UUID) -> int:
    """Make ``version_id`` the document's searchable version in one statement.

    Returns the number of chunks of that version now visible (0 if it has none).
    """
    target = DocumentChunk.version_id == version_id
    session.execute(
        update(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .where(DocumentChunk.is_latest.is_distinct_from(target))
        .values(is_latest=target)
    )
    visible: int = session.execute(
        select(func.count())
        .select_from(DocumentChunk)
        .where(DocumentChunk.document_id == document_id, target, DocumentChunk.is_latest)
    ).scalar_one()
    return visible


def demote_document(session: Session, document_id: uuid.UUID) -> int:
    """Hide every chunk of the document from retrieval (kept for version history)."""
    result = session.execute(
        update(DocumentChunk)
        .where(DocumentChunk.document_id == document_id, DocumentChunk.is_latest)
        .values(is_latest=False)
    )
    return _rowcount(result)


def refresh_acl(
    session: Session,
    *,
    document_id: uuid.UUID,
    acl: ChunkAcl,
    facets: DocumentFacets | None = None,
) -> int:
    """Rewrite the ACL copies (and facets, when given) on all chunks of the document."""
    values: dict[str, Any] = acl.columns()
    if facets is not None:
        values.update(facets.columns())
    result = session.execute(
        update(DocumentChunk).where(DocumentChunk.document_id == document_id).values(**values)
    )
    return _rowcount(result)


def delete_version_chunks(session: Session, version_id: uuid.UUID) -> int:
    result = session.execute(delete(DocumentChunk).where(DocumentChunk.version_id == version_id))
    return _rowcount(result)


def delete_document_chunks(session: Session, document_id: uuid.UUID) -> int:
    result = session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    return _rowcount(result)


def flag_verified_answers_citing(session: Session, document_id: uuid.UUID) -> int:
    """Active verified answers citing any page of the document become ``needs_review``
    (docs/06 §4.8). Returns how many were flagged."""
    prefix = f"{_DOC_SOURCE_PREFIX}{document_id}/"
    elements = (
        func.jsonb_array_elements(VerifiedAnswer.citations)
        .table_valued(column("value", JSONB))
        .alias("citation")
    )
    cites = (
        select(1)
        .select_from(elements)
        .where(func.starts_with(elements.c.value["source"].astext, prefix))
        .exists()
    )
    result = session.execute(
        update(VerifiedAnswer)
        .where(VerifiedAnswer.status == "active", cites)
        .values(status="needs_review", version=VerifiedAnswer.version + 1)
    )
    return _rowcount(result)


# --- embedding cache (ADR-0006: per tenant, keyed by sha256(text)) ----------------------------


def get_cached_embeddings(
    session: Session,
    *,
    model: str,
    input_type: InputType,
    digests: Iterable[bytes],
    newer_than: dt.datetime | None = None,
) -> dict[bytes, list[float]]:
    """Cached vectors for the given SHA-256 digests (missing ones are absent from the result)."""
    wanted = sorted(set(digests))
    if not wanted:
        return {}
    stmt = select(EmbeddingCacheEntry.content_sha256, EmbeddingCacheEntry.embedding).where(
        EmbeddingCacheEntry.model == model,
        EmbeddingCacheEntry.input_type == input_type,
        EmbeddingCacheEntry.content_sha256.in_(wanted),
    )
    if newer_than is not None:
        stmt = stmt.where(EmbeddingCacheEntry.created_at > newer_than)
    return {bytes(d): list(v) for d, v in session.execute(stmt).all()}


def put_cached_embeddings(
    session: Session,
    *,
    model: str,
    input_type: InputType,
    entries: Mapping[bytes, Sequence[float]],
) -> int:
    """Insert vectors by digest; existing entries are kept (same text, same model)."""
    if not entries:
        return 0
    tenant_id = current_tenant_id(session)
    rows = []
    for digest, vector in sorted(entries.items()):
        if len(digest) != 32:
            raise ValueError("cache keys are SHA-256 digests (32 bytes)")
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise ValueError(f"embeddings must have {EMBEDDING_DIMENSIONS} dimensions")
        rows.append(
            {
                "tenant_id": tenant_id,
                "model": model,
                "input_type": input_type,
                "content_sha256": digest,
                "embedding": list(vector),
            }
        )
    stmt = (
        pg_insert(EmbeddingCacheEntry)
        .values(rows)
        .on_conflict_do_nothing()
        .returning(EmbeddingCacheEntry.content_sha256)
    )
    return len(session.execute(stmt).all())


def purge_embedding_cache(
    session: Session, *, input_type: InputType, older_than: dt.datetime
) -> int:
    """Delete cache entries created before ``older_than`` (query vectors: docs/06 §12 TTL)."""
    result = session.execute(
        delete(EmbeddingCacheEntry).where(
            EmbeddingCacheEntry.input_type == input_type,
            EmbeddingCacheEntry.created_at < older_than,
        )
    )
    return _rowcount(result)


def latest_version_of(session: Session, document_id: uuid.UUID) -> uuid.UUID | None:
    """The version whose chunks are searchable now (None when the document has none)."""
    value: uuid.UUID | None = session.execute(
        select(DocumentChunk.version_id)
        .where(DocumentChunk.document_id == document_id, DocumentChunk.is_latest)
        .limit(1)
    ).scalar_one_or_none()
    return value


def has_version_chunks(session: Session, version_id: uuid.UUID) -> bool:
    return (
        session.execute(
            select(DocumentChunk.id).where(DocumentChunk.version_id == version_id).limit(1)
        ).first()
        is not None
    )


def latest_page_texts(session: Session, version_id: uuid.UUID, page: int) -> list[str]:
    """Content of the searchable chunks of ``version_id`` covering ``page`` (page 1 also
    matches chunks without page numbers). Callers check the document's visibility first."""
    covers = (DocumentChunk.page_from <= page) & (
        func.coalesce(DocumentChunk.page_to, DocumentChunk.page_from) >= page
    )
    if page == 1:
        covers = covers | DocumentChunk.page_from.is_(None)
    rows = session.execute(
        select(DocumentChunk.content)
        .where(DocumentChunk.version_id == version_id, DocumentChunk.is_latest, covers)
        .order_by(DocumentChunk.chunk_no)
    ).scalars()
    return list(rows)


@dataclass(frozen=True, slots=True)
class VersionChunk:
    """One indexed chunk of a version, in reading order (circular reading, M4)."""

    chunk_no: int
    page_from: int | None
    content: str


def version_chunks(
    session: Session, document_id: uuid.UUID, version_id: uuid.UUID
) -> list[VersionChunk]:
    """Every chunk of one version of a document, in chunk order (already Aadhaar-masked at
    ingestion). Callers decide who may see the result (workers, or after a visibility check)."""
    rows = session.execute(
        select(DocumentChunk.chunk_no, DocumentChunk.page_from, DocumentChunk.content)
        .where(DocumentChunk.document_id == document_id, DocumentChunk.version_id == version_id)
        .order_by(DocumentChunk.chunk_no)
    ).all()
    return [VersionChunk(r.chunk_no, r.page_from, r.content) for r in rows]


# --- query log (kb.queries; FR-KB-009) ----------------------------------------------------------


def insert_query(session: Session, values: Mapping[str, Any]) -> None:
    """One asked question: ciphertext + HMAC, ids, codes and counts only (invariant 5)."""
    session.execute(insert(Query).values(tenant_id=current_tenant_id(session), **values))


def update_query(session: Session, query_id: uuid.UUID, values: Mapping[str, Any]) -> None:
    """Complete (or cancel) a streamed question's row: ciphertext, codes and counts only."""
    session.execute(update(Query).where(Query.id == query_id).values(**values))


EARLIER_STATUSES = ("answered", "not_found", "refused", "search_only")
"""Questions that count as conversation context (a cancelled or failed one does not)."""


def earlier_questions(
    session: Session,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    since: dt.datetime,
    limit: int,
) -> list[tuple[uuid.UUID, bytes]]:
    """The newest ``limit`` completed questions of ONE user's session since ``since``, newest
    first, as ``(id, question_ciphertext)``. Another user's rows with the same session id are
    never read (FR-KB-012: no cross-user memory; the school is RLS)."""
    if limit < 1:
        return []
    rows = session.execute(
        select(Query.id, Query.question_ciphertext)
        .where(
            Query.user_id == user_id,
            Query.session_id == session_id,
            Query.created_at >= since,
            Query.status.in_(EARLIER_STATUSES),
        )
        .order_by(Query.created_at.desc(), Query.id.desc())
        .limit(limit)
    ).all()
    return [(r.id, bytes(r.question_ciphertext)) for r in rows]


def get_query_of_user(
    session: Session, query_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
) -> Query | None:
    """A query row of the current school asked by ``user_id`` (None otherwise: 404)."""
    stmt = select(Query).where(Query.id == query_id, Query.user_id == user_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def set_feedback(
    session: Session,
    query_id: uuid.UUID,
    *,
    feedback: str,
    reason: str | None,
    at: dt.datetime,
) -> None:
    session.execute(
        update(Query)
        .where(Query.id == query_id)
        .values(feedback=feedback, feedback_reason=reason, feedback_at=at)
    )


# --- metering ledger (kb.llm_calls; 0024_kb_metering) ----------------------------------------


def insert_llm_call(session: Session, values: Mapping[str, Any]) -> None:
    session.execute(
        insert(LlmCall).values(id=new_id(), tenant_id=current_tenant_id(session), **values)
    )


# --- verified answers (kb.verified_answers; FR-KB-030) ---------------------------------------


def list_verified_answers(
    session: Session, *, status: str | None, limit: int, before_id: uuid.UUID | None
) -> list[VerifiedAnswer]:
    stmt = select(VerifiedAnswer)
    if status is not None:
        stmt = stmt.where(VerifiedAnswer.status == status)
    if before_id is not None:
        stmt = stmt.where(VerifiedAnswer.id < before_id)
    stmt = stmt.order_by(VerifiedAnswer.id.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


def get_verified_answer(
    session: Session, answer_id: uuid.UUID, *, for_update: bool = False
) -> VerifiedAnswer | None:
    stmt = select(VerifiedAnswer).where(VerifiedAnswer.id == answer_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def update_verified_answer(
    session: Session,
    answer_id: uuid.UUID,
    *,
    expected_version: int,
    values: Mapping[str, Any],
) -> VerifiedAnswer | None:
    """Apply ``values`` and bump ``version`` when it is still ``expected_version`` (None if
    not: the caller answers 412)."""
    return session.execute(
        update(VerifiedAnswer)
        .where(VerifiedAnswer.id == answer_id, VerifiedAnswer.version == expected_version)
        .values(**values, version=VerifiedAnswer.version + 1)
        .returning(VerifiedAnswer)
    ).scalar_one_or_none()


def insert_verified_answer(session: Session, values: Mapping[str, Any]) -> VerifiedAnswer:
    row = session.execute(
        insert(VerifiedAnswer)
        .values(tenant_id=current_tenant_id(session), **values)
        .returning(VerifiedAnswer)
    ).scalar_one()
    return row


def _rowcount(result: object) -> int:
    count = getattr(result, "rowcount", None)
    return int(count) if isinstance(count, int) and count >= 0 else 0
