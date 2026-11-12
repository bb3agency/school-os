"""Database access for the inspection pack log (0098_inspection_packs; FC-07, FO-12).

Callers inside ``app.compliance`` only. Every function runs in a ``core.db.tenant_session``:
RLS limits each statement to that school. Rows are never updated or deleted (only the
offboarding purge removes them).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.compliance.packs_models import InspectionPack
from app.core.record_tables import dump_table
from app.core.records import RecordTable


def insert(session: Session, **values: Any) -> InspectionPack:
    row = InspectionPack(**values)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def get(session: Session, pack_id: uuid.UUID) -> InspectionPack | None:
    return session.scalars(select(InspectionPack).where(InspectionPack.id == pack_id)).one_or_none()


def recent(session: Session, limit: int) -> list[InspectionPack]:
    stmt = (
        select(InspectionPack)
        .order_by(InspectionPack.generated_at.desc(), InspectionPack.id.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def export_tables(session: Session) -> list[RecordTable]:
    """The school's pack log for its full data export (FR-COMP-043)."""
    return [
        dump_table(
            session,
            InspectionPack.__table__,
            name="inspection_packs",
            order_by=("generated_at", "id"),
        )
    ]


__all__ = ["export_tables", "get", "insert", "recent"]
