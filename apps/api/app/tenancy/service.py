"""Tenancy public API: provisioning, lifecycle, keys and academic structure.

Other modules call only these functions. Routes live in ``tenancy.api`` and wrap each call
with ``require(...)``; the permission assumed is stated per function. Each mutation writes its
audit event here, in the caller's transaction (CLAUDE.md §6.7).

Requirements: FR-TEN-001, FR-TEN-002, FR-TEN-003, FR-TEN-010; US-201, US-202.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.config import get_settings
from app.core.crypto import KeyWrapper, generate_tenant_keys, get_key_wrapper
from app.core.db import platform_session, tenant_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context
from app.tenancy import repository as repo
from app.tenancy.schemas import (
    AcademicYearCreate,
    AcademicYearOut,
    AcademicYearUpdate,
    ClassCreate,
    ClassOut,
    ClassUpdate,
    SectionCreate,
    SectionOut,
    SectionUpdate,
    TenantOut,
    TenantProvisioned,
    TenantProvisionIn,
    TenantSettings,
    TenantSettingsPatch,
    TenantStatus,
    TenantStatusChange,
    TenantUsage,
)

# --- provisioning hooks ---------------------------------------------------------------------

PostProvisionHook = Callable[[Session, uuid.UUID], None]
"""Called as ``hook(session, tenant_id)`` inside the new tenant's ``tenant_session`` (sos_app).

