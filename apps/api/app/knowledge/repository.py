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

from sqlalchemy import column, delete, func, insert, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.knowledge.domain import Chunk, InputType
from app.knowledge.models import (
    EMBEDDING_DIMENSIONS,
    Conversation,
    DocumentChunk,
    EmbeddingCacheEntry,
    LlmCall,
    Query,
    UserMemory,
    UserMemorySettings,
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


def delete_queries_before(session: Session, cutoff: dt.datetime) -> int:
    """Delete the current school's questions asked before ``cutoff`` (retention; RLS and an
    explicit tenant filter). Returns the number of rows deleted."""
    result = session.execute(
        delete(Query).where(
            Query.tenant_id == current_tenant_id(session), Query.created_at < cutoff
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


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


# --- conversations (kb.conversations; 0038, ADR-0033) ------------------------------------------

LIVE_STATUSES = (*EARLIER_STATUSES, "error", "cancelled", "streaming")


@dataclass(frozen=True, slots=True)
class ConversationRow:
    conversation: Conversation
    message_count: int


def _message_count() -> Any:
    return (
        select(func.count())
        .select_from(Query)
        .where(
            Query.conversation_id == Conversation.id,
            Query.user_id == Conversation.user_id,
            Query.superseded_by.is_(None),
        )
        .correlate(Conversation)
        .scalar_subquery()
    )


def insert_conversation(session: Session, values: Mapping[str, Any]) -> Conversation:
    row = session.execute(
        insert(Conversation)
        .values(tenant_id=current_tenant_id(session), **values)
        .returning(Conversation)
    ).scalar_one()
    return row


def conversation_owner(session: Session, conversation_id: uuid.UUID) -> uuid.UUID | None:
    """Who a conversation of this school belongs to (None: no such conversation). Used only to
    tell a legacy ``session_id`` of another user apart; never returned to a caller."""
    value: uuid.UUID | None = session.execute(
        select(Conversation.user_id).where(Conversation.id == conversation_id)
    ).scalar_one_or_none()
    return value


def get_conversation(
    session: Session,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    for_update: bool = False,
    include_deleted: bool = False,
) -> Conversation | None:
    """One of ``user_id``'s conversations (None for another user's, another school's or, unless
    ``include_deleted``, a deleted one: the caller answers 404)."""
    stmt = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.user_id == user_id
    )
    if not include_deleted:
        stmt = stmt.where(Conversation.deleted_at.is_(None))
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def conversation_with_count(
    session: Session, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> ConversationRow | None:
    row = session.execute(
        select(Conversation, _message_count()).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.deleted_at.is_(None),
        )
    ).first()
    return ConversationRow(row[0], int(row[1])) if row is not None else None


def list_conversations(
    session: Session,
    user_id: uuid.UUID,
    *,
    limit: int,
    after: tuple[bool, dt.datetime, uuid.UUID] | None,
) -> list[ConversationRow]:
    """``user_id``'s conversations, pinned first, then newest activity first (keyset on
    ``(pinned, updated_at, id)``, all descending)."""
    stmt = select(Conversation, _message_count()).where(
        Conversation.user_id == user_id, Conversation.deleted_at.is_(None)
    )
    if after is not None:
        stmt = stmt.where(
            tuple_(Conversation.pinned, Conversation.updated_at, Conversation.id) < tuple_(*after)
        )
    stmt = stmt.order_by(
        Conversation.pinned.desc(), Conversation.updated_at.desc(), Conversation.id.desc()
    ).limit(limit)
    return [ConversationRow(r[0], int(r[1])) for r in session.execute(stmt).all()]


def recent_conversations(session: Session, user_id: uuid.UUID, limit: int) -> list[Conversation]:
    """``user_id``'s newest (by activity) non-deleted conversations (chat search)."""
    return list(
        session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id, Conversation.deleted_at.is_(None))
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(limit)
        ).scalars()
    )


def update_conversation(
    session: Session, conversation_id: uuid.UUID, values: Mapping[str, Any]
) -> Conversation | None:
    return session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(**values)
        .returning(Conversation)
    ).scalar_one_or_none()


