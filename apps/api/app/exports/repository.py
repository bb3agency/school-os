"""Database access for exports. Only ``app.exports.service`` calls this module.

Every function takes a ``core.db.tenant_session()``: RLS limits each statement to that school.
Bound parameters only. The app may update only the workflow columns of ``ops.exports`` and never
deletes rows (migration 0017).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import and_, func, insert, or_, select, text, update
from sqlalchemy.orm import Session

from app.exports.models import Export, ExportFile


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set; use core.db.tenant_session()")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def insert_export(session: Session, **values: Any) -> Export:
    return session.execute(insert(Export).values(**values).returning(Export)).scalar_one()


def get_export(session: Session, export_id: uuid.UUID, *, lock: bool = False) -> Export | None:
    stmt = select(Export).where(Export.id == export_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def update_export(session: Session, export_id: uuid.UUID, values: Mapping[str, Any]) -> Export:
    stmt = (
        update(Export)
        .where(Export.id == export_id)
        .values(**values, version=Export.version + 1)
        .returning(Export)
        .execution_options(synchronize_session=False)
    )
    return session.execute(stmt).scalar_one()


def list_for_membership(
    session: Session,
    membership_id: uuid.UUID,
    *,
    after: tuple[dt.datetime, uuid.UUID] | None,
    limit: int,
) -> list[Export]:
    stmt = select(Export).where(Export.requested_by_membership == membership_id)
    if after is not None:
        created, last = after
        stmt = stmt.where(
            or_(
                Export.created_at < created,
                and_(Export.created_at == created, Export.id < last),
            )
        )
    stmt = stmt.order_by(Export.created_at.desc(), Export.id.desc()).limit(limit)
    return list(session.scalars(stmt))


def insert_files(session: Session, rows: Sequence[Mapping[str, Any]]) -> None:
    if rows:
        session.execute(insert(ExportFile), [dict(r) for r in rows])


def files_of(
    session: Session, export_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[ExportFile]]:
    if not export_ids:
        return {}
    out: dict[uuid.UUID, list[ExportFile]] = {}
    stmt = (
        select(ExportFile)
        .where(ExportFile.export_id.in_(list(export_ids)))
        .order_by(ExportFile.export_id, ExportFile.format)
    )
    for row in session.scalars(stmt):
        out.setdefault(row.export_id, []).append(row)
    return out


def due_for_purge(
    session: Session, *, now: dt.datetime, failed_before: dt.datetime, limit: int = 500
) -> list[Export]:
    """Ready exports past ``expires_at`` and failed ones created before ``failed_before`` whose
    files were not deleted yet (docs/05 §13)."""
    stmt = (
        select(Export)
        .where(
            Export.files_deleted_at.is_(None),
            or_(
                and_(Export.status == "ready", Export.expires_at <= now),
                and_(Export.status == "failed", Export.created_at <= failed_before),
            ),
        )
        .order_by(Export.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(session.scalars(stmt))
