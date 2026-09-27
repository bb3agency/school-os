"""Apply a synthetic dataset plan through the module services (docs/12 §3). NEVER real data.

The CLI is :mod:`app.devtools.seed_synthetic` (``make seed-synthetic``), which checks the
environment before importing this module.

What it creates per school (see :mod:`app.devtools.plan` for the exact dataset): the tenant
(shared tier) through ``tenancy.register_tenant`` + ``tenancy.initialise_tenant`` (keys, system
roles via the identity hook) + ``tenancy.activate_tenant``; academic years 2026-27 (current) and
2025-26; classes Nursery-XII (``ensure_default_classes``); sections; staff for every system role
through ``identity.invite_user`` + ``set_membership_status``; class teachers scoped to, and
assigned as class teacher of, one current-year section each; subject teachers scoped to two
classes. Every write goes through the owning module's service in a ``tenant_session``, so RLS,
validation and audit events apply exactly as in production. With :class:`StudentOptions`
(``--profile small|full``) the first office admin then adds students, guardians, enrolments,
register-page images and documents (:mod:`app.devtools.student_seeder`).

The one exception is the school's FIRST owner. On this code base nothing on the tenant side may
create a member before one exists (``core.create_user_for_invite`` requires an active inviter),
and the control-plane owner invite is not merged yet. :class:`AdminOwnerBootstrap` therefore
creates the owner with the local database admin connection, mirroring what
``core.create_owner_invite`` will do (user, membership, ``school`` scope, ``owner`` role), and
the seeder then records ``membership.created``/``membership.role_granted`` in the school's audit
chain. The bootstrap is injectable (``owner_bootstrap=``) so it can be replaced by the
control-plane service once available. The admin URL is a dev-only default and is never logged.

Idempotent: re-running with the same inputs finds each school by its deterministic ID and code,
each year by label, each section by (year, class, name) and each person by e-mail, and only adds
what is missing. Output: one JSON summary on stdout with IDs, codes and counts only (no names,
e-mails or subjects), plus structured log lines through ``app.core.logging``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.resolver import build_snapshot
from app.core.crypto import KeyWrapper
from app.core.db import context_free_session, platform_session, tenant_session
from app.core.errors import Conflict
from app.core.ids import new_id
from app.core.logging import get_logger
from app.devtools import student_seeder
from app.devtools.plan import DatasetPlan, StaffSpec, TenantPlan
from app.devtools.register_pages import RegisterPage
from app.devtools.students import SchoolStudents
from app.identity import service as identity
from app.identity.schemas import InviteIn, ScopeIn, UserOut
from app.platform import service as platform_service
from app.tenancy import service as tenancy
from app.tenancy.schemas import (
    AcademicYearCreate,
    SectionCreate,
    SectionOut,
    SectionUpdate,
    TenantProvisionIn,
)

REQUEST_ID: Final = "seed-synthetic"
AUDIT_SOURCE: Final = "synthetic_seed"
PAGE: Final = 200
STUDENT_SEEDER_ROLE: Final = "office_admin"  # student.create, document.upload

log = get_logger(__name__)

OwnerBootstrap = Callable[[uuid.UUID, StaffSpec], None]
"""``bootstrap(tenant_id, owner_spec)`` creates the school's first, ACTIVE owner membership
(user by IdP subject, membership, ``school`` scope, ``owner`` role). Must be idempotent."""


class SeedError(RuntimeError):
    """The dataset cannot be applied (message never contains personal data)."""


@dataclass(frozen=True)
class StudentOptions:
    """What to add besides schools and staff (``--profile``; docs/12 §3)."""

    build: Callable[[TenantPlan], SchoolStudents]
    """The school's synthetic students (pure: :func:`app.devtools.students.build_students`)."""
    pages: Callable[[TenantPlan, SchoolStudents], list[RegisterPage]]
    """Rendered register pages to store as ``register_scan`` documents."""
    documents: bool = True
    uploader: student_seeder.Uploader = student_seeder.http_uploader


# --- first owner -----------------------------------------------------------------------------


