"""Tenancy public API: provisioning, lifecycle, keys and academic structure.

Other modules call only these functions. Routes live in ``tenancy.api`` and wrap each call
with ``require(...)``; the permission assumed is stated per function. Each mutation writes its
audit event here, in the caller's transaction (CLAUDE.md §6.7).

Requirements: FR-TEN-001, FR-TEN-002, FR-TEN-003, FR-TEN-010; US-201, US-202.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Collection, Iterator, Sequence
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
from app.core import purge as purging
from app.core.config import get_settings
from app.core.crypto import (
    KEY_CACHE_MAX_S,
    MAX_KEY_VERSION,
    KeyWrapper,
    generate_tenant_keys,
    get_key_wrapper,
)
from app.core.db import platform_session, tenant_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.languages import enabled_languages, telugu_enabled
from app.core.logging import get_context
from app.tenancy import offboarding
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
    TenantDataCounts,
    TenantKeysDestroyed,
    TenantKeyVersion,
    TenantOut,
    TenantProvisioned,
    TenantProvisionIn,
    TenantPurgeResult,
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

KeyReferenceCounter = Callable[[Session, int], int]
"""Called as ``counter(session, key_version)`` inside the school's ``tenant_session``; returns how
many stored ciphertexts still use that key version (SEC-012 rotation).

