"""Database access for certificates and registers. Only ``app.certificates.service`` calls this
module.

Every function runs in the caller's ``tenant_session``: RLS limits each statement to the current
school. Object-level scope (which students the caller reaches) is decided by the service and
passed in as an explicit student-id filter.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import and_, false, func, insert, or_, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.certificates.models import Certificate, CertificateCounter, CertificateVerification
from app.core.errors import Conflict, DomainError, Forbidden, ValidationFailed
from app.core.ids import new_id
from app.core.record_tables import dump_table
from app.core.records import RecordTable

_FIELDS: dict[str, str] = {
    "certificates_decision": "reason",
    "certificates_cancelled_fields": "reason",
    "certificates_duplicate_fields": "reason",
    "certificates_student_fk": "student_id",
    "certificates_type_check": "certificate_type",
}

C = Certificate.__table__


_CONFLICTS: dict[str, tuple[str, str]] = {
    "certificates_one_live_tc": (
        "transfer_certificate_exists",
        "This student already has a transfer certificate that is issued or waiting for "
        "approval. Open it instead of preparing a new one.",
    ),
    "certificates_one_pending_duplicate": (
        "duplicate_pending",
        "A duplicate of this certificate is already waiting for approval.",
    ),
    "certificates_frozen": (
        "certificate_not_pending",
        "This certificate was already decided or issued. Reload to see its status.",
    ),
    "certificates_append_only": (
        "certificate_not_pending",
        "This certificate was already decided or issued. Reload to see its status.",
    ),
    # Should never happen (the counter row is locked); refuse rather than reuse a number.
    "certificates_serial_unique": (
        "serial_number_conflict",
        "The serial number is already used. Try again.",
    ),
    "certificates_serial_text_unique": (
        "serial_number_conflict",
        "The serial number is already used. Try again.",
    ),
    "certificates_duplicate_no_unique": (
        "serial_number_conflict",
        "The copy number is already used. Try again.",
    ),
    # R2 (0056): one live code per certificate; codes and withdrawals never change.
    "certificate_verifications_one_live": (
        "verification_exists",
        "This certificate already has a verification code. Reload to see it.",
    ),
    "certificate_verifications_fixed": (
        "verification_not_active",
        "This verification code was already withdrawn. Reload to see it.",
    ),
}


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    """Map a PostgreSQL error to a domain error (never echoes values); ``None`` if unexpected."""
    orig = exc.orig
    state = getattr(orig, "sqlstate", None)
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None) or ""
    if constraint == "certificates_maker_checker":
        return Forbidden(
            "You prepared this certificate, so someone else must decide it.",
            code="self_approval_forbidden",
        )
    if constraint in _CONFLICTS:
        code, message = _CONFLICTS[constraint]
        return Conflict(message, code=code)
    if state in ("23503", "23514") and constraint in _FIELDS:
        return ValidationFailed(
            [{"field": _FIELDS[constraint], "code": "invalid", "message_key": "errors.invalid"}]
        )
    return None


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def insert_certificate(session: Session, **values: Any) -> Certificate:
    return session.scalars(insert(Certificate).values(**values).returning(Certificate)).one()


def get_certificate(
    session: Session, certificate_id: uuid.UUID, *, lock: bool = False
) -> Certificate | None:
    stmt = select(Certificate).where(Certificate.id == certificate_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def update_certificate(
    session: Session, certificate_id: uuid.UUID, values: Mapping[str, Any]
) -> Certificate:
    stmt = (
        update(Certificate)
        .where(Certificate.id == certificate_id)
        .values(**values, version=Certificate.version + 1)
        .returning(Certificate)
        .execution_options(synchronize_session=False)
    )
    return session.scalars(stmt, execution_options={"populate_existing": True}).one()


def list_certificates(
    session: Session,
    *,
    student_ids: Collection[uuid.UUID] | None,
    student_id: uuid.UUID | None = None,
    certificate_type: str | None = None,
    status: str | None = None,
    academic_year_id: uuid.UUID | None = None,
    after: tuple[dt.datetime, uuid.UUID] | None = None,
    limit: int = 50,
) -> list[Certificate]:
    """Newest first; ``student_ids`` ``None`` = the whole school, else only these students."""
    stmt = select(Certificate)
    if student_ids is not None:
        ids = list(student_ids)
        stmt = stmt.where(Certificate.student_id.in_(ids) if ids else false())
    if student_id is not None:
        stmt = stmt.where(Certificate.student_id == student_id)
    if certificate_type is not None:
        stmt = stmt.where(Certificate.certificate_type == certificate_type)
    if status is not None:
        stmt = stmt.where(Certificate.status == status)
    if academic_year_id is not None:
        stmt = stmt.where(Certificate.academic_year_id == academic_year_id)
    if after is not None:
        at, last = after
        stmt = stmt.where(
            or_(
                Certificate.requested_at < at,
                and_(Certificate.requested_at == at, Certificate.id < last),
            )
        )
    stmt = stmt.order_by(Certificate.requested_at.desc(), Certificate.id.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


def allocate_serial(session: Session, certificate_type: str, academic_year_id: uuid.UUID) -> int:
    """The next serial number of (school, type, year), in the caller's transaction.

    ``INSERT ... ON CONFLICT DO UPDATE`` locks the counter row until commit: a concurrent issue
    waits and gets the following number; a rollback releases this one (FR-CERT-006)."""
    stmt = text(
        """
        INSERT INTO sis.certificate_counters
          (id, tenant_id, certificate_type, academic_year_id, last_no)
        VALUES (:id, core.current_tenant(), :t, :y, 1)
        ON CONFLICT (tenant_id, certificate_type, academic_year_id)
        DO UPDATE SET last_no = sis.certificate_counters.last_no + 1, updated_at = now()
        RETURNING last_no
        """
    )
    value: int = session.execute(
        stmt, {"id": new_id(), "t": certificate_type, "y": academic_year_id}
    ).scalar_one()
    return value


def next_duplicate_no(session: Session, original_id: uuid.UUID) -> int:
    """Copy number of the next duplicate (the caller holds the original's row lock)."""
    value: int | None = session.execute(
        select(func.max(Certificate.duplicate_no)).where(
            Certificate.original_certificate_id == original_id
        )
    ).scalar_one()
    return (value or 0) + 1


def register_entries(
    session: Session,
    certificate_types: Collection[str],
    academic_year_id: uuid.UUID,
    *,
    limit: int,
) -> list[Certificate]:
    """Issued (and cancelled) certificates of a year: register lines, in issue order."""
    stmt = (
        select(Certificate)
        .where(
            Certificate.certificate_type.in_(list(certificate_types)),
            Certificate.academic_year_id == academic_year_id,
            Certificate.issued_at.is_not(None),
        )
        .order_by(Certificate.issued_at, Certificate.id)
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


def get_many(session: Session, ids: Collection[uuid.UUID]) -> dict[uuid.UUID, Certificate]:
    if not ids:
        return {}
    rows = session.execute(select(Certificate).where(Certificate.id.in_(list(ids)))).scalars()
    return {r.id: r for r in rows}


def transfer_certificates_of(
    session: Session, student_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Certificate]:
    """The issued (not cancelled) original TC of each student, if any."""
    if not student_ids:
        return {}
    rows = session.execute(
        select(Certificate).where(
            Certificate.student_id.in_(list(student_ids)),
            Certificate.certificate_type == "transfer",
            Certificate.original_certificate_id.is_(None),
            Certificate.status == "issued",
        )
    ).scalars()
    return {r.student_id: r for r in rows}


# --- verification codes (R2, 0056; ADR-0045) ------------------------------------------------

V = CertificateVerification


def insert_verification(session: Session, **values: Any) -> CertificateVerification:
    return session.scalars(insert(V).values(**values).returning(V)).one()


def verifications_of(
    session: Session, certificate_id: uuid.UUID, *, lock: bool = False
) -> list[CertificateVerification]:
    """Every code of one certificate, newest first."""
    stmt = select(V).where(V.certificate_id == certificate_id).order_by(V.created_at.desc())
    if lock:
        stmt = stmt.with_for_update()
    return list(session.scalars(stmt, execution_options={"populate_existing": True}).all())


def live_verifications(
    session: Session, certificate_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, CertificateVerification]:
    """The live (not withdrawn) code of each certificate that has one."""
    if not certificate_ids:
        return {}
    rows = session.scalars(
        select(V).where(V.certificate_id.in_(list(certificate_ids)), V.withdrawn_at.is_(None))
    )
    return {r.certificate_id: r for r in rows}


def verification_by_handle(
    session: Session, handle_id: uuid.UUID
) -> CertificateVerification | None:
    return session.scalars(select(V).where(V.handle_id == handle_id)).one_or_none()


def withdraw_verification(
    session: Session, verification_id: uuid.UUID, *, by: uuid.UUID, reason: str
) -> CertificateVerification:
    stmt = (
        update(V)
        .where(V.id == verification_id, V.withdrawn_at.is_(None))
        .values(
            withdrawn_at=func.now(),
            withdrawn_by=by,
            withdraw_reason=reason,
            version=V.version + 1,
        )
        .returning(V)
        .execution_options(synchronize_session=False)
    )
    return session.scalars(stmt, execution_options={"populate_existing": True}).one()


def count_check(session: Session, verification_id: uuid.UUID) -> None:
    """One more successful public lookup (the ETag ``version`` does not move: a check must not
    make the principal's withdrawal fail)."""
    session.execute(
        update(V)
        .where(V.id == verification_id)
        .values(check_count=V.check_count + 1, last_checked_at=func.now())
        .execution_options(synchronize_session=False)
    )


def export_tables(session: Session) -> list[RecordTable]:
    """The tables of the current school for its full data export (FR-ADM-001)."""
    return [
        dump_table(
            session,
            C,
            name="certificates",
            order_by=("requested_at", "id"),
        ),
        dump_table(
            session,
            CertificateCounter.__table__,
            name="certificate_counters",
            order_by=("certificate_type", "academic_year_id"),
        ),
        # The printed codes are the school's own record of what its papers carry; the handle's
        # key hash never leaves (docs/05 §15.6).
        dump_table(
            session,
            V.__table__,
            name="certificate_verifications",
            order_by=("created_at", "id"),
        ),
    ]


def certificates_of_student(
    session: Session, student_id: uuid.UUID, *, limit: int
) -> tuple[list[tuple[Certificate, int]], int]:
    """One student's certificates, newest request first, each with its total public check count
    (all codes), and how many the student has in all (data-principal report, FR-DPDP-009)."""
    checks = (
        select(func.coalesce(func.sum(V.check_count), 0))
        .where(V.certificate_id == Certificate.id)
        .correlate(Certificate)
        .scalar_subquery()
    )
    stmt = (
        select(Certificate, checks)
        .where(Certificate.student_id == student_id)
        .order_by(Certificate.requested_at.desc(), Certificate.id.desc())
        .limit(limit)
    )
    rows = [(c, int(n)) for c, n in session.execute(stmt).tuples()]
    total = session.scalar(select(func.count()).select_from(C).where(C.c.student_id == student_id))
    return rows, int(total or 0)


def certificate_ids_of_student(session: Session, student_id: uuid.UUID) -> list[uuid.UUID]:
    return list(session.scalars(select(C.c.id).where(C.c.student_id == student_id)))


__all__ = [
    "allocate_serial",
    "certificate_ids_of_student",
    "certificates_of_student",
    "count_check",
    "current_tenant_id",
    "export_tables",
    "get_certificate",
    "get_many",
    "insert_certificate",
    "insert_verification",
    "list_certificates",
    "live_verifications",
    "next_duplicate_no",
    "now",
    "register_entries",
    "transfer_certificates_of",
    "translate_db_error",
    "update_certificate",
    "verification_by_handle",
    "verifications_of",
    "withdraw_verification",
]