class PlatformOwnerBootstrap:
    """Production path for a school's first owner (default).

    1. ``platform.service.invite_school_owner`` (``core.create_owner_invite``) while the school
       is ``provisioning``, audited on the platform chain;
    2. the school is activated (its key already exists from ``initialise_tenant``);
    3. the owner accepts on "first sign-in" via ``identity.accept_invitations`` (ADR-0019),
       audited in the school's chain. Idempotent: re-runs find the active owner and skip.
    """

    def __call__(self, tenant_id: uuid.UUID, spec: StaffSpec) -> None:
        with platform_session() as pdb:
            _user_id, membership_id, _assigned = platform_service.invite_school_owner(
                pdb,
                tenant_id=tenant_id,
                subject=spec.subject,
                display_name=spec.display_name,
                email=spec.email,
                language=spec.preferred_language,
            )
            audit.record_platform(
                pdb,
                action="tenant.owner_invite_created",
                resource_type="membership",
                resource_id=membership_id,
                summary={"source": AUDIT_SOURCE},
                actor_type="system",
                subject_tenant_id=tenant_id,
                request_id=REQUEST_ID,
            )
        with platform_session() as pdb:
            tenancy.activate_tenant(pdb, tenant_id)
        identity.accept_invitations(spec.subject, request_id=REQUEST_ID)


