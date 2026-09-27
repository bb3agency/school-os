"""Database access for imports. Only ``app.imports.service`` calls this module.

Every function takes a ``core.db.tenant_session()``: RLS limits each statement to that school.
Bound parameters only.

The last section ("revert") touches student-record tables of ``app.students``: the revert of a
batch (FR-IMP-005) must remove the students the batch created and withdraw the values it
recorded, and ``students.service`` has no function for that yet (reported to its owner). The
statements are narrow and guarded by the database itself (migration 0012): a student can only be
deleted while the batch that created it is ``reverting``; values are never deleted directly
(the app has no DELETE grant on ``sis.attribute_values``) but go with their student through the
``ON DELETE CASCADE`` foreign key; withdrawn values are marked ``rejected`` (history is kept).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError
from app.imports.models import ImportBatch, ImportMappingTemplate, ImportRow

LIVE_STATUSES = ("parsing", "validating", "committing", "reverting")


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    orig = exc.orig
    state = getattr(orig, "sqlstate", None)
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None) or ""
    if state == "23505" and constraint == "import_batches_one_live_per_document":
        return Conflict("This file is already being imported.", code="import_exists")
    if state == "23505" and constraint == "import_mapping_templates_name_key":
        return Conflict("A template with this name exists. Choose another name.", code="duplicate")
    if state == "23503":
        return Conflict(
            "Records from this import were changed or are used elsewhere, so it cannot be "
            "reverted. Correct the records instead.",
            code="import_has_dependents",
        )
    return None


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set; use core.db.tenant_session()")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


# --- batches ------------------------------------------------------------------------------------


def insert_batch(session: Session, **values: Any) -> ImportBatch:
    return session.execute(insert(ImportBatch).values(**values).returning(ImportBatch)).scalar_one()


def get_batch(session: Session, batch_id: uuid.UUID, *, lock: bool = False) -> ImportBatch | None:
    stmt = select(ImportBatch).where(ImportBatch.id == batch_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def batch_id_taken(session: Session, batch_id: uuid.UUID) -> bool:
    return (
        session.execute(select(ImportBatch.id).where(ImportBatch.id == batch_id)).first()
        is not None
    )


def list_batches(
    session: Session,
    *,
    limit: int,
    before_id: uuid.UUID | None,
    created_by: uuid.UUID | None,
    status: str | None,
) -> list[ImportBatch]:
    stmt = select(ImportBatch).order_by(ImportBatch.id.desc()).limit(limit)
    if before_id is not None:
        stmt = stmt.where(ImportBatch.id < before_id)
    if created_by is not None:
        stmt = stmt.where(ImportBatch.created_by == created_by)
    if status is not None:
        stmt = stmt.where(ImportBatch.status == status)
    return list(session.execute(stmt).scalars())


def update_batch(
    session: Session,
    batch_id: uuid.UUID,
    *,
    expected_version: int | None = None,
    **values: Any,
) -> ImportBatch | None:
    """Update and bump the version (ETag); ``None`` when ``expected_version`` does not match."""
    stmt = update(ImportBatch).where(ImportBatch.id == batch_id)
    if expected_version is not None:
        stmt = stmt.where(ImportBatch.version == expected_version)
    return session.scalars(
        stmt.values(version=ImportBatch.version + 1, **values).returning(ImportBatch),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()


def batches_for_document(session: Session, document_id: uuid.UUID) -> list[ImportBatch]:
    return list(
        session.execute(select(ImportBatch).where(ImportBatch.document_id == document_id)).scalars()
    )


def batches_due_for_file_deletion(session: Session, cutoff: dt.datetime) -> list[ImportBatch]:
    """Batches whose raw file has been kept long enough (FR-IMP-007): committed (or reverted)
    batches ``retention`` days after commit; never-committed batches that long after upload."""
    return list(
        session.execute(
            select(ImportBatch)
            .where(
                ImportBatch.document_id.is_not(None),
                ImportBatch.status.not_in(LIVE_STATUSES),
                func.coalesce(ImportBatch.committed_at, ImportBatch.created_at) <= cutoff,
            )
            .order_by(ImportBatch.id)
            .limit(500)
        ).scalars()
    )


def mark_raw_file_deleted(
    session: Session, batch_ids: Sequence[uuid.UUID], at: dt.datetime
) -> None:
    session.execute(
        update(ImportBatch)
        .where(ImportBatch.id.in_(batch_ids))
        .values(raw_file_deleted_at=at, version=ImportBatch.version + 1)
        .execution_options(synchronize_session=False)
    )


# --- rows ---------------------------------------------------------------------------------------


def replace_rows(
    session: Session, tenant_id: uuid.UUID, batch_id: uuid.UUID, rows: Sequence[Mapping[str, Any]]
) -> None:
    session.execute(delete(ImportRow).where(ImportRow.batch_id == batch_id))
    if rows:
        session.execute(
            insert(ImportRow),
            [{"tenant_id": tenant_id, "batch_id": batch_id, **row} for row in rows],
        )


def list_rows(
    session: Session,
    batch_id: uuid.UUID,
    *,
    status: str | None,
    with_warnings: bool,
    after_row_no: int | None,
    limit: int,
) -> list[ImportRow]:
    stmt = (
        select(ImportRow)
        .where(ImportRow.batch_id == batch_id)
        .order_by(ImportRow.row_no)
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(ImportRow.status == status)
    if with_warnings:
        stmt = stmt.where(func.jsonb_array_length(ImportRow.warnings) > 0)
    if after_row_no is not None:
        stmt = stmt.where(ImportRow.row_no > after_row_no)
    return list(session.execute(stmt).scalars())


def committed_rows(session: Session, batch_id: uuid.UUID) -> list[ImportRow]:
    return list(
        session.execute(
            select(ImportRow)
            .where(ImportRow.batch_id == batch_id, ImportRow.status == "committed")
            .order_by(ImportRow.row_no)
        ).scalars()
    )


def mark_rows_reverted(session: Session, batch_id: uuid.UUID) -> None:
    session.execute(
        update(ImportRow)
        .where(ImportRow.batch_id == batch_id, ImportRow.status == "committed")
        .values(status="reverted")
        .execution_options(synchronize_session=False)
    )


# --- templates ------------------------------------------------------------------------------------


def insert_template(session: Session, **values: Any) -> ImportMappingTemplate:
    return session.execute(
        insert(ImportMappingTemplate).values(**values).returning(ImportMappingTemplate)
    ).scalar_one()


def list_templates(session: Session) -> list[ImportMappingTemplate]:
    return list(
        session.execute(
            select(ImportMappingTemplate).order_by(ImportMappingTemplate.name).limit(200)
        ).scalars()
    )


def find_template(session: Session, signature: str, source: str) -> ImportMappingTemplate | None:
    """The school's template for this header layout: same source first, most recently used."""
    return session.execute(
        select(ImportMappingTemplate)
        .where(ImportMappingTemplate.header_signature == signature)
        .order_by(
            (ImportMappingTemplate.source == source).desc(),
            ImportMappingTemplate.last_used_at.desc().nulls_last(),
            ImportMappingTemplate.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()


def touch_template(session: Session, template_id: uuid.UUID) -> None:
    session.execute(
        update(ImportMappingTemplate)
        .where(ImportMappingTemplate.id == template_id)
        .values(last_used_at=func.now())
        .execution_options(synchronize_session=False)
    )


# --- revert (student-record tables; see module docstring) -----------------------------------------


@dataclass(frozen=True, slots=True)
class BatchValue:
    id: uuid.UUID
    student_id: uuid.UUID
    attribute_key: str
    source: str
    superseded_by: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class PriorValue:
    """A value that one of the batch's values superseded (restored on revert)."""

    id: uuid.UUID
    successor_id: uuid.UUID
    student_id: uuid.UUID
    attribute_key: str
    source: str
    value_text: str | None
    value_date: dt.date | None
    value_ciphertext: bytes | None
    verification_status: str
    evidence_document_id: uuid.UUID | None


def student_versions(session: Session, student_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not student_ids:
        return {}
    rows = session.execute(
        text("SELECT id, version FROM sis.students WHERE id = ANY(:ids)"),
        {"ids": list(student_ids)},
    ).all()
    return {r.id: int(r.version) for r in rows}


def batch_values(session: Session, batch_id: uuid.UUID) -> list[BatchValue]:
    rows = session.execute(
        text(
            "SELECT id, student_id, attribute_key, source, superseded_by "
            "FROM sis.attribute_values WHERE import_batch_id = :b ORDER BY id"
        ),
        {"b": batch_id},
    ).all()
    return [
        BatchValue(r.id, r.student_id, r.attribute_key, r.source, r.superseded_by) for r in rows
    ]


def prior_values(session: Session, successor_ids: Collection[uuid.UUID]) -> list[PriorValue]:
    if not successor_ids:
        return []
    rows = session.execute(
        text(
            "SELECT id, superseded_by, student_id, attribute_key, source, value_text, value_date, "
            "value_ciphertext, verification_status, evidence_document_id "
            "FROM sis.attribute_values WHERE superseded_by = ANY(:ids) ORDER BY id"
        ),
        {"ids": list(successor_ids)},
    ).all()
    return [
        PriorValue(
            id=r.id,
            successor_id=r.superseded_by,
            student_id=r.student_id,
            attribute_key=r.attribute_key,
            source=r.source,
            value_text=r.value_text,
            value_date=r.value_date,
            value_ciphertext=bytes(r.value_ciphertext) if r.value_ciphertext is not None else None,
            verification_status=r.verification_status,
            evidence_document_id=r.evidence_document_id,
        )
        for r in rows
    ]


def withdraw_values(session: Session, value_ids: Collection[uuid.UUID], user_id: uuid.UUID) -> int:
    """Mark current values of a reverted batch ``rejected`` (kept in history, never canonical)."""
    if not value_ids:
        return 0
    result = session.execute(
        text(
            "UPDATE sis.attribute_values SET verification_status = 'rejected', "
            "verified_by = :u, verified_at = now() "
            "WHERE id = ANY(:ids) AND superseded_by IS NULL RETURNING id"
        ),
        {"ids": list(value_ids), "u": user_id},
    )
    return len(result.all())


def delete_created_students(session: Session, student_ids: Collection[uuid.UUID]) -> int:
    """Remove students created by a batch that is ``reverting`` (values and profile cascade)."""
    if not student_ids:
        return 0
    ids = list(student_ids)
    session.execute(text("DELETE FROM sis.enrollments WHERE student_id = ANY(:ids)"), {"ids": ids})
    result = session.execute(
        text("DELETE FROM sis.students WHERE id = ANY(:ids) RETURNING id"), {"ids": ids}
    )
    return len(result.all())
