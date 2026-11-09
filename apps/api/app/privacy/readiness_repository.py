"""Database access for the DPDP readiness checklist and the notice given at admission
(0086_dpdp_readiness; FC-03, FP-13).

Callers inside ``app.privacy`` only. Every function runs in a ``core.db.tenant_session``: RLS
limits each statement to that school. Both tables are insert-only for the app role.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.privacy.models import PrivacyNotice, PrivacyRequest
from app.privacy.readiness_models import AdmissionNotice, ReadinessMark


def insert_mark(session: Session, values: Mapping[str, Any]) -> ReadinessMark:
    row = ReadinessMark(**dict(values))
    session.add(row)
    session.flush()
    return row


def latest_marks(session: Session) -> dict[str, ReadinessMark]:
    """The latest mark per item (by date, then by when it was recorded)."""
    stmt = select(ReadinessMark).order_by(
        ReadinessMark.item, ReadinessMark.done_on.desc(), ReadinessMark.recorded_at.desc()
    )
    out: dict[str, ReadinessMark] = {}
    for row in session.scalars(stmt):
        out.setdefault(row.item, row)
    return out


def closed_request_count(session: Session) -> int:
    stmt = select(func.count()).select_from(PrivacyRequest).where(PrivacyRequest.status == "closed")
    return int(session.execute(stmt).scalar_one())


def get_notice(session: Session, notice_id: uuid.UUID) -> PrivacyNotice | None:
    return session.get(PrivacyNotice, notice_id)


def admission_notices(
    session: Session, student_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, AdmissionNotice]:
    if not student_ids:
        return {}
    stmt = select(AdmissionNotice).where(AdmissionNotice.student_id.in_(list(student_ids)))
    return {r.student_id: r for r in session.scalars(stmt)}


def insert_admission_notices(
    session: Session, rows: Sequence[Mapping[str, Any]]
) -> list[AdmissionNotice]:
    out = [AdmissionNotice(**dict(r)) for r in rows]
    session.add_all(out)
    session.flush()
    return out


def admission_notices_of(session: Session, student_id: uuid.UUID) -> list[AdmissionNotice]:
    stmt = select(AdmissionNotice).where(AdmissionNotice.student_id == student_id)
    return list(session.scalars(stmt))


def export_tables(session: Session) -> list[RecordTable]:
    """The readiness marks and admission notice records for the school's full data export."""
    return [
        dump_table(session, ReadinessMark.__table__, name="privacy_readiness_marks"),
        dump_table(session, AdmissionNotice.__table__, name="privacy_admission_notices"),
    ]