class AdminOwnerBootstrap:
    """Dev/CI-only stand-in for the control-plane owner invite (see module docstring).

    Uses the database admin connection because no application role may create a school's
    first member on this code base. Mirrors ``core.create_owner_invite`` and additionally makes
    the membership active. Superseded by ``PlatformOwnerBootstrap`` (the default); kept for
    tests that need an owner without the control plane.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __call__(self, tenant_id: uuid.UUID, spec: StaffSpec) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO core.users "
                    "(id, idp_subject, display_name, email, preferred_language, status) "
                    "VALUES (:u, :s, :n, :e, :l, 'active') ON CONFLICT (idp_subject) DO NOTHING"
                ),
                {
                    "u": new_id(),
                    "s": spec.subject,
                    "n": spec.display_name,
                    "e": spec.email,
                    "l": spec.preferred_language,
                },
            )
            user_id: uuid.UUID = conn.execute(
                text("SELECT id FROM core.users WHERE idp_subject = :s"), {"s": spec.subject}
            ).scalar_one()
            existing = conn.execute(
                text("SELECT id FROM core.memberships WHERE tenant_id = :t AND user_id = :u"),
                {"t": tenant_id, "u": user_id},
            ).scalar_one_or_none()
            membership_id = existing or new_id()
            if existing is None:
                conn.execute(
                    text(
                        "INSERT INTO core.memberships "
                        "(id, tenant_id, user_id, status, mfa_required) "
                        "VALUES (:m, :t, :u, 'active', true)"
                    ),
                    {"m": membership_id, "t": tenant_id, "u": user_id},
                )
                conn.execute(
                    text(
                        "INSERT INTO core.membership_scopes "
                        "(id, tenant_id, membership_id, scope_type, scope_ref) "
                        "VALUES (:i, :t, :m, 'school', NULL)"
                    ),
                    {"i": new_id(), "t": tenant_id, "m": membership_id},
                )
            granted = conn.execute(
                text(
                    "INSERT INTO core.membership_roles (tenant_id, membership_id, role_id) "
                    "SELECT r.tenant_id, :m, r.id FROM core.roles AS r "
                    "WHERE r.tenant_id = :t AND r.key = 'owner' "
                    "ON CONFLICT DO NOTHING RETURNING role_id"
                ),
                {"t": tenant_id, "m": membership_id},
            ).all()
            has_role: int = conn.execute(
                text(
                    "SELECT count(*) FROM core.membership_roles AS mr "
                    "JOIN core.roles AS r ON r.tenant_id = mr.tenant_id AND r.id = mr.role_id "
                    "WHERE mr.tenant_id = :t AND mr.membership_id = :m AND r.key = 'owner'"
                ),
                {"t": tenant_id, "m": membership_id},
            ).scalar_one()
            if not has_role:
                raise SeedError("the owner role is missing; system roles were not cloned")
        if existing is None or granted:
            with tenant_session(tenant_id) as session:
                if existing is None:
                    audit.record(
                        session,
                        action="membership.created",
                        resource_type="membership",
                        resource_id=membership_id,
                        summary={"user_id": user_id, "status": "active", "source": AUDIT_SOURCE},
                        actor_type="system",
                        request_id=REQUEST_ID,
                    )
                for (role_id,) in granted:
                    audit.record(
                        session,
                        action="membership.role_granted",
                        resource_type="membership",
                        resource_id=membership_id,
                        summary={"role_key": "owner", "role_id": role_id, "source": AUDIT_SOURCE},
                        actor_type="system",
                        request_id=REQUEST_ID,
                    )


# --- summary ---------------------------------------------------------------------------------


@dataclass
class TenantSummary:
    tenant_id: uuid.UUID
    code: str
    outcome: str = "unchanged"
    counts: dict[str, int] = field(default_factory=dict)
    created: Counter[str] = field(default_factory=Counter)
    students: SchoolStudents | None = None
    """The synthetic students applied (for the manifest file; never printed)."""

    def as_json(self) -> dict[str, Any]:
        return {
            "tenant_id": str(self.tenant_id),
            "code": self.code,
            "outcome": self.outcome,
            "counts": dict(sorted(self.counts.items())),
            "created": {k: v for k, v in sorted(self.created.items()) if v},
        }


@dataclass
class SeedSummary:
    dataset_version: str
    seed: int
    tenants: list[TenantSummary]

    def as_json(self) -> dict[str, Any]:
        return {
            "event": "seed_synthetic.summary",
            "dataset_version": self.dataset_version,
            "seed": self.seed,
            "tenants": [t.as_json() for t in self.tenants],
        }


# --- helpers ---------------------------------------------------------------------------------


def _tenant_status(plan: TenantPlan) -> str | None:
    with context_free_session() as session:
        known = set(tenancy.list_tenant_ids(session, None))
    if plan.tenant_id not in known:
        return None
    with tenant_session(plan.tenant_id) as session:
        tenant = tenancy.get_tenant(session)
    if tenant.code != plan.code:
        raise SeedError(f"tenant {plan.tenant_id} exists with a different code")
    return tenant.status


def _members_by_email(session: Session) -> dict[str, UserOut]:
    out: dict[str, UserOut] = {}
    cursor: uuid.UUID | None = None
    while True:
        page, cursor = identity.list_users(session, limit=PAGE, after=cursor)
        for user in page:
            if user.email:
                out[user.email.lower()] = user
        if cursor is None:
            return out


def _member_context(tenant_id: uuid.UUID, member: UserOut) -> UserContext:
    """The member's effective roles, permissions and scopes (as after an MFA sign-in)."""
    snap = build_snapshot(identity.membership_access(tenant_id, member.id, member.membership_id))
    return UserContext(
        user_id=member.id,
        tenant_id=tenant_id,
        membership_id=member.membership_id,
        roles=snap.roles,
        permissions=snap.permissions,
        scopes=snap.scopes,
        mfa=True,
        auth_time=dt.datetime.now(dt.UTC),
        request_id=REQUEST_ID,
        scoped_permissions=snap.scoped_permissions,
    )


def _ensure_tenant(
    plan: TenantPlan, summary: TenantSummary, wrapper: KeyWrapper, bootstrap: OwnerBootstrap
) -> UserOut:
    """Create/resume the school and its first owner; return the owner (active)."""
    status = _tenant_status(plan)
    if status is None:
        data = TenantProvisionIn(
            code=plan.code,
            name=plan.name,
            boards=list(plan.boards),
            plan_tier="shared",
            deployment_mode="shared",
        )
        try:
            with platform_session() as pdb:
                tenancy.register_tenant(pdb, data, tenant_id=plan.tenant_id)
        except Conflict as exc:
            raise SeedError(
                f"tenant code {plan.code} is used by another tenant; reset the local database "
                "or pick another --code-prefix"
            ) from exc
        summary.outcome = "created"
        summary.created["tenants"] += 1
        status = "provisioning"
    if status not in ("provisioning", "active"):
        raise SeedError(f"tenant {plan.tenant_id} is {status}; only new or active schools")
    if status == "provisioning":
        tenancy.initialise_tenant(plan.tenant_id, wrapper=wrapper)
    with tenant_session(plan.tenant_id) as session:
        owner = _members_by_email(session).get(plan.owner.email)
    if owner is None:
        bootstrap(plan.tenant_id, plan.owner)
        summary.created["members"] += 1
        with tenant_session(plan.tenant_id) as session:
            owner = _members_by_email(session).get(plan.owner.email)
    if owner is None or owner.status != "active" or "owner" not in owner.roles:
        raise SeedError(f"tenant {plan.tenant_id}: the synthetic owner is not an active owner")
    # The platform bootstrap activates the school itself (acceptance needs an active school).
    if _tenant_status(plan) == "provisioning":
        with platform_session() as pdb:
            tenancy.activate_tenant(pdb, plan.tenant_id)
    return owner


