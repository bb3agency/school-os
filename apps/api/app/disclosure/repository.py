"""Database access for the disclosure (0066_disclosure; ADR-0051). Callers inside
``app.disclosure`` only; every function runs in the school's ``tenant_session`` (RLS)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.disclosure.models import DisclosureDraft, DisclosureSnapshot, DisclosureSnapshotDocument


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("disclosure repository used outside tenant_session")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


# --- drafts ----------------------------------------------------------------------------------


def get_draft(session: Session, *, lock: bool = False) -> DisclosureDraft | None:
    stmt = select(DisclosureDraft)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def insert_draft(session: Session, **values: Any) -> DisclosureDraft:
    row = DisclosureDraft(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def update_draft(session: Session, draft_id: uuid.UUID, values: dict[str, Any]) -> DisclosureDraft:
    session.execute(
        update(DisclosureDraft).where(DisclosureDraft.id == draft_id).values(**values),
        execution_options={"synchronize_session": False},
    )
    row = session.get(DisclosureDraft, draft_id, populate_existing=True)
    if row is None:  # pragma: no cover - the row was locked by the caller
        raise RuntimeError("draft vanished")
    return row


# --- snapshots ---------------------------------------------------------------------------------


def insert_snapshot(session: Session, **values: Any) -> DisclosureSnapshot:
    row = DisclosureSnapshot(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def insert_snapshot_documents(session: Session, rows: Sequence[dict[str, Any]]) -> None:
    tenant_id = current_tenant_id(session)
    for values in rows:
        session.add(DisclosureSnapshotDocument(tenant_id=tenant_id, **values))
    session.flush()


def get_snapshot(
    session: Session, snapshot_id: uuid.UUID, *, lock: bool = False
) -> DisclosureSnapshot | None:
    stmt = select(DisclosureSnapshot).where(DisclosureSnapshot.id == snapshot_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def latest_snapshot(session: Session, *, lock: bool = False) -> DisclosureSnapshot | None:
    """The most recent publication (withdrawn or not)."""
    stmt = (
        select(DisclosureSnapshot)
        .order_by(DisclosureSnapshot.published_at.desc(), DisclosureSnapshot.id.desc())
        .limit(1)
    )
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def list_snapshots(session: Session, limit: int) -> list[DisclosureSnapshot]:
    stmt = (
        select(DisclosureSnapshot)
        .order_by(DisclosureSnapshot.published_at.desc(), DisclosureSnapshot.id.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def update_snapshot(
    session: Session, snapshot_id: uuid.UUID, values: dict[str, Any]
) -> DisclosureSnapshot:
    session.execute(
        update(DisclosureSnapshot).where(DisclosureSnapshot.id == snapshot_id).values(**values),
        execution_options={"synchronize_session": False},
    )
    row = get_snapshot(session, snapshot_id)
    if row is None:  # pragma: no cover - the row was locked by the caller
        raise RuntimeError("snapshot vanished")
    return row


def snapshot_documents(
    session: Session, snapshot_id: uuid.UUID
) -> list[DisclosureSnapshotDocument]:
    stmt = (
        select(DisclosureSnapshotDocument)
        .where(DisclosureSnapshotDocument.snapshot_id == snapshot_id)
        .order_by(DisclosureSnapshotDocument.n)
    )
    return list(session.scalars(stmt))


def snapshot_document(
    session: Session, snapshot_id: uuid.UUID, n: int
) -> DisclosureSnapshotDocument | None:
    stmt = select(DisclosureSnapshotDocument).where(
        DisclosureSnapshotDocument.snapshot_id == snapshot_id, DisclosureSnapshotDocument.n == n
    )
    return session.scalars(stmt).one_or_none()


def document_is_published(session: Session, document_id: uuid.UUID) -> bool:
    stmt = (
        select(DisclosureSnapshotDocument.id)
        .where(DisclosureSnapshotDocument.document_id == document_id)
        .limit(1)
    )
    return session.scalars(stmt).first() is not None


_COUNT_VIEW = text(
    """
    UPDATE ops.disclosure_snapshots AS s
       SET views_by_day = (
         SELECT COALESCE(jsonb_object_agg(e.key, e.value), '{}'::jsonb)
           FROM jsonb_each(
                  s.views_by_day || jsonb_build_object(
                    CAST(:day AS text),
                    COALESCE((s.views_by_day ->> CAST(:day AS text))::int, 0) + 1)) AS e
          WHERE e.key >= CAST(:keep_from AS text))
     WHERE s.id = :id
    """
)


def count_view(session: Session, snapshot_id: uuid.UUID, day: dt.date, keep_days: int) -> None:
    """Add one view to ``day`` and drop days older than ``keep_days`` (no visitor data)."""
    keep_from = day - dt.timedelta(days=keep_days - 1)
    session.execute(
        _COUNT_VIEW,
        {"id": snapshot_id, "day": day.isoformat(), "keep_from": keep_from.isoformat()},
    )


# --- export ----------------------------------------------------------------------------------


def export_tables(session: Session) -> list[RecordTable]:
    """The draft (with the school's current public link), every snapshot (without the storage
    keys of its files) and its published document versions (FR-DISC-012)."""
    return [
        dump_table(session, DisclosureDraft.__table__, name="disclosure_drafts"),
        dump_table(
            session,
            DisclosureSnapshot.__table__,
            name="disclosure_snapshots",
            exclude=("pdf_key", "html_key"),
            order_by=("published_at", "id"),
        ),
        dump_table(
            session,
            DisclosureSnapshotDocument.__table__,
            name="disclosure_snapshot_documents",
            order_by=("snapshot_id", "n"),
        ),
    ]


__all__ = [
    "count_view",
    "current_tenant_id",
    "document_is_published",
    "export_tables",
    "get_draft",
    "get_snapshot",
    "insert_draft",
    "insert_snapshot",
    "insert_snapshot_documents",
    "latest_snapshot",
    "list_snapshots",
    "now",
    "snapshot_document",
    "snapshot_documents",
    "update_draft",
    "update_snapshot",
]
