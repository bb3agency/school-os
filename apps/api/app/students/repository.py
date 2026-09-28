"""Database access for students. Only ``app.students.service`` calls this module.

Every function runs in the caller's ``tenant_session``: RLS limits each statement to the current
school. Object-level scope (class/section) is decided by the service and passed in as explicit
section-id filters, so no query here returns a student outside the filters it was given.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Row,
    Text,
    and_,
    case,
    cast,
    delete,
    false,
    func,
    insert,
    literal,
    or_,
    select,
    text,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, aliased

from app.core.errors import Conflict, DomainError, NotFound, ValidationFailed
from app.students.models import (
    AttributeDefinition,
    AttributeValue,
    Enrollment,
    Guardian,
    PromotionItem,
    PromotionRun,
    Student,
    StudentGuardian,
    StudentProfile,
)

_CONSTRAINT_FIELDS: dict[str, str] = {
    "enrollments_section_fk": "section_id",
    "enrollments_academic_year_fk": "section_id",
    "enrollments_roll_no_length": "roll_no",
    "enrollments_dates_ordered": "started_on",
    "student_guardians_guardian_fk": "guardian_id",
    "attribute_values_verified_fields": "status",
    "students_admission_no_length": "value",
    "guardians_full_name_length": "full_name",
}
_DUPLICATE_MESSAGES: dict[str, str] = {
    "students_adm_no": "Another student already has this admission number.",
    "one_active_enrollment": "The student is already enrolled for this academic year.",
    "student_guardians_pkey": "This guardian is already linked to the student.",
    "student_guardians_one_primary": "The student already has a primary guardian.",
    "av_current": "Someone else recorded this value at the same time. Reload and try again.",
    "promotion_runs_one_committed": (
        "This year's students were already promoted. Undo that promotion first."
    ),
}
_DUPLICATE_CODES: dict[str, str] = {
    "students_adm_no": "duplicate_admission_no",
    "promotion_runs_one_committed": "promotion_already_committed",
}


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    """Map a PostgreSQL error to a domain error (never echoes values); ``None`` if unexpected."""
    orig = exc.orig
    state = getattr(orig, "sqlstate", None)
    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None) or ""
    if state == "23505":
        code = _DUPLICATE_CODES.get(constraint, "duplicate")
        return Conflict(_DUPLICATE_MESSAGES.get(constraint, "This already exists."), code=code)
    if constraint == "attribute_values_immutable":
        return Conflict("Recorded values cannot be changed.", code="value_immutable")
    if state in ("23503", "23514"):
        field = _CONSTRAINT_FIELDS.get(constraint, "body")
        code = "not_found" if state == "23503" else "invalid"
        return ValidationFailed([{"field": field, "code": code, "message_key": f"errors.{code}"}])
    return None


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set; use core.db.tenant_session()")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    """Transaction timestamp (consistent within one request)."""
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


# --- definitions ---------------------------------------------------------------------------


def list_definitions(session: Session) -> list[AttributeDefinition]:
    stmt = select(AttributeDefinition).order_by(
        AttributeDefinition.sort_order, AttributeDefinition.key
    )
    return list(session.scalars(stmt))


# --- students ------------------------------------------------------------------------------


def get_student(session: Session, student_id: uuid.UUID, *, lock: bool = False) -> Student | None:
    stmt = select(Student).where(Student.id == student_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def insert_student(
    session: Session, *, student_id: uuid.UUID, tenant_id: uuid.UUID, status: str
) -> Student:
    return session.scalars(
        insert(Student)
        .values(id=student_id, tenant_id=tenant_id, status=status)
        .returning(Student),
        execution_options={"populate_existing": True},
    ).one()


def update_student(
    session: Session,
    student_id: uuid.UUID,
    *,
    values: dict[str, Any],
    expected_version: int | None = None,
) -> Student | None:
    """Update + bump version; ``None`` when unknown or ``expected_version`` is stale."""
    stmt = update(Student).where(Student.id == student_id)
    if expected_version is not None:
        stmt = stmt.where(Student.version == expected_version)
    return session.scalars(
        stmt.values(**values, version=Student.version + 1).returning(Student),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()


def students_in_sections(
    session: Session,
    student_ids: Collection[uuid.UUID],
    *,
    academic_year_id: uuid.UUID,
    section_ids: Collection[uuid.UUID],
) -> set[uuid.UUID]:
    """Which of ``student_ids`` are actively enrolled in one of ``section_ids`` that year."""
    if not student_ids or not section_ids:
        return set()
    rows = session.scalars(
        select(Enrollment.student_id).where(
            Enrollment.student_id.in_(list(student_ids)),
            Enrollment.academic_year_id == academic_year_id,
            Enrollment.status == "active",
            Enrollment.section_id.in_(list(section_ids)),
        )
    )
    return set(rows)


def student_ids_in_sections(
    session: Session, *, academic_year_id: uuid.UUID, section_ids: Collection[uuid.UUID] | None
) -> list[uuid.UUID]:
    """Students actively enrolled this year (``section_ids`` None = every section)."""
    stmt = select(Enrollment.student_id).where(
        Enrollment.academic_year_id == academic_year_id, Enrollment.status == "active"
    )
    if section_ids is not None:
        if not section_ids:
            return []
        stmt = stmt.where(Enrollment.section_id.in_(list(section_ids)))
    return list(session.scalars(stmt.order_by(Enrollment.student_id)))


def all_student_ids(session: Session) -> list[uuid.UUID]:
    return list(session.scalars(select(Student.id).order_by(Student.id)))


def active_enrollments_of(session: Session, student_ids: Collection[uuid.UUID]) -> list[Enrollment]:
    """Active enrolments of ``student_ids`` in any academic year (one query)."""
    if not student_ids:
        return []
    stmt = select(Enrollment).where(
        Enrollment.student_id.in_(list(student_ids)), Enrollment.status == "active"
    )
    return list(session.scalars(stmt.order_by(Enrollment.student_id, Enrollment.created_at)))


def active_placements(session: Session) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Distinct (academic year, section) pairs that have an active enrolment (RLS: this school)."""
    stmt = (
        select(Enrollment.academic_year_id, Enrollment.section_id)
        .where(Enrollment.status == "active")
        .distinct()
    )
    return [(row.academic_year_id, row.section_id) for row in session.execute(stmt)]