def _ensure_structure(
    session: Session, plan: TenantPlan, summary: TenantSummary
) -> tuple[dict[str, uuid.UUID], dict[tuple[str, str], SectionOut]]:
    """Years, classes and sections. Returns class ids by code and current-year sections."""
    years = {y.label: y for y in tenancy.list_academic_years(session)}
    has_current = any(y.is_current for y in years.values())
    for spec in plan.years:
        if spec.label in years:
            continue
        years[spec.label] = tenancy.create_academic_year(
            session,
            AcademicYearCreate(
                label=spec.label,
                starts_on=spec.starts_on,
                ends_on=spec.ends_on,
                is_current=spec.is_current and not has_current,
            ),
        )
        summary.created["academic_years"] += 1
    before = len(tenancy.list_classes(session))
    classes = {c.code: c.id for c in tenancy.ensure_default_classes(session)}
    if len(classes) > before:
        summary.created["classes"] += len(classes) - before
    current: dict[tuple[str, str], SectionOut] = {}
    by_id = {v: k for k, v in classes.items()}
    for spec in plan.years:
        year_id = years[spec.label].id
        existing = {
            (by_id.get(s.class_id, ""), s.name): s
            for s in tenancy.list_sections(session, academic_year_id=year_id)
        }
        for class_code, name in plan.sections:
            section = existing.get((class_code, name))
            if section is None:
                section = tenancy.create_section(
                    session,
                    SectionCreate(
                        academic_year_id=year_id, class_id=classes[class_code], name=name
                    ),
                )
                summary.created["sections"] += 1
            if spec.label == plan.current_year.label:
                current[(class_code, name)] = section
    return classes, current


def _scopes_for(
    spec: StaffSpec,
    classes: dict[str, uuid.UUID],
    sections: dict[tuple[str, str], SectionOut],
) -> list[ScopeIn]:
    if spec.section is not None:
        return [ScopeIn(type="section", ref=sections[spec.section].id)]
    return [ScopeIn(type="class", ref=classes[c]) for c in spec.classes]


def _ensure_staff(
    session: Session,
    ctx: UserContext,
    *,
    plan: TenantPlan,
    summary: TenantSummary,
    classes: dict[str, uuid.UUID],
    sections: dict[tuple[str, str], SectionOut],
) -> None:
    members = _members_by_email(session)
    for spec in plan.staff:
        user = members.get(spec.email)
        if user is None:
            user = identity.invite_user(
                session,
                ctx,
                InviteIn(
                    idp_subject=spec.subject,
                    display_name=spec.display_name,
                    email=spec.email,
                    preferred_language=spec.preferred_language,
                    roles=[spec.role],
                    scopes=_scopes_for(spec, classes, sections),
                ),
            )
            summary.created["members"] += 1
        if user.status == "invited":
            user = identity.set_membership_status(
                session, ctx, user.id, status="active", expected_version=user.version
            )
        if spec.section is not None:
            section = sections[spec.section]
            if section.class_teacher_membership_id is None:
                sections[spec.section] = tenancy.update_section(
                    session,
                    section.id,
                    SectionUpdate(class_teacher_membership_id=user.membership_id),
                    expected_version=section.version,
                )
                summary.created["class_teacher_assignments"] += 1


