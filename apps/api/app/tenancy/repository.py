"""Database access for tenancy. Only ``app.tenancy.service`` calls this module.

Every function takes the caller's session:
- tenant data: a ``core.db.tenant_session()`` (RLS limits every statement to that tenant);
- control-plane calls: a ``core.db.platform_session()`` which can only EXECUTE the allowlisted
  definer functions (it has no privileges on tenant tables).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any, Literal, cast

from sqlalchemy import Row, and_, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import (
    Conflict,
    DomainError,
    Forbidden,
    NotFound,
    ValidationFailed,
)
from app.tenancy.models import AcademicYear, SchoolClass, Section, Tenant, TenantKey

# FK/CHECK constraint -> API field, so errors say which input to fix (never echo values).
_CONSTRAINT_FIELDS: dict[str, str] = {
    "sections_class_fk": "class_id",
    "sections_academic_year_fk": "academic_year_id",
    "sections_class_teacher_fk": "class_teacher_membership_id",
    "sections_name_length": "name",
    "academic_years_dates_ordered": "ends_on",
    "academic_years_label_length": "label",
    "tenants_code_format": "code",
    "tenants_name_length": "name",
}
_DUPLICATE_MESSAGES: dict[str, str] = {
    "tenants_code_key": "A school with this code already exists.",
    "academic_years_tenant_id_label_key": "An academic year with this label already exists.",
    "one_current_year": "Another academic year is already marked as current.",
    "classes_tenant_id_code_key": "A class with this code already exists.",
    "sections_year_class_name_key": "This class already has a section with this name this year.",
}


# Archive guards (0023_api_gaps) -> 409 with a code the web can explain.
_ARCHIVE_CONFLICTS: dict[str, tuple[str, str]] = {
    "structure_active_enrolments": (
        "structure_in_use",
        "Students are still enrolled here. Move or close their enrolments first.",
    ),
    "academic_years_current_not_archived": (
        "academic_year_current",
        "The current academic year cannot be archived. Make another year current first.",
    ),
}


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    """Map a PostgreSQL error to a domain error (RFC 9457); ``None`` if it is unexpected."""
    diag = getattr(exc.orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None) or ""
    if constraint in _ARCHIVE_CONFLICTS:
        code, message = _ARCHIVE_CONFLICTS[constraint]
        return Conflict(message, code=code)
    return _translate_state(exc)


def _translate_state(exc: DBAPIError) -> DomainError | None:
    orig = exc.orig
    state = getattr(orig, "sqlstate", None)
    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None) or ""
    if state == "23505":
        return Conflict(
            _DUPLICATE_MESSAGES.get(constraint, "This already exists."), code="duplicate"
        )
    if state in ("23503", "23514"):
        field = _CONSTRAINT_FIELDS.get(constraint, "body")
        code = "not_found" if state == "23503" else "invalid"
        return ValidationFailed([{"field": field, "code": code, "message_key": f"errors.{code}"}])
    if state == "P0002":
        return NotFound()
    if state == "55000":
        return Conflict(getattr(diag, "message_primary", None) or None, code="invalid_state")
    if state == "42501":
        return Forbidden()
    return None


def current_tenant_id(session: Session) -> uuid.UUID:
    """The tenant bound to this transaction by ``tenant_session()``."""
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set; use core.db.tenant_session()")
    return uuid.UUID(str(value))


# --- control-plane definer calls (platform_session) -----------------------------------------


def call_provision_tenant(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    code: str,
    name: str,
    boards: Sequence[str],
    plan_tier: str,
    deployment_mode: str,
) -> uuid.UUID:
    value: object = session.execute(
        text("SELECT core.provision_tenant(:i, :c, :n, CAST(:b AS text[]), :p, :d)"),
        {
            "i": tenant_id,
            "c": code,
            "n": name,
            "b": list(boards),
            "p": plan_tier,
            "d": deployment_mode,
        },
    ).scalar_one()
    return uuid.UUID(str(value))


def call_set_tenant_status(session: Session, tenant_id: uuid.UUID, status: str) -> str:
    return str(
        session.execute(
            text("SELECT core.set_tenant_status(:t, :s)"), {"t": tenant_id, "s": status}
        ).scalar_one()
    )


def call_list_tenant_ids(session: Session, statuses: Sequence[str] | None) -> list[uuid.UUID]:
    rows: list[object] = list(
        session.execute(
            text("SELECT tenant_id FROM core.list_tenant_ids(CAST(:s AS text[]))"),
            {"s": list(statuses) if statuses is not None else None},
        ).scalars()
    )
    return [uuid.UUID(str(v)) for v in rows]


def call_tenant_usage_summary(session: Session, tenant_id: uuid.UUID) -> Row[Any]:
    return session.execute(
        text("SELECT * FROM core.tenant_usage_summary(:t)"), {"t": tenant_id}
    ).one()


# --- tenant row and keys (tenant_session) ---------------------------------------------------


def get_own_tenant(session: Session) -> Tenant | None:
    """The current tenant's row (RLS policy ``own_tenant`` hides all others)."""
    return session.scalars(select(Tenant)).one_or_none()


