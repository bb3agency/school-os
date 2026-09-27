"""Database access for extraction. Only ``app.extraction.service`` calls this module.

Every function takes a ``core.db.tenant_session()``: RLS limits each statement to that school.
Import and extraction staff are school-wide (docs/07 §6.2: ``import.run``/``import.commit`` are
never scoped), so batches and items need no further object filter; student and document access
is checked by their own modules' services.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError
from app.extraction.models import ExtractionBatch, ExtractionItem, ExtractionPage


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    """Map a PostgreSQL error to a domain error; ``None`` if unexpected."""
    state = getattr(exc.orig, "sqlstate", None)
    if state == "23505":
        return Conflict("This was saved already.", code="duplicate")
    if state == "42501":
        return Conflict("This row was reviewed already.", code="item_already_reviewed")
    return None


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set; use core.db.tenant_session()")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


# --- batches --------------------------------------------------------------------------------


def insert_batch(session: Session, values: Mapping[str, Any]) -> ExtractionBatch:
    batch = ExtractionBatch(**values)
    session.add(batch)
    session.flush()
    session.refresh(batch)
    return batch


def get_batch(
    session: Session, batch_id: uuid.UUID, *, for_update: bool = False
) -> ExtractionBatch | None:
    stmt = select(ExtractionBatch).where(ExtractionBatch.id == batch_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def list_batches(
    session: Session, *, before_id: uuid.UUID | None, limit: int
) -> list[ExtractionBatch]:
    stmt = select(ExtractionBatch).order_by(ExtractionBatch.id.desc()).limit(limit)
    if before_id is not None:
        stmt = stmt.where(ExtractionBatch.id < before_id)
    return list(session.execute(stmt).scalars())


def update_batch(
    session: Session, batch_id: uuid.UUID, values: Mapping[str, Any]
) -> ExtractionBatch:
    """Apply ``values`` (plain values or SQL expressions such as ``col + 1``); bumps version."""
    stmt = (
        update(ExtractionBatch)
        .where(ExtractionBatch.id == batch_id)
        .values(**values, version=ExtractionBatch.version + 1)
        .returning(ExtractionBatch)
        .execution_options(synchronize_session=False)
    )
    row: ExtractionBatch = session.execute(stmt).scalar_one()
    session.refresh(row)
    return row


# --- pages ----------------------------------------------------------------------------------


def insert_pages(session: Session, rows: Sequence[Mapping[str, Any]]) -> None:
    session.execute(pg_insert(ExtractionPage), [dict(r) for r in rows])


def pages_of(session: Session, batch_id: uuid.UUID) -> list[ExtractionPage]:
    stmt = (
        select(ExtractionPage)
        .where(ExtractionPage.batch_id == batch_id)
        .order_by(ExtractionPage.seq)
    )
    return list(session.execute(stmt).scalars())


def pages_by_id(
    session: Session, page_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, ExtractionPage]:
    if not page_ids:
        return {}
    stmt = select(ExtractionPage).where(ExtractionPage.id.in_(list(page_ids)))
    return {p.id: p for p in session.execute(stmt).scalars()}


def get_page(
    session: Session, page_id: uuid.UUID, *, for_update: bool = False
) -> ExtractionPage | None:
    stmt = select(ExtractionPage).where(ExtractionPage.id == page_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def documents_in_open_batches(
    session: Session, document_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """Documents already queued or extracted (a failed page may be extracted again)."""
    if not document_ids:
        return set()
    stmt = select(ExtractionPage.document_id).where(
        ExtractionPage.document_id.in_(list(document_ids)), ExtractionPage.status != "failed"
    )
    return set(session.execute(stmt).scalars())


def finish_page(
    session: Session, page_id: uuid.UUID, values: Mapping[str, Any]
) -> ExtractionPage | None:
    """Move a ``queued`` page to its outcome; ``None`` when it was finished already."""
    stmt = (
        update(ExtractionPage)
        .where(ExtractionPage.id == page_id, ExtractionPage.status == "queued")
        .values(**values)
        .returning(ExtractionPage)
        .execution_options(synchronize_session=False)
    )
    return session.execute(stmt).scalar_one_or_none()


def fail_queued_pages(session: Session, batch_id: uuid.UUID, error_code: str) -> int:
    stmt = (
        update(ExtractionPage)
        .where(ExtractionPage.batch_id == batch_id, ExtractionPage.status == "queued")
        .values(status="failed", error_code=error_code, processed_at=func.now())
        .execution_options(synchronize_session=False)
    )
    return int(session.execute(stmt).rowcount or 0)  # type: ignore[attr-defined]


# --- items ----------------------------------------------------------------------------------


def insert_items(session: Session, rows: Sequence[Mapping[str, Any]]) -> None:
    if rows:
        stmt = pg_insert(ExtractionItem).on_conflict_do_nothing(
            constraint="extraction_items_row_key"
        )
        session.execute(stmt, [dict(r) for r in rows])


def get_item(
    session: Session, item_id: uuid.UUID, *, for_update: bool = False
) -> ExtractionItem | None:
    stmt = select(ExtractionItem).where(ExtractionItem.id == item_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def list_items(
    session: Session,
    *,
    batch_id: uuid.UUID | None,
    status: str | None,
    after_id: uuid.UUID | None,
    limit: int,
) -> list[ExtractionItem]:
    """Queue order: by id (UUIDv7, written page by page, row by row)."""
    stmt = select(ExtractionItem).order_by(ExtractionItem.id).limit(limit)
    if batch_id is not None:
        stmt = stmt.where(ExtractionItem.batch_id == batch_id)
    if status is not None:
        stmt = stmt.where(ExtractionItem.status == status)
    if after_id is not None:
        stmt = stmt.where(ExtractionItem.id > after_id)
    return list(session.execute(stmt).scalars())


def review_item(
    session: Session, item_id: uuid.UUID, values: Mapping[str, Any]
) -> ExtractionItem | None:
    """Record the review outcome of a ``pending_review`` item; ``None`` if already reviewed."""
    stmt = (
        update(ExtractionItem)
        .where(ExtractionItem.id == item_id, ExtractionItem.status == "pending_review")
        .values(**values, version=ExtractionItem.version + 1)
        .returning(ExtractionItem)
        .execution_options(synchronize_session=False)
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is not None:
        session.refresh(row)
    return row