Later modules append to :data:`POST_PROVISION_HOOKS` at import time (e.g. authz clones system
roles, audit creates the chain head). Hooks MUST be idempotent: :func:`initialise_tenant` may be
retried while the tenant is still ``provisioning``.
"""

POST_PROVISION_HOOKS: list[PostProvisionHook] = []

FIRST_KEY_VERSION = 1


@contextmanager
def _db_errors() -> Iterator[None]:
    """Translate expected PostgreSQL errors into RFC 9457 domain errors."""
    try:
        yield
    except DBAPIError as exc:
        mapped = repo.translate_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


def _audit(
    session: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    summary: dict[str, Any],
    system: bool = False,
) -> None:
    """Audit in the caller's transaction (CLAUDE.md §6.7); actor = the session's user."""
    request_id = get_context().get("request_id")
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        actor_type="system" if system else "user",
        request_id=request_id if isinstance(request_id, str) else None,
    )


def _validation_failed(exc: ValidationError) -> ValidationFailed:
    return ValidationFailed(
        [
            {
                "field": ".".join(str(p) for p in err["loc"]) or "body",
                "code": err["type"],
                "message_key": f"errors.{err['type']}",
            }
            for err in exc.errors()
        ]
    )


# --- tenants: control plane -----------------------------------------------------------------


def register_tenant(
    platform_db: Session, data: TenantProvisionIn, *, tenant_id: uuid.UUID | None = None
) -> uuid.UUID:
    """Create the tenant row (status ``provisioning``) via ``core.provision_tenant``.

    Runs in the caller's ``platform_session`` so the control plane can write its own
    ``platform.*`` rows (subscription, deployment) and platform audit in the same transaction.
    The tenant becomes visible to :func:`initialise_tenant` only after that transaction commits.

    Permission: ``platform.tenants.provision`` (step-up). Audit: platform chain
    ``platform.tenant.provisioned`` (+ tenant chain ``tenant.provisioned``, actor_type platform).
    """
    tid = tenant_id or new_id()
    with _db_errors():
        return repo.call_provision_tenant(
            platform_db,
            tenant_id=tid,
            code=data.code,
            name=data.name,
            boards=data.boards,
            plan_tier=data.plan_tier,
            deployment_mode=data.deployment_mode,
        )


def initialise_tenant(
    tenant_id: uuid.UUID, *, wrapper: KeyWrapper, engine: Engine | None = None
) -> tuple[int, str]:
    """Create the tenant's wrapped DEK + HMAC key and run :data:`POST_PROVISION_HOOKS`.

    Opens ``tenant_session(tenant_id)`` as ``sos_app``. Idempotent while the tenant is
    ``provisioning``: an existing key is kept. Concurrent calls for one tenant are serialised
    (transaction-level advisory lock), so a retry racing another never creates a second key or
    runs the hooks side by side (FR-PLT-002). Returns ``(key_version, key_id)``.

    Audit (tenant chain, actor_type system): ``tenant.key.created`` with key_version and key_id
    (never key material).
    """
    with tenant_session(tenant_id, engine=engine) as session:
        tenant = repo.get_own_tenant(session)
        if tenant is None:
            raise NotFound("Tenant not found")
        repo.lock_tenant_initialisation(session)
        if tenant.status != "provisioning":
            raise Conflict("Only a school that is being provisioned can be initialised.")
        keys = [k for k in repo.list_tenant_keys(session) if k.retired_at is None]
        if keys:
            key_version, key_id = keys[-1].key_version, keys[-1].kms_key_arn
        else:
            wrapped_dek, wrapped_hmac = generate_tenant_keys(tenant_id, wrapper)
            key = repo.insert_tenant_key(
                session,
                tenant_id=tenant_id,
                key_version=FIRST_KEY_VERSION,
                wrapped_dek=wrapped_dek,
                wrapped_hmac=wrapped_hmac,
                key_id=wrapper.key_id,
            )
            key_version, key_id = key.key_version, key.kms_key_arn
            _audit(
                session,
                action="tenant.key.created",
                resource_type="tenant",
                resource_id=tenant_id,
                summary={"key_version": key_version, "key_id": key_id},
                system=True,
            )
        for hook in POST_PROVISION_HOOKS:
            hook(session, tenant_id)
    return key_version, key_id


def provision_tenant(
    *,
    code: str,
    name: str,
    boards: Sequence[str] = (),
    plan_tier: str = "shared",
    deployment_mode: str = "shared",
    wrapper: KeyWrapper | None = None,
    platform_engine: Engine | None = None,
    app_engine: Engine | None = None,
) -> TenantProvisioned:
    """US-201 / FR-TEN-003: create a school tenant and its per-tenant keys.

    1. ``platform_session`` (sos_platform) -> ``core.provision_tenant`` (status ``provisioning``);
    2. ``tenant_session(new_id)`` (sos_app) -> wrapped keys + post-provision hooks.
    Activation is a separate step (:func:`activate_tenant`), which the database refuses until a
    key exists. The owner invite is created by the control plane afterwards.

    Permission: ``platform.tenants.provision`` (step-up). Audit: see :func:`register_tenant` and
    :func:`initialise_tenant`.
    """
    try:
        data = TenantProvisionIn(
            code=code,
            name=name,
            boards=list(boards),
            plan_tier=plan_tier,
            deployment_mode=deployment_mode,
        )
    except ValidationError as exc:
        raise _validation_failed(exc) from exc
    wrapper = wrapper or get_key_wrapper(get_settings())
    with platform_session(engine=platform_engine) as pdb:
        tenant_id = register_tenant(pdb, data)
    key_version, key_id = initialise_tenant(tenant_id, wrapper=wrapper, engine=app_engine)
    return TenantProvisioned(
        tenant_id=tenant_id,
        code=data.code,
        status="provisioning",
        key_version=key_version,
        key_id=key_id,
    )


def set_tenant_status(
    platform_db: Session, tenant_id: uuid.UUID, status: TenantStatus
) -> TenantStatusChange:
    """Move a tenant through its lifecycle via ``core.set_tenant_status``.

    Allowed: provisioning->active (requires a key), active<->suspended,
    active|suspended->offboarding, offboarding->deleted. Anything else raises ``Conflict``;
    an unknown tenant raises ``NotFound``.

    Permissions: ``platform.tenants.provision`` (activate), ``platform.tenants.suspend``
    (suspend/reactivate), ``platform.tenants.offboard`` (two-person; offboarding/deleted).
    Audit: platform chain ``platform.tenant.status_changed`` and tenant chain
    ``tenant.status_changed`` with {from, to}.
    """
    with _db_errors():
        previous = repo.call_set_tenant_status(platform_db, tenant_id, status)
    return TenantStatusChange(tenant_id=tenant_id, previous=previous, current=status)


def activate_tenant(platform_db: Session, tenant_id: uuid.UUID) -> TenantStatusChange:
    return set_tenant_status(platform_db, tenant_id, "active")


def suspend_tenant(platform_db: Session, tenant_id: uuid.UUID) -> TenantStatusChange:
    return set_tenant_status(platform_db, tenant_id, "suspended")


def reactivate_tenant(platform_db: Session, tenant_id: uuid.UUID) -> TenantStatusChange:
    return set_tenant_status(platform_db, tenant_id, "active")


def begin_offboarding(platform_db: Session, tenant_id: uuid.UUID) -> TenantStatusChange:
    return set_tenant_status(platform_db, tenant_id, "offboarding")


def list_tenant_ids(
    session: Session, statuses: Sequence[str] | None = ("active",)
) -> list[uuid.UUID]:
    """Tenant ids for per-tenant job fan-out (``context_free_session``) or the control plane.

    Workers MUST then open one ``tenant_session`` per tenant (07 §7). ``None`` = all statuses.
    """
    return repo.call_list_tenant_ids(session, statuses)


def tenant_usage(platform_db: Session, tenant_id: uuid.UUID) -> TenantUsage:
    """Counts only, for plan limits and the fleet dashboard. Permission: ``platform.usage.read``."""
    row = repo.call_tenant_usage_summary(platform_db, tenant_id)
    return TenantUsage.model_validate(row._asdict())


# --- academic structure (tenant_session) -----------------------------------------------------
#
# Reads assume ``student.read_basic``; writes assume ``tenant.structure.manage`` (owner,
# principal, office_admin; no step-up). ``session`` must be a tenant_session with the acting
# user's id set.


def _precondition_or_missing(exists: bool, what: str) -> Exception:
    if exists:
        return PreconditionFailed(f"The {what} was changed by someone else. Reload and try again.")
    return NotFound(f"{what.capitalize()} not found")


def _archived(what: str) -> Conflict:
    return Conflict(f"This {what} is archived. Unarchive it first.", code="structure_archived")


def list_academic_years(
    session: Session, *, include_archived: bool = True
) -> list[AcademicYearOut]:
    """Academic years, newest first. Archived years are included unless ``include_archived``
    is False (the API lists hide them by default; other modules still resolve old records)."""
    return [
        AcademicYearOut.model_validate(y)
        for y in repo.list_academic_years(session)
        if include_archived or y.archived_at is None
    ]


def get_academic_year(session: Session, year_id: uuid.UUID) -> AcademicYearOut:
    year = repo.get_academic_year(session, year_id)
    if year is None:
        raise NotFound("Academic year not found")
    return AcademicYearOut.model_validate(year)


def get_current_academic_year(session: Session) -> AcademicYearOut | None:
    year = repo.get_current_academic_year(session)
    return AcademicYearOut.model_validate(year) if year else None


def create_academic_year(session: Session, data: AcademicYearCreate) -> AcademicYearOut:
    """Create an academic year; with ``is_current`` it atomically becomes the only current year.

    Rejects a date range overlapping another year. Audit: ``academic_year.created``
    (+ ``academic_year.current_set`` when is_current).
    """
    tenant_id = repo.current_tenant_id(session)
    repo.lock_academic_structure(session)
    if repo.academic_year_overlaps(session, data.starts_on, data.ends_on):
        raise Conflict("The dates overlap another academic year.", code="academic_year_overlap")
    previous = repo.get_current_academic_year(session) if data.is_current else None
    with _db_errors():
        if data.is_current:
            repo.clear_current_academic_year(session, except_id=None)
        year = repo.insert_academic_year(
            session,
            year_id=new_id(),
            tenant_id=tenant_id,
            label=data.label,
            starts_on=data.starts_on,
            ends_on=data.ends_on,
            is_current=data.is_current,
        )
    _audit(
        session,
        action="academic_year.created",
        resource_type="academic_year",
        resource_id=year.id,
        summary={"label": year.label, "is_current": year.is_current},
    )
    if data.is_current:
        _audit(
            session,
            action="academic_year.current_set",
            resource_type="academic_year",
            resource_id=year.id,
            summary={"previous_year_id": previous.id if previous else None, "year_id": year.id},
        )
    return AcademicYearOut.model_validate(year)


def update_academic_year(
    session: Session, year_id: uuid.UUID, data: AcademicYearUpdate, *, expected_version: int
) -> AcademicYearOut:
    """Change label/dates with optimistic locking (``PreconditionFailed`` on a stale version).

    Audit: ``academic_year.updated`` with changed field names.
    """
    repo.lock_academic_structure(session)
    current = repo.get_academic_year(session, year_id)
    if current is None:
        raise NotFound("Academic year not found")
    values = data.model_dump(exclude_unset=True, exclude_none=True)
    starts_on: dt.date = values.get("starts_on", current.starts_on)
    ends_on: dt.date = values.get("ends_on", current.ends_on)
    if not starts_on < ends_on:
        raise ValidationFailed(
            [{"field": "ends_on", "code": "invalid", "message_key": "errors.invalid"}]
        )
    if repo.academic_year_overlaps(session, starts_on, ends_on, exclude_id=year_id):
        raise Conflict("The dates overlap another academic year.", code="academic_year_overlap")
    with _db_errors():
        year = repo.update_academic_year(
            session, year_id, expected_version=expected_version, values=values
        )
    if year is None:
        raise _precondition_or_missing(True, "academic year")
    _audit(
        session,
        action="academic_year.updated",
        resource_type="academic_year",
        resource_id=year_id,
        summary={"fields": sorted(values)},
    )
    return AcademicYearOut.model_validate(year)


def set_current_academic_year(
    session: Session, year_id: uuid.UUID, *, expected_version: int
) -> AcademicYearOut:
    """FR-TEN-010: make ``year_id`` the one current year, switching atomically.

    Serialised per tenant; the partial unique index ``one_current_year`` is the final guard.
    Audit: ``academic_year.current_set`` with {previous_year_id, year_id}.
    """
    repo.lock_academic_structure(session)
    year = repo.get_academic_year(session, year_id)
    if year is None:
        raise NotFound("Academic year not found")
    if year.version != expected_version:
        raise _precondition_or_missing(True, "academic year")
    if year.is_current:
        return AcademicYearOut.model_validate(year)
    if year.archived_at is not None:
        raise _archived("academic year")
    previous = repo.get_current_academic_year(session)
    with _db_errors():
        repo.clear_current_academic_year(session, except_id=year_id)
        updated = repo.update_academic_year(
            session, year_id, expected_version=expected_version, values={"is_current": True}
        )
    if updated is None:
        raise _precondition_or_missing(True, "academic year")
    _audit(
        session,
        action="academic_year.current_set",
        resource_type="academic_year",
        resource_id=year_id,
        summary={"previous_year_id": previous.id if previous else None, "year_id": year_id},
    )
    return AcademicYearOut.model_validate(updated)


@lru_cache(maxsize=1)
def default_class_catalog() -> tuple[ClassCreate, ...]:
    """Nursery-XII with English and Telugu names (``academic_defaults.yaml``)."""
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("app.tenancy").joinpath("academic_defaults.yaml").read_text("utf-8")
    )
    return tuple(ClassCreate.model_validate(item) for item in raw["classes"])


@lru_cache(maxsize=1)
def suggested_section_names() -> tuple[str, ...]:
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("app.tenancy").joinpath("academic_defaults.yaml").read_text("utf-8")
    )
    return tuple(str(n) for n in raw["section_names"])


def list_classes(session: Session, *, include_archived: bool = True) -> list[ClassOut]:
    """Classes in display order (archived ones unless ``include_archived`` is False)."""
    return [
        ClassOut.model_validate(c)
        for c in repo.list_classes(session)
        if include_archived or c.archived_at is None
    ]


def get_class(session: Session, class_id: uuid.UUID) -> ClassOut:
    klass = repo.get_class(session, class_id)
    if klass is None:
        raise NotFound("Class not found")
    return ClassOut.model_validate(klass)


def create_class(session: Session, data: ClassCreate) -> ClassOut:
    """Audit: ``class.created``."""
    tenant_id = repo.current_tenant_id(session)
    with _db_errors():
        klass = repo.insert_class(
            session,
            class_id=new_id(),
            tenant_id=tenant_id,
            code=data.code,
            display_en=data.display_en,
            display_te=data.display_te,
            sort_order=data.sort_order,
        )
    _audit(
        session,
        action="class.created",
        resource_type="class",
        resource_id=klass.id,
        summary={"sort_order": klass.sort_order},
    )
    return ClassOut.model_validate(klass)


def ensure_default_classes(session: Session) -> list[ClassOut]:
    """Add any missing default classes (Nursery-XII); existing classes are left untouched.

    Audit: ``class.defaults_added`` with the count added (skip when 0).
    """
    tenant_id = repo.current_tenant_id(session)
    rows = [
        {"id": new_id(), "tenant_id": tenant_id, **item.model_dump()}
        for item in default_class_catalog()
    ]
    added = repo.insert_classes_if_missing(session, rows)
    if added:
        _audit(
            session,
            action="class.defaults_added",
            resource_type="class",
            resource_id=None,
            summary={"count": added},
        )
    return list_classes(session)


def update_class(
    session: Session, class_id: uuid.UUID, data: ClassUpdate, *, expected_version: int
) -> ClassOut:
    """Audit: ``class.updated`` with changed field names."""
    values = data.model_dump(exclude_unset=True, exclude_none=True)
    with _db_errors():
        klass = repo.update_class(
            session, class_id, expected_version=expected_version, values=values
        )
    if klass is None:
        raise _precondition_or_missing(repo.get_class(session, class_id) is not None, "class")
    _audit(
        session,
        action="class.updated",
        resource_type="class",
        resource_id=class_id,
        summary={"fields": sorted(values)},
    )
    return ClassOut.model_validate(klass)


def list_sections(
    session: Session,
    *,
    academic_year_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    include_archived: bool = True,
) -> list[SectionOut]:
    """Sections in class order (archived ones unless ``include_archived`` is False)."""
    rows = repo.list_sections(session, academic_year_id=academic_year_id, class_id=class_id)
    return [SectionOut.model_validate(s) for s in rows if include_archived or s.archived_at is None]


def get_section(session: Session, section_id: uuid.UUID) -> SectionOut:
    section = repo.get_section(session, section_id)
    if section is None:
        raise NotFound("Section not found")
    return SectionOut.model_validate(section)


def create_section(session: Session, data: SectionCreate) -> SectionOut:
    """Create a section; year, class and class teacher must belong to this school (composite FKs).

    An archived year or class answers 409 ``structure_archived``.
    Audit: ``section.created`` (+ ``section.class_teacher_assigned`` when set).
    """
    tenant_id = repo.current_tenant_id(session)
    year = repo.get_academic_year(session, data.academic_year_id)
    if year is not None and year.archived_at is not None:
        raise _archived("academic year")
    klass = repo.get_class(session, data.class_id)
    if klass is not None and klass.archived_at is not None:
        raise _archived("class")
    with _db_errors():
        section = repo.insert_section(
            session,
            section_id=new_id(),
            tenant_id=tenant_id,
            academic_year_id=data.academic_year_id,
            class_id=data.class_id,
            name=data.name,
            class_teacher_membership_id=data.class_teacher_membership_id,
        )
    _audit(
        session,
        action="section.created",
        resource_type="section",
        resource_id=section.id,
        summary={"class_id": section.class_id, "academic_year_id": section.academic_year_id},
    )
    if section.class_teacher_membership_id is not None:
        _audit(
            session,
            action="section.class_teacher_assigned",
            resource_type="section",
            resource_id=section.id,
            summary={"membership_id": section.class_teacher_membership_id},
        )
    return SectionOut.model_validate(section)


def update_section(
    session: Session, section_id: uuid.UUID, data: SectionUpdate, *, expected_version: int
) -> SectionOut:
    """Rename or (re)assign/clear the class teacher. Audit: ``section.updated``."""
    values = {k: getattr(data, k) for k in data.model_fields_set}
    if values.get("name", "") is None:
        raise ValidationFailed(
            [{"field": "name", "code": "missing", "message_key": "errors.missing"}]
        )
    with _db_errors():
        section = repo.update_section(
            session, section_id, expected_version=expected_version, values=values
        )
    if section is None:
        raise _precondition_or_missing(repo.get_section(session, section_id) is not None, "section")
    _audit(
        session,
        action="section.updated",
        resource_type="section",
        resource_id=section_id,
        summary={"fields": sorted(values)},
    )
    if "class_teacher_membership_id" in values:
        _audit(
            session,
            action="section.class_teacher_assigned",
            resource_type="section",
            resource_id=section_id,
            summary={"membership_id": values["class_teacher_membership_id"]},
        )
    return SectionOut.model_validate(section)


# --- archive (US-202, FR-TEN-010; 0023_api_gaps) ---------------------------------------------


def _set_archived(
    session: Session,
    kind: repo.StructureKind,
    row_id: uuid.UUID,
    *,
    archived: bool,
    expected_version: int,
) -> Any:
    """Archive or unarchive one year/class/section (``tenant.structure.manage``; If-Match).

    404 for an unknown id (or another school's), 412 for a stale version; a row already in the
    requested state is returned unchanged (no audit). The current year cannot be archived (409
    ``academic_year_current``); nor can a year, class or section with active enrolments (409
    ``structure_in_use``, enforced by the database). Audit: ``<kind>.archived`` /
    ``<kind>.unarchived`` (no values).
    """
    what = kind.replace("_", " ")
    repo.lock_academic_structure(session)
    current = repo.get_structure_row(session, kind, row_id)
    if current is None:
        raise NotFound(f"{what.capitalize()} not found")
    if current.version != expected_version:
        raise _precondition_or_missing(True, what)
    if (current.archived_at is not None) == archived:
        return current
    if archived and getattr(current, "is_current", False):
        raise Conflict(
            "The current academic year cannot be archived. Make another year current first.",
            code="academic_year_current",
        )
    with _db_errors():
        row = repo.set_archived(
            session, kind, row_id, expected_version=expected_version, archived=archived
        )
    if row is None:  # pragma: no cover - serialised by the structure lock
        raise _precondition_or_missing(True, what)
    _audit(
        session,
        action=f"{kind}.{'archived' if archived else 'unarchived'}",
        resource_type=kind,
        resource_id=row_id,
        summary={},
    )
    return row


def archive_academic_year(
    session: Session, year_id: uuid.UUID, *, archived: bool, expected_version: int
) -> AcademicYearOut:
    """Archive/unarchive an academic year (see :func:`_set_archived`)."""
    row = _set_archived(
        session, "academic_year", year_id, archived=archived, expected_version=expected_version
    )
    return AcademicYearOut.model_validate(row)


def archive_class(
    session: Session, class_id: uuid.UUID, *, archived: bool, expected_version: int
) -> ClassOut:
    """Archive/unarchive a class (see :func:`_set_archived`)."""
    row = _set_archived(
        session, "class", class_id, archived=archived, expected_version=expected_version
    )
    return ClassOut.model_validate(row)


def archive_section(
    session: Session, section_id: uuid.UUID, *, archived: bool, expected_version: int
) -> SectionOut:
    """Archive/unarchive a section (see :func:`_set_archived`)."""
    row = _set_archived(
        session, "section", section_id, archived=archived, expected_version=expected_version
    )
    return SectionOut.model_validate(row)


# --- school profile and settings (tenant_session; FR-TEN-012) ---------------------------------


def _tenant_out(tenant: Any) -> TenantOut:
    stored: dict[str, Any] = dict(tenant.settings or {})
    known = {k: v for k, v in stored.items() if k in TenantSettings.model_fields}
    try:
        settings = TenantSettings.model_validate(known)
    except ValidationError:
        settings = TenantSettings()  # unreadable legacy values fall back to defaults
    return TenantOut(
        id=tenant.id,
        code=tenant.code,
        name=tenant.name,
        boards=list(tenant.boards),
        state_code=tenant.state_code,
        status=tenant.status,
        plan_tier=tenant.plan_tier,
        deployment_mode=tenant.deployment_mode,
        settings=settings,
        version=tenant.version,
    )


def get_tenant(session: Session) -> TenantOut:
    """The caller's own school (RLS ``own_tenant``). Permission: any member."""
    tenant = repo.get_own_tenant(session)
    if tenant is None:
        raise NotFound("School not found")
    return _tenant_out(tenant)


def update_tenant_settings(
    session: Session, data: TenantSettingsPatch, *, expected_version: int
) -> TenantOut:
    """Change school settings (``tenant.settings.manage``, step-up; optimistic locking).

    Audit: ``tenant.settings_updated`` with the changed field names.
    """
    tenant = repo.get_own_tenant(session)
    if tenant is None:
        raise NotFound("School not found")
    current = _tenant_out(tenant).settings.model_dump(mode="json")
    changes = data.model_dump(mode="json", exclude_unset=True, exclude_none=True)
    try:
        merged = TenantSettings.model_validate({**current, **changes})
    except ValidationError as exc:
        raise _validation_failed(exc) from exc
    stored = {**dict(tenant.settings or {}), **merged.model_dump(mode="json")}
    updated = repo.update_tenant_settings(
        session, expected_version=expected_version, settings=stored
    )
    if updated is None:
        raise PreconditionFailed("The settings were changed by someone else. Reload and try again.")
    _audit(
        session,
        action="tenant.settings_updated",
        resource_type="tenant",
        resource_id=updated.id,
        summary={"fields": sorted(k for k in changes if changes[k] != current.get(k))},
    )
    return _tenant_out(updated)


def session_settings(session: Session) -> TenantSettings:
    """The school settings a signed-in session applies (idle timeout, date format, languages),
    read in the caller's ``tenant_session`` (FR-TEN-012, FR-IAM-003). Any member."""
    return get_tenant(session).settings
