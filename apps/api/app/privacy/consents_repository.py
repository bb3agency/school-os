"""Database access for the consent register (0067_consents_breach; ADR-0049).

Callers inside ``app.privacy`` only. Every function runs in a ``core.db.tenant_session``: RLS
limits each statement to that school. Decisions are append-only for the app role (INSERT and
SELECT only); purposes may change only their text, version and ``active`` flag.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError, PreconditionFailed, ValidationFailed
from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.privacy.consents_models import Consent, ConsentPurpose


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("consents repository used outside tenant_session")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def _field(field: str, code: str) -> ValidationFailed:
    return ValidationFailed([{"field": field, "code": code, "message_key": f"errors.{code}"}])


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    """Constraint names -> API errors (the service validates first; these close races)."""
    message = str(exc.orig) if exc.orig is not None else str(exc)
    if "consents_seq_key" in message:
        return PreconditionFailed(
            "Someone else recorded a decision for this student just now. Reload and try again."
        )
    if "consents_evidence_fk" in message:
        return _field("evidence_document_id", "not_found")
    if "consents_guardian_fk" in message:
        return _field("guardian_id", "not_found")
    if "consent_purposes_key_key" in message:
        return Conflict("A purpose with this key already exists.", code="purpose_exists")
    if "consent_purposes_frozen" in message:
        return Conflict(
            "Families have answered this version. Save the change as a new version.",
            code="purpose_answered",
        )
    return None


# --- purposes -------------------------------------------------------------------------------


def list_purposes(session: Session, *, active_only: bool = False) -> list[ConsentPurpose]:
    stmt = select(ConsentPurpose).order_by(ConsentPurpose.created_at, ConsentPurpose.id)
    if active_only:
        stmt = stmt.where(ConsentPurpose.active.is_(True))
    return list(session.scalars(stmt))


def get_purpose(
    session: Session, purpose_id: uuid.UUID, *, lock: bool = False
) -> ConsentPurpose | None:
    stmt = select(ConsentPurpose).where(ConsentPurpose.id == purpose_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).one_or_none()


def purpose_by_key(session: Session, key: str) -> ConsentPurpose | None:
    return session.scalars(select(ConsentPurpose).where(ConsentPurpose.key == key)).one_or_none()


def insert_purpose(session: Session, **values: Any) -> ConsentPurpose:
    row = ConsentPurpose(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def update_purpose(session: Session, purpose_id: uuid.UUID, **values: Any) -> ConsentPurpose:
    session.execute(
        update(ConsentPurpose)
        .where(ConsentPurpose.id == purpose_id)
        .values(**values, version=ConsentPurpose.version + 1),
        execution_options={"synchronize_session": False},
    )
    return session.scalars(
        select(ConsentPurpose).where(ConsentPurpose.id == purpose_id),
        execution_options={"populate_existing": True},
    ).one()


def answered_versions(session: Session, purpose_id: uuid.UUID) -> set[int]:
    """Form versions of the purpose that at least one decision refers to."""
    return set(
        session.scalars(
            select(Consent.form_version).where(Consent.purpose_id == purpose_id).distinct()
        )
    )


def answered_counts(session: Session) -> dict[uuid.UUID, int]:
    """Number of decisions recorded per purpose (for the purposes screen)."""
    stmt = select(Consent.purpose_id, func.count()).group_by(Consent.purpose_id)
    return {pid: int(n) for pid, n in session.execute(stmt)}


# --- decisions ------------------------------------------------------------------------------


def history(
    session: Session, student_id: uuid.UUID, purpose_id: uuid.UUID | None = None
) -> list[Consent]:
    """Every decision recorded for the student (optionally one purpose), newest first."""
    stmt = select(Consent).where(Consent.student_id == student_id)
    if purpose_id is not None:
        stmt = stmt.where(Consent.purpose_id == purpose_id)
    return list(session.scalars(stmt.order_by(Consent.purpose_id, Consent.seq.desc())))


def current(
    session: Session, student_ids: Collection[uuid.UUID], purpose_id: uuid.UUID | None = None
) -> dict[tuple[uuid.UUID, uuid.UUID], Consent]:
    """The latest decision per (student, purpose) that has one."""
    if not student_ids:
        return {}
    stmt = select(Consent).where(Consent.student_id.in_(list(student_ids)))
    if purpose_id is not None:
        stmt = stmt.where(Consent.purpose_id == purpose_id)
    stmt = stmt.ext(distinct_on(Consent.student_id, Consent.purpose_id)).order_by(
        Consent.student_id, Consent.purpose_id, Consent.seq.desc()
    )
    return {(row.student_id, row.purpose_id): row for row in session.scalars(stmt)}


def insert(session: Session, **values: Any) -> Consent:
    row = Consent(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


# --- full data export -----------------------------------------------------------------------


def export_tables(session: Session) -> list[RecordTable]:
    """The school's purposes and consent register for its full data export (FR-DPDP-018)."""
    return [
        dump_table(session, ConsentPurpose.__table__, name="consent_purposes", order_by=("key",)),
        dump_table(
            session,
            Consent.__table__,
            name="consents",
            order_by=("student_id", "purpose_id", "seq"),
        ),
    ]