def _count(session: Session, summary: TenantSummary) -> None:
    members = list(_members_by_email(session).values())
    roles: Counter[str] = Counter(r for m in members for r in m.roles)
    summary.counts = {
        "academic_years": len(tenancy.list_academic_years(session)),
        "classes": len(tenancy.list_classes(session)),
        "sections": len(tenancy.list_sections(session)),
        "members": len(members),
        "members_active": sum(1 for m in members if m.status == "active"),
        **{f"role.{k}": v for k, v in sorted(roles.items())},
    }


def _seed_students(plan: TenantPlan, summary: TenantSummary, options: StudentOptions) -> None:
    """Students, guardians, enrolments and documents, created as the first office admin."""
    school = options.build(plan)
    summary.students = school
    if not school.students:
        return
    spec = next(s for s in plan.staff if s.role == STUDENT_SEEDER_ROLE and s.ordinal == 1)
    with tenant_session(plan.tenant_id) as session:
        member = _members_by_email(session).get(spec.email)
    if member is None or member.status != "active":
        raise SeedError(f"tenant {plan.tenant_id}: the synthetic office admin is not active")
    ctx = _member_context(plan.tenant_id, member)
    created: dict[str, int] = {}
    try:
        student_seeder.seed_students(
            tenant_id=plan.tenant_id,
            user_id=member.id,
            ctx=ctx,
            plan=plan,
            school=school,
            created=created,
        )
        if options.documents:
            student_seeder.seed_documents(
                tenant_id=plan.tenant_id,
                user_id=member.id,
                ctx=ctx,
                plan=plan,
                school=school,
                pages=options.pages(plan, school),
                uploader=options.uploader,
                created=created,
            )
    except student_seeder.StudentSeedError as exc:
        raise SeedError(str(exc)) from exc
    finally:
        summary.created.update({k: v for k, v in created.items() if v})
    with tenant_session(plan.tenant_id, member.id) as session:
        summary.counts["students"] = len(student_seeder.existing_admission_numbers(session, ctx))
        if options.documents:
            summary.counts["documents"] = student_seeder.count_documents(session, ctx)


def seed_tenant(
    plan: TenantPlan,
    *,
    wrapper: KeyWrapper,
    owner_bootstrap: OwnerBootstrap,
    students: StudentOptions | None = None,
) -> TenantSummary:
    summary = TenantSummary(tenant_id=plan.tenant_id, code=plan.code)
    owner = _ensure_tenant(plan, summary, wrapper, owner_bootstrap)
    ctx = _member_context(plan.tenant_id, owner)
    with tenant_session(plan.tenant_id, owner.id) as session:
        classes, sections = _ensure_structure(session, plan, summary)
        _ensure_staff(session, ctx, plan=plan, summary=summary, classes=classes, sections=sections)
    with tenant_session(plan.tenant_id, owner.id) as session:
        _count(session, summary)
    if students is not None:
        _seed_students(plan, summary, students)
    if summary.outcome != "created" and any(summary.created.values()):
        summary.outcome = "updated"
    log.info(
        "devtools.seed_synthetic.tenant",
        tenant_id=str(plan.tenant_id),
        outcome=summary.outcome,
        count=summary.counts["members"],
    )
    return summary


def seed(
    plan: DatasetPlan,
    *,
    wrapper: KeyWrapper,
    owner_bootstrap: OwnerBootstrap,
    students: StudentOptions | None = None,
) -> SeedSummary:
    """Apply ``plan`` (idempotent). Callers must have checked the environment.

    Importing :mod:`app.identity.service` (above) registers the system-role cloning hook that
    :func:`app.tenancy.service.initialise_tenant` runs. ``students`` adds synthetic students,
    guardians, enrolments and documents (docs/12 §3; ``--profile``).
    """
    tenants = [
        seed_tenant(t, wrapper=wrapper, owner_bootstrap=owner_bootstrap, students=students)
        for t in plan.tenants
    ]
    log.info("devtools.seed_synthetic.done", count=len(tenants), outcome="ok")
    return SeedSummary(dataset_version=plan.dataset_version, seed=plan.seed, tenants=tenants)
