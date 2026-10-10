"""Database access for the APAAR consent register (0051_apaar_consent_pen; ADR-0039).

Callers inside ``app.apaar`` only. Every function runs in a ``core.db.tenant_session``: RLS limits
each statement to that school and ``tenant_id`` is taken from the session's context. The register
is append-only for the app role (INSERT and SELECT only), so nothing here updates or deletes a
decision.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.apaar.models import ApaarConsent, ApaarConsentSettings
from app.core.errors import DomainError, PreconditionFailed, ValidationFailed
from app.core.record_tables import dump_table
from app.core.records import RecordTable


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("apaar repository used outside tenant_session")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    """Constraint names -> API errors (the service validates first; these close races)."""
    message = str(exc.orig) if exc.orig is not None else str(exc)
    if "apaar_consents_seq_key" in message:
        return PreconditionFailed(
            "Someone else recorded a decision for this student just now. Reload and try again."
        )
    if "apaar_consents_evidence_fk" in message:
        field = "evidence_document_id"
        return ValidationFailed(
            [{"field": field, "code": "not_found", "message_key": "errors.not_found"}]
        )
    if "apaar_consents_guardian_fk" in message:
        return ValidationFailed(
            [{"field": "guardian_id", "code": "not_found", "message_key": "errors.not_found"}]
        )
    return None


# --- consents -------------------------------------------------------------------------------


def history(session: Session, student_id: uuid.UUID) -> list[ApaarConsent]:
    """Every decision recorded for the student, newest first."""
    return list(
        session.scalars(
            select(ApaarConsent)
            .where(ApaarConsent.student_id == student_id)
            .order_by(ApaarConsent.seq.desc())
        )
    )


def current(session: Session, student_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, ApaarConsent]:
    """The latest decision of each student that has one (no entry: pending)."""
    if not student_ids:
        return {}
    stmt = (
        select(ApaarConsent)
        .where(ApaarConsent.student_id.in_(list(student_ids)))
        .distinct(ApaarConsent.student_id)
        .order_by(ApaarConsent.student_id, ApaarConsent.seq.desc())
    )
    return {row.student_id: row for row in session.scalars(stmt)}


def insert(session: Session, **values: Any) -> ApaarConsent:
    row = ApaarConsent(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def statuses(session: Session, student_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Current status per student that has a decision (bulk, for DQ and other modules)."""
    return {sid: row.status for sid, row in current(session, student_ids).items()}


# --- settings -------------------------------------------------------------------------------


def get_settings(session: Session, *, lock: bool = False) -> ApaarConsentSettings | None:
    stmt = select(ApaarConsentSettings)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).one_or_none()


def insert_settings(session: Session, **values: Any) -> ApaarConsentSettings:
    row = ApaarConsentSettings(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def update_settings(
    session: Session, settings_id: uuid.UUID, *, form_language: str, updated_by: uuid.UUID
) -> ApaarConsentSettings:
    session.execute(
        update(ApaarConsentSettings)
        .where(ApaarConsentSettings.id == settings_id)
        .values(
            form_language=form_language,
            updated_by=updated_by,
            version=ApaarConsentSettings.version + 1,
        ),
        execution_options={"synchronize_session": False},
    )
    row = session.scalars(
        select(ApaarConsentSettings).where(ApaarConsentSettings.id == settings_id),
        execution_options={"populate_existing": True},
    ).one()
    return row


# --- full data export -----------------------------------------------------------------------


def export_tables(session: Session) -> list[RecordTable]:
    """The school's consent register and setting for its full data export (FR-ADM-001)."""
    return [
        dump_table(
            session,
            ApaarConsent.__table__,
            name="apaar_consents",
            order_by=("student_id", "seq"),
        ),
        dump_table(session, ApaarConsentSettings.__table__, name="apaar_consent_settings"),
    ]
