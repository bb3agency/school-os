"""Database access for legal holds (0087_legal_holds; ADR-0059).

Callers inside ``app.privacy`` only. Every function runs in a ``core.db.tenant_session``: RLS
limits each statement to that school. Holds are never deleted (only the offboarding purge
removes them); the workflow changes ``status`` and its columns.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Any, Final

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import Session

from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.privacy.holds_models import LegalHold

CIPHERTEXT: Final = "reference_ciphertext"
# Holds that stop retention and erasure (a lift waiting for its approval still holds).
EFFECTIVE: Final = ("active", "lift_pending")
OPEN: Final = ("pending", "active", "lift_pending")
# Lifted and rejected holds the list shows (newest first); open ones are always listed in full.
CLOSED_LIMIT: Final = 200


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("legal hold repository used outside tenant_session")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def insert(session: Session, **values: Any) -> LegalHold:
    row = LegalHold(**values)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def get(session: Session, hold_id: uuid.UUID, *, lock: bool = False) -> LegalHold | None:
    stmt = select(LegalHold).where(LegalHold.id == hold_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).one_or_none()


def list_holds(session: Session) -> list[LegalHold]:
    """Open holds (earliest review first), then the newest closed ones."""
    open_rows = list(
        session.scalars(
            select(LegalHold)
            .where(LegalHold.status.in_(OPEN))
            .order_by(LegalHold.review_on, LegalHold.id)
        )
    )
    closed = session.scalars(
        select(LegalHold)
        .where(LegalHold.status.not_in(OPEN))
        .order_by(LegalHold.updated_at.desc(), LegalHold.id.desc())
        .limit(CLOSED_LIMIT)
    )
    return [*open_rows, *closed]


def effective(session: Session, record_class: str) -> tuple[bool, frozenset[uuid.UUID]]:
    """(the class is held as a whole, the students under a hold) for the current school."""
    rows = session.execute(
        select(LegalHold.scope, LegalHold.student_id, LegalHold.record_class).where(
            LegalHold.status.in_(EFFECTIVE),
            or_(LegalHold.scope == "student", LegalHold.record_class == record_class),
        )
    ).all()
    whole = any(r.scope == "record_class" for r in rows)
    students = frozenset(r.student_id for r in rows if r.student_id is not None)
    return whole, students


def student_held(session: Session, student_id: uuid.UUID) -> bool:
    stmt = select(func.count()).where(
        LegalHold.status.in_(EFFECTIVE), LegalHold.student_id == student_id
    )
    return int(session.execute(stmt).scalar_one()) > 0


def held_classes(session: Session) -> set[str]:
    """Record classes held as a whole in the current school."""
    stmt = select(LegalHold.record_class).where(
        LegalHold.status.in_(EFFECTIVE), LegalHold.scope == "record_class"
    )
    return {str(c) for c in session.scalars(stmt) if c is not None}


def update_hold(
    session: Session, row: LegalHold, values: Mapping[str, Any], *, expected_version: int
) -> LegalHold | None:
    """Optimistic update: ``None`` when the row's version moved on (someone else acted)."""
    result = session.execute(
        update(LegalHold)
        .where(LegalHold.id == row.id, LegalHold.version == expected_version)
        .values({**values, "version": LegalHold.version + 1})
        .returning(LegalHold.id),
        execution_options={"synchronize_session": False},
    )
    if result.scalar_one_or_none() is None:
        return None
    session.expire(row)
    return get(session, row.id)


def due_for_reminder(
    session: Session, today: dt.date, *, before_days: int, every_days: int
) -> list[LegalHold]:
    """Effective holds whose review date is within ``before_days`` and that were not reminded
    in the last ``every_days`` days."""
    stmt = (
        select(LegalHold)
        .where(
            LegalHold.status.in_(EFFECTIVE),
            LegalHold.review_on <= today + dt.timedelta(days=before_days),
            or_(
                LegalHold.last_reminded_on.is_(None),
                LegalHold.last_reminded_on <= today - dt.timedelta(days=every_days),
            ),
        )
        .order_by(LegalHold.review_on, LegalHold.id)
        .with_for_update(skip_locked=True)
    )
    return list(session.scalars(stmt))


def mark_reminded(session: Session, hold_id: uuid.UUID, day: dt.date) -> None:
    """Bookkeeping only: the row's version is not bumped (no one's edit is lost)."""
    session.execute(
        update(LegalHold).where(LegalHold.id == hold_id).values(last_reminded_on=day),
        execution_options={"synchronize_session": False},
    )


def set_ciphertext(session: Session, hold_id: uuid.UUID, blob: bytes) -> None:
    """Re-encryption only (DEK rotation): the row's version is not bumped."""
    session.execute(
        update(LegalHold).where(LegalHold.id == hold_id).values(reference_ciphertext=blob),
        execution_options={"synchronize_session": False},
    )


def stale_ciphertexts(
    session: Session, target_version: int, limit: int
) -> list[tuple[uuid.UUID, bytes]]:
    """(id, blob) of references under another key version than ``target_version``."""
    if limit <= 0:
        return []
    col = LegalHold.reference_ciphertext
    version = func.get_byte(col, 1) * 256 + func.get_byte(col, 2)
    stmt = (
        select(LegalHold.id, col)
        .where(col.is_not(None), version != target_version)
        .order_by(LegalHold.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return [(rid, bytes(blob)) for rid, blob in session.execute(stmt) if blob is not None]


def export_tables(session: Session) -> list[RecordTable]:
    """The school's legal holds for its full data export (the encrypted reference left out)."""
    return [
        dump_table(
            session,
            LegalHold.__table__,
            name="legal_holds",
            exclude=(CIPHERTEXT,),
            order_by=("placed_at", "id"),
        )
    ]


__all__ = [
    "EFFECTIVE",
    "OPEN",
    "current_tenant_id",
    "due_for_reminder",
    "effective",
    "export_tables",
    "get",
    "held_classes",
    "insert",
    "list_holds",
    "mark_reminded",
    "now",
    "set_ciphertext",
    "stale_ciphertexts",
    "student_held",
    "update_hold",
]