def lock_tenant_initialisation(session: Session) -> None:
    """Serialise ``initialise_tenant`` per tenant (FR-PLT-002): concurrent provisioning retries
    see one another's key and hook writes instead of racing on them."""
    session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended("
            "'core.tenant_keys:initialise:' || core.current_tenant()::text, 0))"
        )
    )


def lock_tenant_keys(session: Session) -> None:
    """Serialise key rotation and retirement per tenant (transaction-level advisory lock)."""
    session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended("
            "'core.tenant_keys:rotate:' || core.current_tenant()::text, 0))"
        )
    )


def db_now(session: Session) -> dt.datetime:
    """The transaction timestamp (``now()``), the clock ``created_at``/``retired_at`` use."""
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def retire_tenant_key(session: Session, key_version: int) -> TenantKey | None:
    """Set ``retired_at`` once (``sos_app`` may update only that column); ``None`` if absent."""
    return session.scalars(
        update(TenantKey)
        .where(TenantKey.key_version == key_version, TenantKey.retired_at.is_(None))
        .values(retired_at=func.now())
        .returning(TenantKey)
    ).one_or_none()


def list_tenant_keys(session: Session) -> list[TenantKey]:
    return list(session.scalars(select(TenantKey).order_by(TenantKey.key_version)))


def insert_tenant_key(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    key_version: int,
    wrapped_dek: bytes,
    wrapped_hmac: bytes,
    key_id: str,
) -> TenantKey:
    return session.scalars(
        insert(TenantKey)
        .values(
            tenant_id=tenant_id,
            key_version=key_version,
            wrapped_dek=wrapped_dek,
            wrapped_hmac=wrapped_hmac,
            kms_key_arn=key_id,
        )
        .returning(TenantKey)
    ).one()


def update_tenant_settings(
    session: Session, *, expected_version: int, settings: dict[str, Any]
) -> Tenant | None:
    """Replace the current tenant's settings (optimistic); ``None`` on a stale version.

    ``sos_app`` may update only ``name``, ``settings`` and ``version`` (0003 grants); RLS
    ``own_tenant`` limits the statement to the current tenant.
    """
    return session.scalars(
        update(Tenant)
        .where(Tenant.version == expected_version)
        .values(settings=settings, version=Tenant.version + 1)
        .returning(Tenant),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()


# --- academic years ------------------------------------------------------------------------


def lock_academic_structure(session: Session) -> None:
    """Serialise structure changes per tenant (current-year switch, overlap checks)."""
    session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended("
            "'core.academic_years:' || core.current_tenant()::text, 0))"
        )
    )


def list_academic_years(session: Session) -> list[AcademicYear]:
    return list(session.scalars(select(AcademicYear).order_by(AcademicYear.starts_on.desc())))


def get_academic_year(session: Session, year_id: uuid.UUID) -> AcademicYear | None:
    return session.get(AcademicYear, year_id, populate_existing=True)