def conversation_messages(
    session: Session, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> list[Query]:
    """Every question of the conversation asked by ``user_id``, oldest first (superseded too)."""
    return list(
        session.execute(
            select(Query)
            .where(Query.conversation_id == conversation_id, Query.user_id == user_id)
            .order_by(Query.created_at, Query.id)
        ).scalars()
    )


def thread_messages(
    session: Session, conversation_ids: Sequence[uuid.UUID], user_id: uuid.UUID
) -> list[Query]:
    """The current (not superseded) questions of these conversations, oldest first."""
    if not conversation_ids:
        return []
    return list(
        session.execute(
            select(Query)
            .where(
                Query.conversation_id.in_(list(conversation_ids)),
                Query.user_id == user_id,
                Query.superseded_by.is_(None),
            )
            .order_by(Query.created_at, Query.id)
        ).scalars()
    )


def completed_turns(
    session: Session, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> list[Query]:
    """The conversation's current, completed turns (context), oldest first."""
    return list(
        session.execute(
            select(Query)
            .where(
                Query.conversation_id == conversation_id,
                Query.user_id == user_id,
                Query.superseded_by.is_(None),
                Query.status.in_(EARLIER_STATUSES),
            )
            .order_by(Query.created_at, Query.id)
        ).scalars()
    )


def supersede(session: Session, query_ids: Sequence[uuid.UUID], by: uuid.UUID) -> int:
    if not query_ids:
        return 0
    result = session.execute(
        update(Query)
        .where(Query.id.in_(list(query_ids)), Query.superseded_by.is_(None))
        .values(superseded_by=by)
    )
    return _rowcount(result)


def orphan_sessions(session: Session, limit: int) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """``(session_id, user_id)`` groups of questions asked before conversations existed (no
    ``conversation_id``) whose session id names no conversation yet, oldest first."""
    rows = session.execute(
        select(Query.session_id, Query.user_id)
        .where(
            Query.conversation_id.is_(None),
            ~select(Conversation.id).where(Conversation.id == Query.session_id).exists(),
        )
        .group_by(Query.session_id, Query.user_id)
        .order_by(func.min(Query.created_at))
        .limit(limit)
    ).all()
    return [(r.session_id, r.user_id) for r in rows]


def session_questions(session: Session, session_id: uuid.UUID, user_id: uuid.UUID) -> list[Query]:
    """Questions of a legacy session (no conversation yet) of ``user_id``, oldest first."""
    return list(
        session.execute(
            select(Query)
            .where(
                Query.session_id == session_id,
                Query.user_id == user_id,
                Query.conversation_id.is_(None),
            )
            .order_by(Query.created_at, Query.id)
        ).scalars()
    )


def link_session(session: Session, session_id: uuid.UUID, user_id: uuid.UUID) -> int:
    """Put a legacy session's questions into the conversation with the same id."""
    result = session.execute(
        update(Query)
        .where(
            Query.session_id == session_id,
            Query.user_id == user_id,
            Query.conversation_id.is_(None),
        )
        .values(conversation_id=session_id)
    )
    return _rowcount(result)


def delete_empty_conversations(session: Session) -> int:
    """Conversations with no question left (the query purge took them all), this school."""
    result = session.execute(
        delete(Conversation).where(
            Conversation.tenant_id == current_tenant_id(session),
            ~select(Query.id).where(Query.conversation_id == Conversation.id).exists(),
        )
    )
    return _rowcount(result)


def clear_summaries_before(session: Session, cutoff: dt.datetime) -> int:
    """Forget rolling summaries that cover a question older than ``cutoff`` (the query-log
    retention): they are rebuilt from the questions that are kept."""
    result = session.execute(
        update(Conversation)
        .where(
            Conversation.tenant_id == current_tenant_id(session),
            Conversation.summary_oldest_at < cutoff,
        )
        .values(
            summary_ciphertext=None,
            summary_oldest_at=None,
            summary_through=None,
            summary_sources=[],
        )
    )
    return _rowcount(result)


def query_by_id(session: Session, query_id: uuid.UUID) -> Query | None:
    return session.execute(select(Query).where(Query.id == query_id)).scalar_one_or_none()


# --- answer cache (docs/06 cost and performance design) ------------------------------------------


def cache_candidates(
    session: Session, *, question_hmac: bytes, fingerprint: bytes, since: dt.datetime, limit: int
) -> list[Query]:
    """Earlier answered, documents-only questions with the same HMAC and access fingerprint,
    not invalidated, asked since ``since``, newest first (the caller re-checks every source)."""
    return list(
        session.execute(
            select(Query)
            .where(
                Query.question_hmac == question_hmac,
                Query.access_fingerprint == fingerprint,
                Query.cache_invalidated_at.is_(None),
                Query.created_at >= since,
                Query.status == "answered",
                Query.route == "documents",
                Query.cached_from.is_(None),
                Query.answer_ciphertext.is_not(None),
                Query.citations_ciphertext.is_not(None),
            )
            .order_by(Query.created_at.desc(), Query.id.desc())
            .limit(limit)
        ).scalars()
    )


def invalidate_cache_citing(session: Session, document_id: uuid.UUID) -> int:
    """Cached answers given any page of the document stop being reused (FR-KB-030 mechanism:
    a new version, an ACL change, archive or delete)."""
    prefix = f"{_DOC_SOURCE_PREFIX}{document_id}/"
    retrieved = (
        func.jsonb_array_elements(Query.retrieved)
        .table_valued(column("value", JSONB))
        .alias("given")
    )
    used = (
        select(1)
        .select_from(retrieved)
        .where(func.starts_with(retrieved.c.value["source"].astext, prefix))
        .exists()
    )
    result = session.execute(
        update(Query)
        .where(
            Query.access_fingerprint.is_not(None),
            Query.cache_invalidated_at.is_(None),
            used,
        )
        .values(cache_invalidated_at=func.now())
    )
    return _rowcount(result)


# --- memory (kb.user_memories, kb.user_memory_settings; ADR-0033) ----------------------------


def _live_memory(now: dt.datetime) -> Any:
    return (UserMemory.expires_at.is_(None)) | (UserMemory.expires_at > now)


def list_memories(session: Session, user_id: uuid.UUID, now: dt.datetime) -> list[UserMemory]:
    """``user_id``'s memory items (active and unexpired pending), newest first."""
    return list(
        session.execute(
            select(UserMemory)
            .where(UserMemory.user_id == user_id, _live_memory(now))
            .order_by(UserMemory.created_at.desc(), UserMemory.id.desc())
        ).scalars()
    )


def active_memories(session: Session, user_id: uuid.UUID) -> list[UserMemory]:
    """``user_id``'s confirmed items, oldest first (a stable prompt block)."""
    return list(
        session.execute(
            select(UserMemory)
            .where(UserMemory.user_id == user_id, UserMemory.status == "active")
            .order_by(UserMemory.created_at, UserMemory.id)
        ).scalars()
    )


def get_memory(
    session: Session,
    memory_id: uuid.UUID,
    user_id: uuid.UUID,
    now: dt.datetime,
    *,
    for_update: bool = False,
) -> UserMemory | None:
    stmt = select(UserMemory).where(
        UserMemory.id == memory_id, UserMemory.user_id == user_id, _live_memory(now)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def count_memories(session: Session, user_id: uuid.UUID, now: dt.datetime) -> int:
    value: int = session.execute(
        select(func.count())
        .select_from(UserMemory)
        .where(UserMemory.user_id == user_id, _live_memory(now))
    ).scalar_one()
    return value


def insert_memory(session: Session, values: Mapping[str, Any]) -> UserMemory:
    row = session.execute(
        insert(UserMemory)
        .values(tenant_id=current_tenant_id(session), **values)
        .returning(UserMemory)
    ).scalar_one()
    return row


def update_memory(
    session: Session, memory_id: uuid.UUID, *, expected_version: int, values: Mapping[str, Any]
) -> UserMemory | None:
    return session.execute(
        update(UserMemory)
        .where(UserMemory.id == memory_id, UserMemory.version == expected_version)
        .values(**values, version=UserMemory.version + 1, updated_at=func.now())
        .returning(UserMemory)
    ).scalar_one_or_none()


def delete_memory(session: Session, memory_id: uuid.UUID) -> int:
    return _rowcount(session.execute(delete(UserMemory).where(UserMemory.id == memory_id)))


def delete_user_memories(session: Session, user_id: uuid.UUID) -> int:
    return _rowcount(session.execute(delete(UserMemory).where(UserMemory.user_id == user_id)))


def delete_expired_memories(session: Session, now: dt.datetime) -> int:
    result = session.execute(
        delete(UserMemory).where(
            UserMemory.tenant_id == current_tenant_id(session),
            UserMemory.expires_at.is_not(None),
            UserMemory.expires_at <= now,
        )
    )
    return _rowcount(result)


def memory_user_ids(session: Session) -> set[uuid.UUID]:
    """Users of this school with memory items or a memory setting."""
    items = session.execute(select(UserMemory.user_id).distinct()).scalars()
    switches = session.execute(select(UserMemorySettings.user_id)).scalars()
    return set(items) | set(switches)


def delete_memory_data_of(session: Session, user_ids: Sequence[uuid.UUID]) -> int:
    """Every memory item and setting of these users in this school."""
    if not user_ids:
        return 0
    wanted = list(user_ids)
    items = _rowcount(session.execute(delete(UserMemory).where(UserMemory.user_id.in_(wanted))))
    session.execute(delete(UserMemorySettings).where(UserMemorySettings.user_id.in_(wanted)))
    return items


def memory_enabled(session: Session, user_id: uuid.UUID) -> bool | None:
    """The user's switch (None: never set, which means on)."""
    value: bool | None = session.execute(
        select(UserMemorySettings.enabled).where(UserMemorySettings.user_id == user_id)
    ).scalar_one_or_none()
    return value


def set_memory_enabled(session: Session, user_id: uuid.UUID, enabled: bool) -> None:
    stmt = pg_insert(UserMemorySettings).values(
        tenant_id=current_tenant_id(session), user_id=user_id, enabled=enabled
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[UserMemorySettings.tenant_id, UserMemorySettings.user_id],
            set_={"enabled": enabled, "updated_at": func.now()},
        )
    )


def export_memories(session: Session) -> list[UserMemory]:
    """Every memory item of this school (full data export; the caller decrypts)."""
    return list(
        session.execute(select(UserMemory).order_by(UserMemory.user_id, UserMemory.created_at))
        .scalars()
        .all()
    )


def export_memory_settings(session: Session) -> list[UserMemorySettings]:
    return list(
        session.execute(select(UserMemorySettings).order_by(UserMemorySettings.user_id))
        .scalars()
        .all()
    )


def _rowcount(result: object) -> int:
    count = getattr(result, "rowcount", None)
    return int(count) if isinstance(count, int) and count >= 0 else 0
