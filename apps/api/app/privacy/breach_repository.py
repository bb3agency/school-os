"""Database access for the breach register (0067_consents_breach; ADR-0050).

Callers inside ``app.privacy`` only. Every function runs in a ``core.db.tenant_session``: RLS
limits each statement to that school. Records are never deleted by the app; affected lists are
replaced as a whole; reminders are insert-once.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Sequence
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.privacy.breach_models import BreachAffected, BreachRecord, BreachReminder

CIPHERTEXT = ("description_ciphertext", "containment_ciphertext")


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("breach repository used outside tenant_session")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def list_records(session: Session, *, include_closed: bool = True) -> list[BreachRecord]:
    stmt = select(BreachRecord).order_by(BreachRecord.noticed_at.desc(), BreachRecord.id.desc())
    if not include_closed:
        stmt = stmt.where(BreachRecord.status != "closed")
    return list(session.scalars(stmt.limit(500)))


def get(session: Session, breach_id: uuid.UUID, *, lock: bool = False) -> BreachRecord | None:
    stmt = select(BreachRecord).where(BreachRecord.id == breach_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).one_or_none()


def insert(session: Session, **values: Any) -> BreachRecord:
    row = BreachRecord(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def insert_notice_if_new(session: Session, values: dict[str, Any]) -> uuid.UUID | None:
    """Insert a processor notice once per incident (``ON CONFLICT DO NOTHING``); the new id, or
    None when this school already holds the incident's notice."""
    stmt = (
        pg_insert(BreachRecord)
        .values(tenant_id=current_tenant_id(session), **values)
        .on_conflict_do_nothing(index_elements=["tenant_id", "platform_incident_id"])
        .returning(BreachRecord.id)
    )
    value = session.execute(stmt).scalar_one_or_none()
    return value


def update_record(session: Session, breach_id: uuid.UUID, **values: Any) -> BreachRecord:
    session.execute(
        update(BreachRecord)
        .where(BreachRecord.id == breach_id)
        .values(**values, version=BreachRecord.version + 1),
        execution_options={"synchronize_session": False},
    )
    return session.scalars(
        select(BreachRecord).where(BreachRecord.id == breach_id),
        execution_options={"populate_existing": True},
    ).one()


def set_ciphertext(session: Session, breach_id: uuid.UUID, column: str, blob: bytes) -> None:
    """Re-encryption only (DEK rotation): the row's version is not bumped."""
    if column not in CIPHERTEXT:
        raise ValueError(column)
    session.execute(
        update(BreachRecord).where(BreachRecord.id == breach_id).values({column: blob}),
        execution_options={"synchronize_session": False},
    )


def stale_ciphertexts(
    session: Session, target_version: int, limit: int
) -> list[tuple[uuid.UUID, str, bytes]]:
    """(id, column, blob) of breach texts under another key version than ``target_version``
    (bytes 2-3 of the AES-GCM header; app.core.crypto)."""
    out: list[tuple[uuid.UUID, str, bytes]] = []
    for column in CIPHERTEXT:
        if limit - len(out) <= 0:
            break
        col = getattr(BreachRecord, column)
        version = func.get_byte(col, 1) * 256 + func.get_byte(col, 2)
        stmt = (
            select(BreachRecord.id, col)
            .where(col.is_not(None), version != target_version)
            .order_by(BreachRecord.id)
            .limit(limit - len(out))
            .with_for_update(skip_locked=True)
        )
        out.extend((rid, column, bytes(blob)) for rid, blob in session.execute(stmt))
    return out


# --- affected -------------------------------------------------------------------------------


def affected(session: Session, breach_id: uuid.UUID) -> list[BreachAffected]:
    return list(
        session.scalars(
            select(BreachAffected)
            .where(BreachAffected.breach_id == breach_id)
            .order_by(BreachAffected.created_at, BreachAffected.id)
        )
    )


def breaches_affecting(
    session: Session,
    *,
    student_id: uuid.UUID | None = None,
    guardian_id: uuid.UUID | None = None,
    membership_id: uuid.UUID | None = None,
) -> list[BreachRecord]:
    """Breaches whose affected list names the subject, newest first (access report)."""
    cond = (
        BreachAffected.student_id == student_id
        if student_id is not None
        else BreachAffected.guardian_id == guardian_id
        if guardian_id is not None
        else BreachAffected.membership_id == membership_id
    )
    stmt = (
        select(BreachRecord)
        .join(BreachAffected, BreachAffected.breach_id == BreachRecord.id)
        .where(cond)
        .order_by(BreachRecord.noticed_at.desc(), BreachRecord.id.desc())
    )
    return list(session.scalars(stmt))


def affected_counts(
    session: Session, breach_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int, int]]:
    if not breach_ids:
        return {}
    stmt = (
        select(
            BreachAffected.breach_id,
            func.count(BreachAffected.student_id),
            func.count(BreachAffected.guardian_id),
            func.count(BreachAffected.membership_id),
        )
        .where(BreachAffected.breach_id.in_(list(breach_ids)))
        .group_by(BreachAffected.breach_id)
    )
    return {bid: (int(s), int(g), int(m)) for bid, s, g, m in session.execute(stmt)}


def replace_affected(
    session: Session,
    breach_id: uuid.UUID,
    *,
    student_ids: Sequence[uuid.UUID],
    guardian_ids: Sequence[uuid.UUID],
    membership_ids: Sequence[uuid.UUID],
) -> None:
    session.execute(delete(BreachAffected).where(BreachAffected.breach_id == breach_id))
    tenant_id = current_tenant_id(session)
    rows: list[dict[str, Any]] = []
    for column, ids in (
        ("student_id", student_ids),
        ("guardian_id", guardian_ids),
        ("membership_id", membership_ids),
    ):
        rows.extend(
            {"id": new_id(), "tenant_id": tenant_id, "breach_id": breach_id, column: i} for i in ids
        )
    for start in range(0, len(rows), 1000):
        session.execute(pg_insert(BreachAffected), rows[start : start + 1000])


# --- reminders ------------------------------------------------------------------------------


def insert_reminder_once(session: Session, breach_id: uuid.UUID, reminder: str) -> bool:
    stmt = (
        pg_insert(BreachReminder)
        .values(
            id=new_id(),
            tenant_id=current_tenant_id(session),
            breach_id=breach_id,
            reminder=reminder,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "breach_id", "reminder"])
        .returning(BreachReminder.id)
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def reminders_sent(
    session: Session, breach_ids: Collection[uuid.UUID]
) -> set[tuple[uuid.UUID, str]]:
    if not breach_ids:
        return set()
    stmt = select(BreachReminder.breach_id, BreachReminder.reminder).where(
        BreachReminder.breach_id.in_(list(breach_ids))
    )
    return {(b, r) for b, r in session.execute(stmt)}


# --- full data export -----------------------------------------------------------------------


def export_tables(session: Session, *, include_sensitive: bool) -> list[RecordTable]:
    """The school's breach register for its full data export (FR-DPDP-018). Ciphertext columns
    are always left out (the archive is plain data; the texts are C3 and readable only in the
    app); ``include_sensitive`` adds nothing more here."""
    del include_sensitive
    return [
        dump_table(
            session,
            BreachRecord.__table__,
            name="breach_records",
            exclude=CIPHERTEXT,
            order_by=("noticed_at", "id"),
        ),
        dump_table(
            session,
            BreachAffected.__table__,
            name="breach_affected",
            order_by=("breach_id", "id"),
        ),
        dump_table(
            session,
            BreachReminder.__table__,
            name="breach_reminders",
            order_by=("breach_id", "reminder"),
        ),
    ]