def get_current_academic_year(session: Session) -> AcademicYear | None:
    return session.scalars(select(AcademicYear).where(AcademicYear.is_current)).one_or_none()


def academic_year_overlaps(
    session: Session, starts_on: dt.date, ends_on: dt.date, *, exclude_id: uuid.UUID | None = None
) -> bool:
    cond = and_(AcademicYear.starts_on <= ends_on, AcademicYear.ends_on >= starts_on)
    if exclude_id is not None:
        cond = and_(cond, AcademicYear.id != exclude_id)
    return session.execute(select(func.count()).where(cond)).scalar_one() > 0


def insert_academic_year(
    session: Session,
    *,
    year_id: uuid.UUID,
    tenant_id: uuid.UUID,
    label: str,
    starts_on: dt.date,
    ends_on: dt.date,
    is_current: bool,
) -> AcademicYear:
    return session.scalars(
        insert(AcademicYear)
        .values(
            id=year_id,
            tenant_id=tenant_id,
            label=label,
            starts_on=starts_on,
            ends_on=ends_on,
            is_current=is_current,
        )
        .returning(AcademicYear),
        execution_options={"populate_existing": True},
    ).one()


def update_academic_year(
    session: Session, year_id: uuid.UUID, *, expected_version: int, values: dict[str, Any]
) -> AcademicYear | None:
    """Optimistic update; ``None`` when the id is unknown or the version is stale."""
    return session.scalars(
        update(AcademicYear)
        .where(AcademicYear.id == year_id, AcademicYear.version == expected_version)
        .values(**values, version=AcademicYear.version + 1)
        .returning(AcademicYear),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()


def clear_current_academic_year(session: Session, *, except_id: uuid.UUID | None) -> None:
    stmt = update(AcademicYear).where(AcademicYear.is_current)
    if except_id is not None:
        stmt = stmt.where(AcademicYear.id != except_id)
    session.execute(
        stmt.values(is_current=False, version=AcademicYear.version + 1),
        execution_options={"synchronize_session": False},
    )


# --- classes -------------------------------------------------------------------------------


def list_classes(session: Session) -> list[SchoolClass]:
    return list(
        session.scalars(select(SchoolClass).order_by(SchoolClass.sort_order, SchoolClass.code))
    )


def get_class(session: Session, class_id: uuid.UUID) -> SchoolClass | None:
    return session.get(SchoolClass, class_id, populate_existing=True)


def insert_class(
    session: Session,
    *,
    class_id: uuid.UUID,
    tenant_id: uuid.UUID,
    code: str,
    display_en: str,
    display_te: str,
    sort_order: int,
) -> SchoolClass:
    return session.scalars(
        insert(SchoolClass)
        .values(
            id=class_id,
            tenant_id=tenant_id,
            code=code,
            display_en=display_en,
            display_te=display_te,
            sort_order=sort_order,
        )
        .returning(SchoolClass),
        execution_options={"populate_existing": True},
    ).one()


def insert_classes_if_missing(session: Session, rows: Sequence[dict[str, Any]]) -> int:
    """Insert classes whose code does not exist yet; return how many were added."""
    if not rows:
        return 0
    result = session.execute(
        pg_insert(SchoolClass)
        .values(list(rows))
        .on_conflict_do_nothing(index_elements=["tenant_id", "code"])
        .returning(SchoolClass.id)
    )
    return len(result.all())


def update_class(
    session: Session, class_id: uuid.UUID, *, expected_version: int, values: dict[str, Any]
) -> SchoolClass | None:
    return session.scalars(
        update(SchoolClass)
        .where(SchoolClass.id == class_id, SchoolClass.version == expected_version)
        .values(**values, version=SchoolClass.version + 1)
        .returning(SchoolClass),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()


# --- sections ------------------------------------------------------------------------------


def list_sections(
    session: Session,
    *,
    academic_year_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
) -> list[Section]:
    stmt = (
        select(Section)
        .join(
            SchoolClass,
            and_(SchoolClass.tenant_id == Section.tenant_id, SchoolClass.id == Section.class_id),
        )
        .order_by(SchoolClass.sort_order, Section.name)
    )
    if academic_year_id is not None:
        stmt = stmt.where(Section.academic_year_id == academic_year_id)
    if class_id is not None:
        stmt = stmt.where(Section.class_id == class_id)
    return list(session.scalars(stmt))


def get_section(session: Session, section_id: uuid.UUID) -> Section | None:
    return session.get(Section, section_id, populate_existing=True)


def share_lock_sections(
    session: Session, section_ids: Sequence[uuid.UUID]
) -> list[tuple[Section, SchoolClass, AcademicYear]]:
    """The sections with their class and academic year, all three rows locked ``FOR SHARE``
    (in section-id order) and read after the lock (latest committed state).

    ``FOR SHARE`` conflicts with the ``UPDATE`` that archives a row, so an enrolment write
    holding it and an archive are serialised: the archive's guard trigger then sees the
    committed enrolment, or the enrolment sees the committed ``archived_at``."""
    if not section_ids:
        return []
    stmt = (
        select(Section, SchoolClass, AcademicYear)
        .join(
            SchoolClass,
            and_(SchoolClass.tenant_id == Section.tenant_id, SchoolClass.id == Section.class_id),
        )
        .join(
            AcademicYear,
            and_(
                AcademicYear.tenant_id == Section.tenant_id,
                AcademicYear.id == Section.academic_year_id,
            ),
        )
        .where(Section.id.in_(list(section_ids)))
        .order_by(Section.id)
        .with_for_update(read=True, of=[Section, SchoolClass, AcademicYear])
        .execution_options(populate_existing=True)
    )
    return [(r[0], r[1], r[2]) for r in session.execute(stmt).all()]


def insert_section(
    session: Session,
    *,
    section_id: uuid.UUID,
    tenant_id: uuid.UUID,
    academic_year_id: uuid.UUID,
    class_id: uuid.UUID,
    name: str,
    class_teacher_membership_id: uuid.UUID | None,
) -> Section:
    return session.scalars(
        insert(Section)
        .values(
            id=section_id,
            tenant_id=tenant_id,
            academic_year_id=academic_year_id,
            class_id=class_id,
            name=name,
            class_teacher_membership_id=class_teacher_membership_id,
        )
        .returning(Section),
        execution_options={"populate_existing": True},
    ).one()


def update_section(
    session: Session, section_id: uuid.UUID, *, expected_version: int, values: dict[str, Any]
) -> Section | None:
    return session.scalars(
        update(Section)
        .where(Section.id == section_id, Section.version == expected_version)
        .values(**values, version=Section.version + 1)
        .returning(Section),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()


# --- archive (0023_api_gaps) ----------------------------------------------------------------

type StructureKind = Literal["academic_year", "class", "section"]
type StructureRow = AcademicYear | SchoolClass | Section
_STRUCTURE_MODELS: dict[str, type[AcademicYear] | type[SchoolClass] | type[Section]] = {
    "academic_year": AcademicYear,
    "class": SchoolClass,
    "section": Section,
}


def get_structure_row(
    session: Session, kind: StructureKind, row_id: uuid.UUID
) -> StructureRow | None:
    """One year, class or section of the current school (RLS), fresh from the database."""
    row = session.get(_STRUCTURE_MODELS[kind], row_id, populate_existing=True)
    return cast("StructureRow | None", row)


def set_archived(
    session: Session,
    kind: StructureKind,
    row_id: uuid.UUID,
    *,
    expected_version: int,
    archived: bool,
) -> StructureRow | None:
    """Archive (``archived_at = now()``) or unarchive a year, class or section (optimistic);
    ``None`` when the id is unknown or the version is stale. The database refuses to archive
    one with active enrolments (``structure_active_enrolments``) or the current year."""
    model = _STRUCTURE_MODELS[kind]
    value: ColumnElement[Any] | None = func.now() if archived else None
    row = session.scalars(
        update(model)
        .where(model.id == row_id, model.version == expected_version)
        .values(archived_at=value, version=model.version + 1)
        .returning(model),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()
    return cast("StructureRow | None", row)
