"""Database access for documents. Only ``app.documents.service`` calls this module.

Every function takes a ``core.db.tenant_session()``: RLS limits each statement to that tenant.
Object-level visibility (docs/05 §6, docs/07 §6.1 point 3) is a SQL predicate
(:func:`visible_predicate`) applied inside the same query that fetches documents, so nothing is
ever loaded and then filtered in Python.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    ColumnElement,
    and_,
    delete,
    exists,
    false,
    func,
    insert,
    or_,
    select,
    text,
    true,
    update,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError, ValidationFailed
from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.documents.models import Document, DocumentAcl, DocumentVersion, UploadIntent

_CONSTRAINT_FIELDS: dict[str, str] = {
    "documents_academic_year_fk": "academic_year_id",
    "documents_title_check": "title",
    "documents_issuer_check": "issuer",
}


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    """Map a PostgreSQL error to a domain error; ``None`` if unexpected."""
    orig = exc.orig
    state = getattr(orig, "sqlstate", None)
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None) or ""
    if state == "23505":
        return Conflict("This file or version was registered already.", code="duplicate")
    if state == "23503":
        if constraint in _CONSTRAINT_FIELDS:
            field = _CONSTRAINT_FIELDS[constraint]
            return ValidationFailed(
                [{"field": field, "code": "not_found", "message_key": "errors.not_found"}]
            )
        return Conflict(
            "This document is evidence for a student record and must be kept.",
            code="document_in_use",
        )
    if state == "23514":
        field = _CONSTRAINT_FIELDS.get(constraint, "body")
        return ValidationFailed(
            [{"field": field, "code": "invalid", "message_key": "errors.invalid"}]
        )
    return None


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set; use core.db.tenant_session()")
    return uuid.UUID(str(value))


# --- visibility -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Visibility:
    """The caller's ACL keys (docs/06 §6 ``<ALLOWED>`` uses the same shape).

    ``everything``: holders of ``document.manage_acl`` administer every document of the school.
    ``read``: holds ``document.read``; ``school_wide``: that grant is not limited by scopes.
    ``section_refs``/``class_refs``: scoped sections/classes, each widened by the structure
    (a class scope covers its sections; a section scope makes its class match class ACLs).
    """

    everything: bool
    read: bool
    school_wide: bool
    roles: frozenset[str]
    membership_ref: str
    section_refs: frozenset[str]
    class_refs: frozenset[str]


def visible_predicate(v: Visibility) -> ColumnElement[bool]:
    if v.everything:
        return true()
    if not v.read:
        return false()
    acl = DocumentAcl
    same_doc = and_(acl.tenant_id == Document.tenant_id, acl.document_id == Document.id)
    section_match: ColumnElement[bool] = (
        acl.principal_type == "section"
        if v.school_wide
        else and_(acl.principal_type == "section", acl.principal_ref.in_(sorted(v.section_refs)))
    )
    class_match: ColumnElement[bool] = (
        acl.principal_type == "class"
        if v.school_wide
        else and_(acl.principal_type == "class", acl.principal_ref.in_(sorted(v.class_refs)))
    )
    match_any = exists().where(
        same_doc,
        or_(
            and_(acl.principal_type == "role", acl.principal_ref.in_(sorted(v.roles))),
            and_(acl.principal_type == "membership", acl.principal_ref == v.membership_ref),
            section_match,
            class_match,
        ),
    )
    if v.school_wide:
        # Fail closed: an empty ACL is visible only to school-wide readers (docs/05 §6).
        return or_(~exists().where(same_doc), match_any)
    return match_any


# --- upload intents -------------------------------------------------------------------------


def insert_intent(session: Session, **values: Any) -> UploadIntent:
    return session.scalars(insert(UploadIntent).values(**values).returning(UploadIntent)).one()


def lock_intent(session: Session, intent_id: uuid.UUID) -> UploadIntent | None:
    return session.scalars(
        select(UploadIntent).where(UploadIntent.id == intent_id).with_for_update(),
        execution_options={"populate_existing": True},
    ).one_or_none()


def consume_intent(session: Session, intent_id: uuid.UUID, now: dt.datetime) -> None:
    session.execute(
        update(UploadIntent)
        .where(UploadIntent.id == intent_id, UploadIntent.consumed_at.is_(None))
        .values(consumed_at=now)
    )


def expired_intents(session: Session, now: dt.datetime, limit: int) -> list[UploadIntent]:
    return list(
        session.scalars(
            select(UploadIntent)
            .where(UploadIntent.consumed_at.is_(None), UploadIntent.expires_at < now)
            .order_by(UploadIntent.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


def delete_intents(session: Session, ids: Sequence[uuid.UUID]) -> None:
    if ids:
        session.execute(delete(UploadIntent).where(UploadIntent.id.in_(list(ids))))


def consumed_intents_before(
    session: Session, before: dt.datetime, limit: int
) -> list[UploadIntent]:
    return list(
        session.scalars(
            select(UploadIntent)
            .where(UploadIntent.consumed_at.is_not(None), UploadIntent.consumed_at < before)
            .order_by(UploadIntent.consumed_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


# --- documents ------------------------------------------------------------------------------


def insert_document(session: Session, **values: Any) -> Document:
    return session.scalars(insert(Document).values(**values).returning(Document)).one()


def insert_version(session: Session, **values: Any) -> DocumentVersion:
    return session.scalars(
        insert(DocumentVersion).values(**values).returning(DocumentVersion)
    ).one()


def get_document(
    session: Session,
    document_id: uuid.UUID,
    *,
    visibility: Visibility | None = None,
    for_update: bool = False,
) -> Document | None:
    stmt = select(Document).where(Document.id == document_id)
    if visibility is not None:
        stmt = stmt.where(visible_predicate(visibility))
    if for_update:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def list_documents(
    session: Session,
    visibility: Visibility,
    *,
    limit: int,
    before_id: uuid.UUID | None = None,
    purpose: str | None = None,
    doc_type: str | None = None,
    academic_year_id: uuid.UUID | None = None,
    status: str | None = None,
) -> list[Document]:
    stmt = select(Document).where(visible_predicate(visibility))
    if before_id is not None:
        stmt = stmt.where(Document.id < before_id)
    if purpose is not None:
        stmt = stmt.where(Document.purpose == purpose)
    if doc_type is not None:
        stmt = stmt.where(Document.doc_type == doc_type)
    if academic_year_id is not None:
        stmt = stmt.where(Document.academic_year_id == academic_year_id)
    if status is not None:
        stmt = stmt.where(Document.status == status)
    return list(session.scalars(stmt.order_by(Document.id.desc()).limit(limit)))


def update_document(
    session: Session, document_id: uuid.UUID, *, expected_version: int | None, **values: Any
) -> Document | None:
    stmt = update(Document).where(Document.id == document_id)
    if expected_version is not None:
        stmt = stmt.where(Document.version == expected_version)
    return session.scalars(
        stmt.values(**values, version=Document.version + 1).returning(Document),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()


def delete_document(session: Session, document_id: uuid.UUID) -> None:
    # Clear the circular reference first; versions and ACL rows go with ON DELETE CASCADE.
    session.execute(
        update(Document).where(Document.id == document_id).values(current_version_id=None)
    )
    session.execute(delete(Document).where(Document.id == document_id))


# --- versions -------------------------------------------------------------------------------


def versions_of(session: Session, document_ids: Iterable[uuid.UUID]) -> list[DocumentVersion]:
    ids = list(document_ids)
    if not ids:
        return []
    return list(
        session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id.in_(ids))
            .order_by(DocumentVersion.document_id, DocumentVersion.version_no.desc())
        )
    )


def get_version(
    session: Session,
    document_id: uuid.UUID,
    version_no: int | None = None,
    *,
    version_id: uuid.UUID | None = None,
    for_update: bool = False,
) -> DocumentVersion | None:
    stmt = select(DocumentVersion).where(DocumentVersion.document_id == document_id)
    if version_id is not None:
        stmt = stmt.where(DocumentVersion.id == version_id)
    if version_no is not None:
        stmt = stmt.where(DocumentVersion.version_no == version_no)
    stmt = stmt.order_by(DocumentVersion.version_no.desc()).limit(1)
    if for_update:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def latest_version_with_status(
    session: Session, document_id: uuid.UUID, statuses: Sequence[str]
) -> DocumentVersion | None:
    return session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id, DocumentVersion.status.in_(statuses))
        .order_by(DocumentVersion.version_no.desc())
        .limit(1)
    ).one_or_none()


def max_version_no(session: Session, document_id: uuid.UUID) -> int:
    value = session.execute(
        select(func.coalesce(func.max(DocumentVersion.version_no), 0)).where(
            DocumentVersion.document_id == document_id
        )
    ).scalar_one()
    return int(value)


def versions_with_sha(
    session: Session, sha256: bytes, *, exclude_statuses: Sequence[str]
) -> list[DocumentVersion]:
    return list(
        session.scalars(
            select(DocumentVersion).where(
                DocumentVersion.sha256 == sha256, DocumentVersion.status.not_in(exclude_statuses)
            )
        )
    )


def set_version_status(
    session: Session,
    version_id: uuid.UUID,
    status: str,
    *,
    error: str | None = None,
    from_statuses: Sequence[str] | None = None,
) -> DocumentVersion | None:
    stmt = update(DocumentVersion).where(DocumentVersion.id == version_id)
    if from_statuses is not None:
        stmt = stmt.where(DocumentVersion.status.in_(from_statuses))
    return session.scalars(
        stmt.values(status=status, error=error).returning(DocumentVersion),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()


def discarded_versions(
    session: Session, errors: Sequence[str], *, since: dt.datetime, limit: int
) -> list[DocumentVersion]:
    """Quarantined versions whose file must be gone (``error`` in ``errors``), changed since
    ``since``, newest first (PRV-016 daily sweep)."""
    return list(
        session.scalars(
            select(DocumentVersion)
            .where(
                DocumentVersion.status == "quarantined",
                DocumentVersion.error.in_(list(errors)),
                DocumentVersion.updated_at >= since,
            )
            .order_by(DocumentVersion.updated_at.desc())
            .limit(limit)
        )
    )


def object_keys_of(session: Session, document_id: uuid.UUID) -> list[str]:
    return list(
        session.scalars(
            select(DocumentVersion.object_key).where(DocumentVersion.document_id == document_id)
        )
    )


# --- ACL ------------------------------------------------------------------------------------


def acl_of(
    session: Session, document_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[DocumentAcl]]:
    ids = list(document_ids)
    out: dict[uuid.UUID, list[DocumentAcl]] = {i: [] for i in ids}
    if not ids:
        return out
    rows = session.scalars(
        select(DocumentAcl)
        .where(DocumentAcl.document_id.in_(ids))
        .order_by(DocumentAcl.principal_type, DocumentAcl.principal_ref)
    )
    for row in rows:
        out[row.document_id].append(row)
    return out


def replace_acl(
    session: Session,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    entries: Iterable[tuple[str, str]],
) -> None:
    session.execute(delete(DocumentAcl).where(DocumentAcl.document_id == document_id))
    rows = [
        {
            "tenant_id": tenant_id,
            "document_id": document_id,
            "principal_type": t,
            "principal_ref": r,
        }
        for t, r in sorted(set(entries))
    ]
    if rows:
        session.execute(insert(DocumentAcl), rows)


# --- full data export (FR-ADM-001) ------------------------------------------------------------


def export_record_tables(session: Session) -> list[RecordTable]:
    """Every document, version and ACL entry of the current school (metadata only)."""
    return [
        dump_table(session, Document.__table__, name="documents", order_by=("created_at", "id")),
        dump_table(
            session,
            DocumentVersion.__table__,
            name="document_versions",
            order_by=("document_id", "version_no"),
        ),
        dump_table(
            session,
            DocumentAcl.__table__,
            name="document_acl",
            order_by=("document_id", "principal_type", "principal_ref"),
        ),
    ]


def ready_versions(session: Session) -> list[tuple[DocumentVersion, str]]:
    """Every version that passed the malware scan (``ready``) with its document's purpose,
    ordered by document and version."""
    stmt = (
        select(DocumentVersion, Document.purpose)
        .join(
            Document,
            and_(
                Document.tenant_id == DocumentVersion.tenant_id,
                Document.id == DocumentVersion.document_id,
            ),
        )
        .where(DocumentVersion.status == "ready")
        .order_by(DocumentVersion.document_id, DocumentVersion.version_no)
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]