def student_ids_with_batch(session: Session, batch_id: uuid.UUID) -> list[uuid.UUID]:
    """Students with at least one value recorded by import batch ``batch_id``."""
    stmt = (
        select(AttributeValue.student_id)
        .where(AttributeValue.import_batch_id == batch_id)
        .distinct()
        .order_by(AttributeValue.student_id)
    )
    return list(session.scalars(stmt))


# --- import revert (FR-IMP-005) ---------------------------------------------------------------
# Guarded by the database (migration 0012): a student can only be deleted while the import batch
# that created it is ``reverting`` in the same transaction; values are never deleted directly
# (``sos_app`` has no DELETE on ``sis.attribute_values``) but go with their student through the
# ``ON DELETE CASCADE`` foreign key.


def student_versions(session: Session, student_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not student_ids:
        return {}
    rows = session.execute(
        select(Student.id, Student.version).where(Student.id.in_(list(student_ids)))
    ).all()
    return {r.id: int(r.version) for r in rows}


def batch_values(session: Session, batch_id: uuid.UUID) -> list[AttributeValue]:
    """Every value recorded by import batch ``batch_id`` (current or not)."""
    stmt = (
        select(AttributeValue)
        .where(AttributeValue.import_batch_id == batch_id)
        .order_by(AttributeValue.id)
    )
    return list(session.scalars(stmt, execution_options={"populate_existing": True}))


def superseded_by_any(
    session: Session, successor_ids: Collection[uuid.UUID]
) -> list[AttributeValue]:
    """Values that one of ``successor_ids`` replaced."""
    if not successor_ids:
        return []
    stmt = (
        select(AttributeValue)
        .where(AttributeValue.superseded_by.in_(list(successor_ids)))
        .order_by(AttributeValue.id)
    )
    return list(session.scalars(stmt, execution_options={"populate_existing": True}))


def reject_current_values(
    session: Session, value_ids: Collection[uuid.UUID], user_id: uuid.UUID
) -> list[AttributeValue]:
    """Mark current values ``rejected`` (kept in history, never canonical); returns them."""
    if not value_ids:
        return []
    return list(
        session.scalars(
            update(AttributeValue)
            .where(AttributeValue.id.in_(list(value_ids)), AttributeValue.superseded_by.is_(None))
            .values(verification_status="rejected", verified_by=user_id, verified_at=func.now())
            .returning(AttributeValue),
            execution_options={"populate_existing": True, "synchronize_session": False},
        )
    )


def delete_students(session: Session, student_ids: Collection[uuid.UUID]) -> int:
    """Delete students (enrolments first; values, profile and derived rows cascade). The
    database refuses unless an import batch that created them is ``reverting``."""
    if not student_ids:
        return 0
    ids = list(student_ids)
    session.execute(
        delete(Enrollment).where(Enrollment.student_id.in_(ids)),
        execution_options={"synchronize_session": False},
    )
    result = session.execute(
        delete(Student).where(Student.id.in_(ids)).returning(Student.id),
        execution_options={"synchronize_session": False},
    )
    return len(result.all())


# --- enrolments -----------------------------------------------------------------------------


def active_enrollment(
    session: Session, student_id: uuid.UUID, academic_year_id: uuid.UUID, *, lock: bool = False
) -> Enrollment | None:
    stmt = select(Enrollment).where(
        Enrollment.student_id == student_id,
        Enrollment.academic_year_id == academic_year_id,
        Enrollment.status == "active",
    )
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def latest_active_section(session: Session, student_id: uuid.UUID) -> uuid.UUID | None:
    """Section of the most recently created active enrolment (profile projection)."""
    value: uuid.UUID | None = session.scalars(
        select(Enrollment.section_id)
        .where(Enrollment.student_id == student_id, Enrollment.status == "active")
        .order_by(Enrollment.created_at.desc(), Enrollment.id.desc())
        .limit(1)
    ).first()
    return value


def insert_enrollment(session: Session, **values: Any) -> Enrollment:
    return session.scalars(
        insert(Enrollment).values(**values).returning(Enrollment),
        execution_options={"populate_existing": True},
    ).one()


def end_enrollment(
    session: Session, enrollment_id: uuid.UUID, *, status: str, ended_on: dt.date
) -> Enrollment:
    return session.scalars(
        update(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .values(status=status, ended_on=ended_on, version=Enrollment.version + 1)
        .returning(Enrollment),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one()


def enrollments_of(session: Session, student_id: uuid.UUID) -> list[Enrollment]:
    """Every enrolment of one student, newest first."""
    stmt = (
        select(Enrollment)
        .where(Enrollment.student_id == student_id)
        .order_by(
            Enrollment.started_on.desc().nulls_last(),
            Enrollment.created_at.desc(),
            Enrollment.id.desc(),
        )
    )
    return list(session.scalars(stmt, execution_options={"populate_existing": True}))


def get_enrollment(
    session: Session, student_id: uuid.UUID, enrollment_id: uuid.UUID, *, lock: bool = False
) -> Enrollment | None:
    stmt = select(Enrollment).where(
        Enrollment.id == enrollment_id, Enrollment.student_id == student_id
    )
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def update_enrollment(
    session: Session, enrollment_id: uuid.UUID, *, expected_version: int, values: dict[str, Any]
) -> Enrollment | None:
    """Update + bump version; ``None`` when ``expected_version`` is stale."""
    return session.scalars(
        update(Enrollment)
        .where(Enrollment.id == enrollment_id, Enrollment.version == expected_version)
        .values(**values, version=Enrollment.version + 1)
        .returning(Enrollment),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()


# --- promotions (FR-TEN-011) ---------------------------------------------------------------


def year_enrollments(
    session: Session, academic_year_id: uuid.UUID, *, lock: bool = False
) -> list[tuple[Enrollment, str]]:
    """Active enrolments of one academic year with each student's record status."""
    stmt = (
        select(Enrollment, Student.status)
        .join(
            Student,
            and_(Student.tenant_id == Enrollment.tenant_id, Student.id == Enrollment.student_id),
        )
        .where(Enrollment.academic_year_id == academic_year_id, Enrollment.status == "active")
        .order_by(Enrollment.section_id, Enrollment.student_id)
    )
    if lock:
        stmt = stmt.with_for_update(of=Enrollment)
    rows = session.execute(stmt, execution_options={"populate_existing": True}).all()
    return [(e, status) for e, status in rows]


def students_active_in_year(
    session: Session, student_ids: Collection[uuid.UUID], academic_year_id: uuid.UUID
) -> set[uuid.UUID]:
    if not student_ids:
        return set()
    stmt = select(Enrollment.student_id).where(
        Enrollment.student_id.in_(list(student_ids)),
        Enrollment.academic_year_id == academic_year_id,
        Enrollment.status == "active",
    )
    return set(session.scalars(stmt))


def close_enrollments(
    session: Session, enrollment_ids: Collection[uuid.UUID], *, ended_on: dt.date
) -> dict[uuid.UUID, int]:
    """Mark enrolments ``completed`` on ``ended_on`` (never before their start); new versions."""
    if not enrollment_ids:
        return {}
    end = case(
        (
            and_(Enrollment.started_on.is_not(None), Enrollment.started_on > ended_on),
            Enrollment.started_on,
        ),
        else_=literal(ended_on),
    )
    rows = session.execute(
        update(Enrollment)
        .where(Enrollment.id.in_(list(enrollment_ids)), Enrollment.status == "active")
        .values(status="completed", ended_on=end, version=Enrollment.version + 1)
        .returning(Enrollment.id, Enrollment.version),
        execution_options={"synchronize_session": False},
    ).all()
    return {r.id: r.version for r in rows}


def insert_enrollments(session: Session, rows: Sequence[dict[str, Any]]) -> dict[uuid.UUID, int]:
    """Insert many enrolments; returns ``{enrollment_id: version}``."""
    if not rows:
        return {}
    out = session.execute(
        insert(Enrollment).values(list(rows)).returning(Enrollment.id, Enrollment.version),
        execution_options={"synchronize_session": False},
    ).all()
    return {r.id: r.version for r in out}


def enrollment_states(
    session: Session, enrollment_ids: Collection[uuid.UUID], *, lock: bool = False
) -> dict[uuid.UUID, tuple[str, int]]:
    """``{enrollment_id: (status, version)}`` for the enrolments that still exist."""
    if not enrollment_ids:
        return {}
    stmt = select(Enrollment.id, Enrollment.status, Enrollment.version).where(
        Enrollment.id.in_(list(enrollment_ids))
    )
    if lock:
        stmt = stmt.with_for_update()
    return {r.id: (r.status, r.version) for r in session.execute(stmt)}


def enrollment_section_ids(
    session: Session, enrollment_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """The distinct sections of these enrolments."""
    if not enrollment_ids:
        return set()
    stmt = select(Enrollment.section_id).where(Enrollment.id.in_(list(enrollment_ids))).distinct()
    return set(session.scalars(stmt))


def delete_enrollments(session: Session, enrollment_ids: Collection[uuid.UUID]) -> int:
    if not enrollment_ids:
        return 0
    result = session.execute(
        delete(Enrollment).where(Enrollment.id.in_(list(enrollment_ids))).returning(Enrollment.id),
        execution_options={"synchronize_session": False},
    )
    return len(result.all())


def reopen_enrollments(session: Session, enrollment_ids: Collection[uuid.UUID]) -> int:
    if not enrollment_ids:
        return 0
    result = session.execute(
        update(Enrollment)
        .where(Enrollment.id.in_(list(enrollment_ids)), Enrollment.status == "completed")
        .values(status="active", ended_on=None, version=Enrollment.version + 1)
        .returning(Enrollment.id),
        execution_options={"synchronize_session": False},
    )
    return len(result.all())


def student_statuses(
    session: Session, student_ids: Collection[uuid.UUID], *, lock: bool = False
) -> dict[uuid.UUID, str]:
    if not student_ids:
        return {}
    stmt = select(Student.id, Student.status).where(Student.id.in_(list(student_ids)))
    if lock:
        stmt = stmt.with_for_update()
    return {r.id: r.status for r in session.execute(stmt)}


def set_student_status(session: Session, student_ids: Collection[uuid.UUID], status: str) -> None:
    if not student_ids:
        return
    session.execute(
        update(Student)
        .where(Student.id.in_(list(student_ids)))
        .values(status=status, version=Student.version + 1),
        execution_options={"synchronize_session": False},
    )


def bump_student_versions(session: Session, student_ids: Collection[uuid.UUID]) -> None:
    """New ETag for students whose enrolments changed (the student view shows the enrolment)."""
    if not student_ids:
        return
    session.execute(
        update(Student)
        .where(Student.id.in_(list(student_ids)))
        .values(version=Student.version + 1),
        execution_options={"synchronize_session": False},
    )


def refresh_placements(session: Session, student_ids: Collection[uuid.UUID]) -> None:
    """Set the profile's current section (latest active enrolment) and status for many
    students in one statement (same rule as :func:`latest_active_section`)."""
    if not student_ids:
        return
    latest = (
        select(Enrollment.section_id)
        .where(
            Enrollment.tenant_id == StudentProfile.tenant_id,
            Enrollment.student_id == StudentProfile.student_id,
            Enrollment.status == "active",
        )
        .order_by(Enrollment.created_at.desc(), Enrollment.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    session.execute(
        update(StudentProfile)
        .where(
            StudentProfile.student_id.in_(list(student_ids)),
            Student.tenant_id == StudentProfile.tenant_id,
            Student.id == StudentProfile.student_id,
        )
        .values(current_section_id=latest, status=Student.status, updated_at=func.now()),
        execution_options={"synchronize_session": False},
    )


def committed_promotion(
    session: Session, from_academic_year_id: uuid.UUID, *, lock: bool = False
) -> PromotionRun | None:
    stmt = select(PromotionRun).where(
        PromotionRun.from_academic_year_id == from_academic_year_id,
        PromotionRun.status == "committed",
    )
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def promotions_from(session: Session, from_academic_year_id: uuid.UUID) -> list[PromotionRun]:
    stmt = (
        select(PromotionRun)
        .where(PromotionRun.from_academic_year_id == from_academic_year_id)
        .order_by(PromotionRun.committed_at.desc(), PromotionRun.id.desc())
    )
    return list(session.scalars(stmt, execution_options={"populate_existing": True}))


def insert_promotion(
    session: Session, run: dict[str, Any], items: Sequence[dict[str, Any]]
) -> PromotionRun:
    row = session.scalars(
        insert(PromotionRun).values(**run).returning(PromotionRun),
        execution_options={"populate_existing": True},
    ).one()
    if items:
        session.execute(insert(PromotionItem).values(list(items)))
    return row


def promotion_items(session: Session, run_id: uuid.UUID) -> list[PromotionItem]:
    stmt = select(PromotionItem).where(PromotionItem.run_id == run_id)
    return list(session.scalars(stmt.order_by(PromotionItem.student_id)))


def mark_promotion_undone(
    session: Session, run_id: uuid.UUID, *, undone_by: uuid.UUID | None
) -> PromotionRun:
    return session.scalars(
        update(PromotionRun)
        .where(PromotionRun.id == run_id)
        .values(
            status="undone",
            undone_by=undone_by,
            undone_at=func.now(),
            version=PromotionRun.version + 1,
        )
        .returning(PromotionRun),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one()


# --- attribute values ----------------------------------------------------------------------


def current_values(
    session: Session,
    student_ids: Collection[uuid.UUID],
    attribute_keys: Collection[str] | None = None,
) -> list[AttributeValue]:
    if not student_ids:
        return []
    stmt = select(AttributeValue).where(
        AttributeValue.student_id.in_(list(student_ids)), AttributeValue.superseded_by.is_(None)
    )
    if attribute_keys is not None:
        stmt = stmt.where(AttributeValue.attribute_key.in_(list(attribute_keys)))
    return list(session.scalars(stmt.order_by(AttributeValue.recorded_at, AttributeValue.id)))


def current_value(
    session: Session, student_id: uuid.UUID, attribute_key: str, source: str
) -> AttributeValue | None:
    return session.scalars(
        select(AttributeValue).where(
            AttributeValue.student_id == student_id,
            AttributeValue.attribute_key == attribute_key,
            AttributeValue.source == source,
            AttributeValue.superseded_by.is_(None),
        ),
        execution_options={"populate_existing": True},
    ).one_or_none()


def get_value(session: Session, value_id: uuid.UUID) -> AttributeValue | None:
    return session.scalars(
        select(AttributeValue).where(AttributeValue.id == value_id),
        execution_options={"populate_existing": True},
    ).one_or_none()


def value_history(
    session: Session, student_id: uuid.UUID, attribute_key: str | None
) -> list[AttributeValue]:
    stmt = select(AttributeValue).where(AttributeValue.student_id == student_id)
    if attribute_key is not None:
        stmt = stmt.where(AttributeValue.attribute_key == attribute_key)
    stmt = stmt.order_by(
        AttributeValue.attribute_key, AttributeValue.recorded_at.desc(), AttributeValue.id.desc()
    )
    return list(session.scalars(stmt))


def insert_value(session: Session, **values: Any) -> AttributeValue:
    return session.scalars(
        insert(AttributeValue).values(**values).returning(AttributeValue),
        execution_options={"populate_existing": True},
    ).one()


def mark_superseded(session: Session, value_id: uuid.UUID, successor_id: uuid.UUID) -> None:
    session.execute(
        update(AttributeValue)
        .where(AttributeValue.id == value_id, AttributeValue.superseded_by.is_(None))
        .values(superseded_by=successor_id),
        execution_options={"synchronize_session": False},
    )


def set_verification(
    session: Session,
    value_id: uuid.UUID,
    *,
    status: str,
    by: uuid.UUID | None,
    at: dt.datetime | None,
) -> AttributeValue:
    return session.scalars(
        update(AttributeValue)
        .where(AttributeValue.id == value_id)
        .values(verification_status=status, verified_by=by, verified_at=at)
        .returning(AttributeValue),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one()


# --- profile projection ----------------------------------------------------------------------


def upsert_profile(session: Session, *, search_doc: str, **values: Any) -> None:
    tsv = func.to_tsvector(literal("simple", type_=REGCONFIG), cast(search_doc, Text))
    row = {**values, "search_tsv": tsv}
    stmt = pg_insert(StudentProfile).values(**row)
    update_cols: dict[str, Any] = {
        k: stmt.excluded[k] for k in row if k not in ("tenant_id", "student_id")
    }
    update_cols["updated_at"] = func.now()
    session.execute(
        stmt.on_conflict_do_update(index_elements=["tenant_id", "student_id"], set_=update_cols),
        execution_options={"synchronize_session": False},
    )


def get_profile(session: Session, student_id: uuid.UUID) -> StudentProfile | None:
    return session.scalars(
        select(StudentProfile).where(StudentProfile.student_id == student_id),
        execution_options={"populate_existing": True},
    ).one_or_none()


# --- search ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchSpec:
    academic_year_id: uuid.UUID | None
    allowed_sections: frozenset[uuid.UUID] | None  # None = whole school
    section_filter: frozenset[uuid.UUID] | None  # None = no section filter
    status: str | None
    admission_no: str | None
    name_key: str
    name_phonetic: str
    admission_terms: tuple[str, ...]
    threshold: float
    parent_weight: float
    admission_bonus: float
    offset: int
    limit: int


def _ws(query: str, column: Any) -> ColumnElement[float]:
    return func.word_similarity(literal(query), func.coalesce(column, ""))


def search(session: Session, spec: SearchSpec) -> list[Row[Any]]:
    """Ranked, scoped student search (FR-STU-010/011). Returns ``limit + 1`` rows at most."""
    p = StudentProfile
    e = aliased(Enrollment)
    year_match: ColumnElement[bool] = (
        e.academic_year_id == spec.academic_year_id
        if spec.academic_year_id is not None
        else false()
    )
    conditions: list[ColumnElement[bool]] = []
    score: ColumnElement[Any] = literal(0.0)
    match_field: ColumnElement[Any] = literal(None)
    if spec.allowed_sections is not None:
        conditions.append(e.section_id.in_(list(spec.allowed_sections)))
    if spec.section_filter is not None:
        conditions.append(e.section_id.in_(list(spec.section_filter)))
    if spec.status is not None:
        conditions.append(Student.status == spec.status)
    if spec.admission_no is not None:
        conditions.append(func.upper(Student.admission_no) == spec.admission_no.upper())
    if spec.admission_terms:
        adm = func.upper(func.coalesce(Student.admission_no, ""))
        conditions.append(or_(*[func.starts_with(adm, t) for t in spec.admission_terms]))
        exact = or_(*[adm == t for t in spec.admission_terms])
        score = score + case((exact, spec.admission_bonus), else_=0.5)
        match_field = literal("admission_no")
    if spec.name_key:
        s_name = func.greatest(
            _ws(spec.name_key, p.full_name_norm), _ws(spec.name_phonetic, p.full_name_translit)
        )
        s_father = _ws(spec.name_key, p.father_name_norm)
        s_mother = _ws(spec.name_key, p.mother_name_norm)
        t = spec.threshold
        conditions.append(or_(s_name >= t, s_father >= t, s_mother >= t))
        score = score + func.greatest(
            s_name, spec.parent_weight * s_father, spec.parent_weight * s_mother
        )
        match_field = case(
            (s_name >= t, "full_name"),
            (s_father >= t, "father_name"),
            else_="mother_name",
        )
    ranked = bool(spec.name_key or spec.admission_terms)
    order = (
        [score.desc(), p.full_name_norm.asc().nulls_last(), Student.id]
        if ranked
        else [p.full_name_norm.asc().nulls_last(), Student.id]
    )
    stmt = (
        select(
            Student.id,
            Student.admission_no,
            Student.status,
            p.full_name,
            e.section_id,
            score.label("score"),
            match_field.label("match_field"),
        )
        .select_from(Student)
        .join(p, and_(p.tenant_id == Student.tenant_id, p.student_id == Student.id))
        .outerjoin(
            e,
            and_(
                e.tenant_id == Student.tenant_id,
                e.student_id == Student.id,
                e.status == "active",
                year_match,
            ),
        )
        .where(and_(true(), *conditions))
        .order_by(*order)
        .offset(spec.offset)
        .limit(spec.limit + 1)
    )
    return list(session.execute(stmt).all())


# --- guardians -------------------------------------------------------------------------------


def insert_guardian(session: Session, **values: Any) -> Guardian:
    return session.scalars(
        insert(Guardian).values(**values).returning(Guardian),
        execution_options={"populate_existing": True},
    ).one()


def get_guardian(session: Session, guardian_id: uuid.UUID) -> Guardian | None:
    return session.scalars(
        select(Guardian).where(Guardian.id == guardian_id),
        execution_options={"populate_existing": True},
    ).one_or_none()


def update_guardian(
    session: Session, guardian_id: uuid.UUID, *, expected_version: int, values: dict[str, Any]
) -> Guardian | None:
    return session.scalars(
        update(Guardian)
        .where(Guardian.id == guardian_id, Guardian.version == expected_version)
        .values(**values, version=Guardian.version + 1)
        .returning(Guardian),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()


def link_guardian(session: Session, **values: Any) -> None:
    session.execute(insert(StudentGuardian).values(**values))


def get_link(
    session: Session, student_id: uuid.UUID, guardian_id: uuid.UUID
) -> StudentGuardian | None:
    return session.scalars(
        select(StudentGuardian).where(
            StudentGuardian.student_id == student_id, StudentGuardian.guardian_id == guardian_id
        ),
        execution_options={"populate_existing": True},
    ).one_or_none()


def update_link(
    session: Session, student_id: uuid.UUID, guardian_id: uuid.UUID, values: dict[str, Any]
) -> None:
    session.execute(
        update(StudentGuardian)
        .where(StudentGuardian.student_id == student_id, StudentGuardian.guardian_id == guardian_id)
        .values(**values),
        execution_options={"synchronize_session": False},
    )


def clear_primary(session: Session, student_id: uuid.UUID) -> None:
    session.execute(
        update(StudentGuardian)
        .where(StudentGuardian.student_id == student_id, StudentGuardian.is_primary)
        .values(is_primary=False),
        execution_options={"synchronize_session": False},
    )


def guardians_of(session: Session, student_id: uuid.UUID) -> list[tuple[Guardian, StudentGuardian]]:
    rows = session.execute(
        select(Guardian, StudentGuardian)
        .join(
            StudentGuardian,
            and_(
                StudentGuardian.tenant_id == Guardian.tenant_id,
                StudentGuardian.guardian_id == Guardian.id,
            ),
        )
        .where(StudentGuardian.student_id == student_id)
        .order_by(StudentGuardian.is_primary.desc(), StudentGuardian.created_at, Guardian.id)
    ).all()
    return [(g, link) for g, link in rows]


def unlink_guardian(session: Session, student_id: uuid.UUID, guardian_id: uuid.UUID) -> bool:
    result = session.execute(
        delete(StudentGuardian)
        .where(StudentGuardian.student_id == student_id, StudentGuardian.guardian_id == guardian_id)
        .returning(StudentGuardian.guardian_id),
        execution_options={"synchronize_session": False},
    )
    return result.first() is not None


def delete_guardian(session: Session, guardian_id: uuid.UUID) -> bool:
    """Delete a guardian no student is linked to any more (its C3 phone/address go with it)."""
    result = session.execute(
        delete(Guardian).where(Guardian.id == guardian_id).returning(Guardian.id),
        execution_options={"synchronize_session": False},
    )
    return result.first() is not None


def guardian_student_ids(session: Session, guardian_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        session.scalars(
            select(StudentGuardian.student_id).where(StudentGuardian.guardian_id == guardian_id)
        )
    )


def ensure_found[T](value: T | None, what: str) -> T:
    if value is None:
        raise NotFound(f"{what} not found")
    return value


def unique_ids(ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    return list(dict.fromkeys(ids))