The module that owns field encryption (``students.rotation``) appends to
:data:`KEY_REFERENCE_COUNTERS` at import time. :func:`retire_key_version` refuses when none is
registered (fail closed) or any counter reports a reference.
"""

KEY_REFERENCE_COUNTERS: list[KeyReferenceCounter] = []

ROTATABLE_STATUSES = ("active", "suspended")
RETIRE_MARGIN_S = 60
"""Extra wait on top of the key cache TTL before an older version may be retired."""


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


# --- key versions (SEC-012 rotation, 07 §8) -------------------------------------------------


def _key_versions(keys: Sequence[Any]) -> list[TenantKeyVersion]:
    active = [k.key_version for k in keys if k.retired_at is None]
    current = max(active) if active else None
    return [
        TenantKeyVersion(
            key_version=k.key_version,
            key_id=k.kms_key_arn,
            created_at=k.created_at,
            retired_at=k.retired_at,
            current=k.key_version == current,
        )
        for k in keys
    ]


def list_key_versions(session: Session) -> list[TenantKeyVersion]:
    """The current school's key versions, oldest first (``tenant_session``; no key material).

    ``current`` marks the newest unretired version: the one new ciphertext uses.
    """
    return _key_versions(repo.list_tenant_keys(session))


def current_key_version(session: Session) -> int:
    """The newest unretired key version of the current school; ``Conflict`` if there is none."""
    current = [k.key_version for k in list_key_versions(session) if k.current]
    if not current:
        raise Conflict("The school has no active data encryption key.", code="key_missing")
    return current[0]


def create_key_version(
    session: Session, tenant_id: uuid.UUID, *, wrapper: KeyWrapper, new_hmac_key: bool = False
) -> TenantKeyVersion:
    """Rotate the school's data encryption key: add the next key version (SEC-012, 07 §8).

    Runs in the school's ``tenant_session`` (sos_app may INSERT into ``core.tenant_keys``; RLS).
    A fresh random DEK is wrapped by ``wrapper`` (KMS in deployed environments, encryption
    context = tenant id). The blind-index HMAC key is carried over (unwrapped and wrapped again),
    so existing blind indexes keep matching, unless ``new_hmac_key`` (incident response): then a
    fresh one is generated and re-encryption recomputes the blind indexes it owns.

    Older versions are kept for decryption; nothing is retired or destroyed here. Only schools in
    ``active``/``suspended`` can rotate (``Conflict`` otherwise). Serialised per school.

    Audit (tenant chain, actor_type system): ``tenant.key.rotated`` with key_version,
    previous_key_version, key_id and hmac_key (``carried``/``new``); never key material.
    """
    tenant = repo.get_own_tenant(session)
    if tenant is None or tenant.id != tenant_id:
        raise NotFound("Tenant not found")
    if tenant.status not in ROTATABLE_STATUSES:
        raise Conflict("Only an active or suspended school can rotate its key.", code="key_state")
    repo.lock_tenant_keys(session)
    keys = repo.list_tenant_keys(session)
    active = [k for k in keys if k.retired_at is None]
    if not active:
        raise Conflict("The school has no active data encryption key.", code="key_missing")
    previous = active[-1]
    version = max(k.key_version for k in keys) + 1
    if version > MAX_KEY_VERSION:
        raise Conflict("The school has used every key version.", code="key_versions_exhausted")
    wrapped_dek, wrapped_hmac = generate_tenant_keys(tenant_id, wrapper)
    if not new_hmac_key:
        hmac_key = wrapper.unwrap(bytes(previous.wrapped_hmac), tenant_id=tenant_id)
        wrapped_hmac = wrapper.wrap(hmac_key, tenant_id=tenant_id)
        del hmac_key
    key = repo.insert_tenant_key(
        session,
        tenant_id=tenant_id,
        key_version=version,
        wrapped_dek=wrapped_dek,
        wrapped_hmac=wrapped_hmac,
        key_id=wrapper.key_id,
    )
    _audit(
        session,
        action="tenant.key.rotated",
        resource_type="tenant",
        resource_id=tenant_id,
        summary={
            "key_version": key.key_version,
            "previous_key_version": previous.key_version,
            "key_id": key.kms_key_arn,
            "hmac_key": "new" if new_hmac_key else "carried",
        },
        system=True,
    )
    return next(v for v in list_key_versions(session) if v.key_version == version)


def retire_key_version(session: Session, key_version: int) -> TenantKeyVersion:
    """Mark an older key version retired once no ciphertext uses it (SEC-012, 07 §8).

    Refuses (``Conflict``) the current version, a version any :data:`KEY_REFERENCE_COUNTERS`
    counter still reports (or when no counter is registered), and any retirement until the
    current version is older than the key cache TTL plus :data:`RETIRE_MARGIN_S` (a process may
    still write under an older version until its cache expires). Retiring an already retired
    version returns it unchanged. The wrapped key row is kept: a retired version still decrypts
    (for example data restored from a backup); key material is never destroyed here.

    Audit (tenant chain, actor_type system): ``tenant.key.retired`` with key_version.
    """
    tenant = repo.get_own_tenant(session)
    if tenant is None:
        raise NotFound("Tenant not found")
    repo.lock_tenant_keys(session)
    versions = list_key_versions(session)
    target = next((v for v in versions if v.key_version == key_version), None)
    if target is None:
        raise NotFound("Key version not found")
    if target.retired_at is not None:
        return target
    if target.current:
        raise Conflict("The current key version cannot be retired.", code="key_current")
    current = next(v for v in versions if v.current)
    age = (repo.db_now(session) - current.created_at).total_seconds()
    if age < KEY_CACHE_MAX_S + RETIRE_MARGIN_S:
        raise Conflict(
            "Wait until cached keys have expired after the rotation.", code="key_cache_window"
        )
    if not KEY_REFERENCE_COUNTERS:
        raise Conflict("No ciphertext census is registered.", code="key_census_missing")
    if any(counter(session, key_version) for counter in KEY_REFERENCE_COUNTERS):
        raise Conflict("Stored values still use this key version.", code="key_in_use")
    repo.retire_tenant_key(session, key_version)
    _audit(
        session,
        action="tenant.key.retired",
        resource_type="tenant",
        resource_id=tenant.id,
        summary={"key_version": key_version},
        system=True,
    )
    return next(v for v in list_key_versions(session) if v.key_version == key_version)


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


def _class_out(klass: Any) -> ClassOut:
    """A class as shown: ``display_te`` is kept in the database and empty while Telugu is
    hidden (ADR-0036)."""
    out = ClassOut.model_validate(klass)
    return out if telugu_enabled() else out.model_copy(update={"display_te": ""})


def list_classes(session: Session, *, include_archived: bool = True) -> list[ClassOut]:
    """Classes in display order (archived ones unless ``include_archived`` is False)."""
    return [
        _class_out(c)
        for c in repo.list_classes(session)
        if include_archived or c.archived_at is None
    ]


def get_class(session: Session, class_id: uuid.UUID) -> ClassOut:
    klass = repo.get_class(session, class_id)
    if klass is None:
        raise NotFound("Class not found")
    return _class_out(klass)


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
            # Optional while Telugu is hidden (ADR-0036); the column needs a value, so a class
            # without a Telugu name keeps its English name there (never shown as Telugu).
            display_te=data.display_te or data.display_en,
            sort_order=data.sort_order,
        )
    _audit(
        session,
        action="class.created",
        resource_type="class",
        resource_id=klass.id,
        summary={"sort_order": klass.sort_order},
    )
    return _class_out(klass)


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
    return _class_out(klass)


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


def lock_enrolment_targets(
    session: Session, section_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, SectionOut]:
    """Lock sections for an enrolment write (enrol, move, promotion commit or undo).

    Each section, its class and its academic year are locked ``FOR SHARE`` until the caller's
    transaction ends, so a concurrent archive of any of them waits and then fails on its
    active-enrolment guard (409 ``structure_in_use``), or, when the archive committed first,
    this call sees it. Raises 404 when a section is unknown (or another school's) and 409
    ``structure_archived`` when a section, its class or its year is archived.
    """
    wanted = sorted(set(section_ids))
    rows = repo.share_lock_sections(session, wanted)
    if len(rows) != len(wanted):
        raise NotFound("Section not found")
    out: dict[uuid.UUID, SectionOut] = {}
    for section, klass, year in rows:
        if year.archived_at is not None:
            raise _archived("academic year")
        if klass.archived_at is not None:
            raise _archived("class")
        if section.archived_at is not None:
            raise _archived("section")
        out[section.id] = SectionOut.model_validate(section)
    return out


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
    return _class_out(row)


def archive_section(
    session: Session, section_id: uuid.UUID, *, archived: bool, expected_version: int
) -> SectionOut:
    """Archive/unarchive a section (see :func:`_set_archived`)."""
    row = _set_archived(
        session, "section", section_id, archived=archived, expected_version=expected_version
    )
    return SectionOut.model_validate(row)


# --- school profile and settings (tenant_session; FR-TEN-012) ---------------------------------


_LETTERHEAD_TE = ("school_name_te", "address_te")


def _stored_settings(tenant: Any) -> TenantSettings:
    stored: dict[str, Any] = dict(tenant.settings or {})
    known = {k: v for k, v in stored.items() if k in TenantSettings.model_fields}
    try:
        return TenantSettings.model_validate(known)
    except ValidationError:
        return TenantSettings()  # unreadable legacy values fall back to defaults


def _shown_settings(settings: TenantSettings) -> TenantSettings:
    """The settings as shown and applied. While Telugu is hidden (ADR-0036) the school's
    languages are the enabled ones (English) and the Telugu letterhead lines are empty; the
    stored values are kept for when Telugu comes back."""
    if telugu_enabled():
        return settings
    enabled = enabled_languages()
    languages = [lang for lang in settings.languages if lang in enabled] or list(enabled)
    head = settings.certificate_letterhead.model_copy(update=dict.fromkeys(_LETTERHEAD_TE, ""))
    return settings.model_copy(update={"languages": languages, "certificate_letterhead": head})


def _tenant_out(tenant: Any) -> TenantOut:
    settings = _shown_settings(_stored_settings(tenant))
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
    current = _stored_settings(tenant).model_dump(mode="json")
    changes = data.model_dump(mode="json", exclude_unset=True, exclude_none=True)
    head = changes.get("certificate_letterhead")
    if head is not None and not telugu_enabled():
        # The form shows no Telugu lines while Telugu is hidden (ADR-0036): an empty one keeps
        # what is stored instead of erasing it.
        for key in _LETTERHEAD_TE:
            if not head.get(key):
                head[key] = current["certificate_letterhead"][key]
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


# --- offboarding: deleting a school's data (FR-PLT-005, ADR-0029) ---------------------------
#
# Lifecycle "offboard" (ADR-0020): the control plane calls these with a tenant id; each opens
# the school's own tenant_session (sos_app) and returns counts only. The database lets the purge
# role act only on a school in ``offboarding`` (``core.tenant_purge_allowed``).

TenantDataOwner = offboarding.TenantDataOwner
TenantObjectOwner = offboarding.TenantObjectOwner
KEYS_DESTROYED_HOOKS = offboarding.KEYS_DESTROYED_HOOKS
"""``hook(tenant_id)`` after a school's keys were destroyed (e.g. drop cached unwrapped keys)."""


def register_data_owner(owner: TenantDataOwner) -> None:
    """Each tenant module registers its school tables at import time (``offboarding.yaml``)."""
    offboarding.register_data_owner(owner)


def register_object_owner(owner: TenantObjectOwner) -> None:
    """The documents module registers the files under ``t/<tenant_id>/``."""
    offboarding.register_object_owner(owner)


def tenant_data_inventory(
    tenant_id: uuid.UUID, *, engine: Engine | None = None
) -> TenantDataCounts:
    """Rows per category and files of an ``offboarding`` school, before deletion (counts only).
    Called by the offboarding job (``platform.tenants.offboard``)."""
    return offboarding.inventory(tenant_id, engine=engine)


def purge_tenant(tenant_id: uuid.UUID, *, engine: Engine | None = None) -> TenantPurgeResult:
    """Delete every row (one transaction) and file of an ``offboarding`` school. Idempotent.
    Audit (school chain, system): ``tenant.data_purged`` with counts per category."""
    return offboarding.purge(tenant_id, engine=engine)


def verify_tenant_purged(tenant_id: uuid.UUID, *, engine: Engine | None = None) -> TenantDataCounts:
    """What remains of the school (rows per table, files); empty means the purge is complete."""
    return offboarding.verify(tenant_id, engine=engine)


def destroy_tenant_keys(
    tenant_id: uuid.UUID, *, engine: Engine | None = None
) -> TenantKeysDestroyed:
    """Crypto-shredding once nothing else remains (``409 data_remaining`` otherwise).
    Audit (school chain, system): ``tenant.keys_destroyed`` (versions and key id only)."""
    return offboarding.destroy_keys(tenant_id, engine=engine)


def purge_expired_audit_chain(tenant_id: uuid.UUID, *, engine: Engine | None = None) -> int:
    """Delete a ``deleted`` school's audit chain after its retention (events older than a year)."""
    return offboarding.purge_expired_audit_chain(tenant_id, engine=engine)


# The academic structure and settings are this module's own school data.
_STRUCTURE = purging.PurgeTables(deleted=("core.sections", "core.classes", "core.academic_years"))


def structure_data_counts(session: Session) -> dict[str, int]:
    return _STRUCTURE.count(session)


def purge_structure_data(session: Session) -> dict[str, int]:
    """Offboarding purge (as ``sos_purger``): sections, classes, academic years."""
    return _STRUCTURE.delete(session)


def clear_school_settings(session: Session) -> int:
    """Offboarding prepare step (as ``sos_app``): the school's settings become ``{}``; the
    tenant row itself stays (status, code, registered name)."""
    return repo.clear_tenant_settings(session)


register_data_owner(
    TenantDataOwner(
        name="tenancy",
        count=structure_data_counts,
        purge=purge_structure_data,
        prepare=clear_school_settings,
    )
)
