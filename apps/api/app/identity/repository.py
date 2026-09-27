"""Database access for identity: users, memberships, roles, permissions and scopes.

Only ``app.identity.service`` (and the authz module's policy code through it) calls this module.
Sessions:
- before a tenant is chosen (login): ``core.db.context_free_session()`` -> definer functions only;
- everything else: ``core.db.tenant_session(tenant_id, user_id)``; RLS limits every statement to
  that tenant and ``core.users`` to people with a membership in it.

Text inputs are NFC-normalised here as a last line of defence (docs/05 §1).
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, delete, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, NotFound, PreconditionFailed
from app.core.ids import new_id
from app.identity.models import (
    Membership,
    MembershipRole,
    MembershipScope,
    Permission,
    Role,
    RolePermission,
    User,
)

# Default lifetime of an auditor_readonly membership (07 §6.2). Applied by the role-assignment
# service; lives here until authz config owns role policies.
AUDITOR_READONLY_DEFAULT_TTL = dt.timedelta(days=14)


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def _current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set; use core.db.tenant_session()")
    return uuid.UUID(str(value))


# --- login and invites (SECURITY DEFINER functions) -----------------------------------------


@dataclass(frozen=True, slots=True)
class LoginMembership:
    """One active, unexpired membership of an active user (``core.resolve_login``)."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    tenant_status: str


def _issuer(issuer: str) -> str:
    # Never call the definer functions without an issuer: NULL means "older API image" there
    # (subject-only matching, ADR-0023 expand phase).
    if not isinstance(issuer, str) or not issuer.strip():
        raise ValueError("an OIDC issuer is required")
    return issuer


def resolve_login(
    session: Session, subject: str, *, issuer: str, support_only: bool = False
) -> list[LoginMembership]:
    """Memberships the identity ``(issuer, subject)`` may sign in to (``context_free_session``).

    ``support_only`` (SchoolOS support principals, ADR-0023): only unexpired memberships holding
    exactly ``platform_support``; otherwise memberships holding ``platform_support`` are never
    returned. Returns [] for unknown or disabled users. The caller picks the tenant
    (FR-IAM-013) and must refuse tenants whose status is not ``active``. Audit:
    ``auth.login.succeeded`` / ``auth.login.denied`` in the chosen tenant.
    """
    rows = session.execute(
        text(
            "SELECT user_id, tenant_id, membership_id, tenant_status "
            "FROM core.resolve_login(:s, :i, :p)"
        ),
        {"s": subject, "i": _issuer(issuer), "p": support_only},
    ).all()
    return [
        LoginMembership(
            user_id=uuid.UUID(str(r.user_id)),
            tenant_id=uuid.UUID(str(r.tenant_id)),
            membership_id=uuid.UUID(str(r.membership_id)),
            tenant_status=str(r.tenant_status),
        )
        for r in rows
    ]


def accept_invitations(
    session: Session, subject: str
) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """Activate the subject's own pending invitations (ADR-0019, ``core.accept_invitations``).

    Returns (tenant_id, membership_id, user_id) for each accepted membership.
    """
    rows = session.execute(
        text("SELECT tenant_id, membership_id, user_id FROM core.accept_invitations(:s)"),
        {"s": subject},
    ).all()
    return [
        (uuid.UUID(str(r.tenant_id)), uuid.UUID(str(r.membership_id)), uuid.UUID(str(r.user_id)))
        for r in rows
    ]


def find_user_id_by_subject(session: Session, subject: str, *, issuer: str) -> uuid.UUID | None:
    value: object = session.execute(
        text("SELECT core.find_user_id_by_subject(:s, :i)"), {"s": subject, "i": _issuer(issuer)}
    ).scalar_one()
    return uuid.UUID(str(value)) if value is not None else None


def create_user_for_invite(
    session: Session,
    *,
    subject: str,
    issuer: str,
    display_name: str,
    email: str | None,
    language: str,
) -> uuid.UUID:
    """Create (or find) the global user ``(issuer, subject)``; returns only the user id.

    ``session`` must be a ``tenant_session(tenant_id, inviter_user_id)`` and the inviter must hold
    an active membership there, else :class:`Forbidden`. The new user stays invisible to the
    tenant until :func:`create_membership` links it. A subject already used by another issuer's
    identity raises ``unique_violation`` (never that identity). Audit: ``user.invited``.
    """
    try:
        value: object = session.execute(
            text("SELECT core.create_user_for_invite(:s, :n, CAST(:e AS public.citext), :l, :i)"),
            {
                "s": subject,
                "n": _nfc(display_name),
                "e": _nfc(email) if email else None,
                "l": language,
                "i": _issuer(issuer),
            },
        ).scalar_one()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42501":
            raise Forbidden() from exc
        raise
    return uuid.UUID(str(value))


# --- users ---------------------------------------------------------------------------------


def get_user(session: Session, user_id: uuid.UUID) -> User | None:
    """The user if they have a membership in the current tenant, else ``None``."""
    return session.get(User, user_id, populate_existing=True)


def update_user_profile(
    session: Session, user_id: uuid.UUID, *, expected_version: int, values: dict[str, Any]
) -> User:
    """Update display_name / email / preferred_language (optimistic locking)."""
    allowed = {"display_name", "email", "preferred_language"}
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"not editable: {sorted(unknown)}")
    clean = {k: (_nfc(v) if isinstance(v, str) else v) for k, v in values.items()}
    user = session.scalars(
        update(User)
        .where(User.id == user_id, User.version == expected_version)
        .values(**clean, version=User.version + 1)
        .returning(User),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()
    if user is None:
        if get_user(session, user_id) is None:
            raise NotFound("User not found")
        raise PreconditionFailed()
    return user


def record_login(session: Session, user_id: uuid.UUID) -> None:
    """Stamp ``last_login_at`` (does not change the profile version)."""
    session.execute(
        update(User).where(User.id == user_id).values(last_login_at=func.now()),
        execution_options={"synchronize_session": False},
    )


# --- memberships ---------------------------------------------------------------------------


def get_membership(session: Session, membership_id: uuid.UUID) -> Membership | None:
    return session.get(Membership, membership_id, populate_existing=True)


def display_names(session: Session, membership_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Membership id -> the member's display name (this school only; RLS)."""
    if not membership_ids:
        return {}
    stmt = (
        select(Membership.id, User.display_name)
        .join(User, User.id == Membership.user_id)
        .where(Membership.id.in_(membership_ids))
    )
    return {row.id: row.display_name for row in session.execute(stmt)}


def members_by_user(
    session: Session, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    """User id -> (membership id, display name) in this school only (RLS)."""
    if not user_ids:
        return {}
    stmt = (
        select(Membership.user_id, Membership.id, User.display_name)
        .join(User, User.id == Membership.user_id)
        .where(Membership.user_id.in_(user_ids))
    )
    return {row.user_id: (row.id, row.display_name) for row in session.execute(stmt)}


def list_memberships_for_user(session: Session, user_id: uuid.UUID) -> list[Membership]:
    """Memberships of ``user_id`` visible in the current tenant (at most one)."""
    return list(session.scalars(select(Membership).where(Membership.user_id == user_id)))


def list_memberships(session: Session, *, status: str | None = None) -> list[Membership]:
    stmt = select(Membership).order_by(Membership.created_at)
    if status is not None:
        stmt = stmt.where(Membership.status == status)
    return list(session.scalars(stmt))


def create_membership(
    session: Session,
    *,
    user_id: uuid.UUID,
    status: str = "invited",
    expires_at: dt.datetime | None = None,
    created_by: uuid.UUID | None = None,
    mfa_required: bool = False,
) -> Membership:
    """Link a user to the current tenant. Audit: ``membership.created``.

    ``expires_at`` is set for time-bound roles (``auditor_readonly``: now +
    :data:`AUDITOR_READONLY_DEFAULT_TTL`; break-glass ``platform_support`` later).
    """
    return session.scalars(
        insert(Membership)
        .values(
            id=new_id(),
            tenant_id=_current_tenant_id(session),
            user_id=user_id,
            status=status,
            expires_at=expires_at,
            created_by=created_by,
            mfa_required=mfa_required,
        )
        .returning(Membership),
        execution_options={"populate_existing": True},
    ).one()


def set_membership_status(
    session: Session, membership_id: uuid.UUID, *, status: str, expected_version: int
) -> Membership:
    """Activate / suspend / remove. Audit: ``membership.status_changed`` {from, to}."""
    membership = session.scalars(
        update(Membership)
        .where(Membership.id == membership_id, Membership.version == expected_version)
        .values(status=status, version=Membership.version + 1)
        .returning(Membership),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()
    if membership is None:
        if get_membership(session, membership_id) is None:
            raise NotFound("Membership not found")
        raise PreconditionFailed()
    return membership


def set_membership_window(
    session: Session, membership_id: uuid.UUID, *, status: str, expires_at: dt.datetime | None
) -> Membership:
    """Set status and expiry together (break-glass memberships only; docs/07 §6.4).

    Used by ``identity.service`` to open or close a temporary ``platform_support`` membership,
    whose window is set by the grant rather than by the staff-management transitions.
    Audit: ``membership.breakglass_opened`` / ``membership.breakglass_closed``.
    """
    membership = session.scalars(
        update(Membership)
        .where(Membership.id == membership_id)
        .values(status=status, expires_at=expires_at, version=Membership.version + 1)
        .returning(Membership),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one_or_none()
    if membership is None:
        raise NotFound("Membership not found")
    return membership


# --- roles and permissions -----------------------------------------------------------------


def list_permissions(session: Session) -> list[Permission]:
    return list(session.scalars(select(Permission).order_by(Permission.key)))


def list_roles(session: Session) -> list[Role]:
    return list(session.scalars(select(Role).order_by(Role.key)))


def get_role(session: Session, role_id: uuid.UUID) -> Role | None:
    return session.get(Role, role_id, populate_existing=True)


def get_role_by_key(session: Session, key: str) -> Role | None:
    return session.scalars(select(Role).where(Role.key == key)).one_or_none()


def create_role(
    session: Session, *, key: str, name_en: str, name_te: str, is_system: bool = False
) -> Role:
    """Audit: ``role.created`` (custom roles go through maker-checker, 07 §6.3)."""
    return session.scalars(
        insert(Role)
        .values(
            id=new_id(),
            tenant_id=_current_tenant_id(session),
            key=key,
            name_en=_nfc(name_en),
            name_te=_nfc(name_te),
            is_system=is_system,
        )
        .returning(Role),
        execution_options={"populate_existing": True},
    ).one()


def grant_role_permission(session: Session, role_id: uuid.UUID, permission_key: str) -> None:
    """Idempotent; the database refuses platform permissions.

    Audit: ``role.permission_granted``.
    """
    session.execute(
        pg_insert(RolePermission)
        .values(
            tenant_id=_current_tenant_id(session), role_id=role_id, permission_key=permission_key
        )
        .on_conflict_do_nothing()
    )


def revoke_role_permission(session: Session, role_id: uuid.UUID, permission_key: str) -> bool:
    """Remove one grant; ``True`` if a row was deleted. Audit: ``role.permission_revoked``."""
    result = session.execute(
        delete(RolePermission).where(
            RolePermission.role_id == role_id, RolePermission.permission_key == permission_key
        )
    )
    return bool(getattr(result, "rowcount", 0))


def update_role_names(session: Session, role_id: uuid.UUID, *, name_en: str, name_te: str) -> None:
    """Set a role's display names (system-role sync). Audit: ``role.updated`` {fields}."""
    session.execute(
        update(Role)
        .where(Role.id == role_id)
        .values(name_en=_nfc(name_en), name_te=_nfc(name_te), version=Role.version + 1)
        .execution_options(synchronize_session=False)
    )


@dataclass(frozen=True, slots=True)
class DatabaseRole:
    """The connected database role (operator commands refuse roles that could bypass RLS)."""

    name: str
    superuser: bool
    bypass_rls: bool


def database_role(session: Session) -> DatabaseRole:
    row = session.execute(
        text(
            "SELECT r.rolname, r.rolsuper, r.rolbypassrls FROM pg_catalog.pg_roles AS r "
            "WHERE r.rolname = current_user"
        )
    ).one()
    return DatabaseRole(name=str(row[0]), superuser=bool(row[1]), bypass_rls=bool(row[2]))


def current_tenant_id(session: Session) -> uuid.UUID:
    """The tenant of the open ``tenant_session`` (RuntimeError when none is set)."""
    return _current_tenant_id(session)


def lock_system_role_sync(session: Session, tenant_id: uuid.UUID) -> None:
    """Serialise system-role syncs of one school (transaction-level advisory lock)."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"sos:system_role_sync:{tenant_id}"},
    )


def set_transaction_read_only(session: Session) -> None:
    """Make the current transaction read-only (dry runs: any write fails in the database)."""
    session.execute(text("SET TRANSACTION READ ONLY"))


def add_membership_role(
    session: Session,
    membership_id: uuid.UUID,
    role_id: uuid.UUID,
    *,
    granted_by: uuid.UUID | None = None,
) -> None:
    """Idempotent. Audit: ``membership.role_granted`` (step-up ``role.assign``)."""
    session.execute(
        pg_insert(MembershipRole)
        .values(
            tenant_id=_current_tenant_id(session),
            membership_id=membership_id,
            role_id=role_id,
            granted_by=granted_by,
        )
        .on_conflict_do_nothing()
    )


def remove_membership_role(session: Session, membership_id: uuid.UUID, role_id: uuid.UUID) -> bool:
    """Audit: ``membership.role_revoked``."""
    result = session.execute(
        delete(MembershipRole).where(
            MembershipRole.membership_id == membership_id, MembershipRole.role_id == role_id
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


def list_roles_for_membership(session: Session, membership_id: uuid.UUID) -> list[Role]:
    return list(
        session.scalars(
            select(Role)
            .join(
                MembershipRole,
                and_(MembershipRole.tenant_id == Role.tenant_id, MembershipRole.role_id == Role.id),
            )
            .where(MembershipRole.membership_id == membership_id)
            .order_by(Role.key)
        )
    )


def permission_keys_for_membership(session: Session, membership_id: uuid.UUID) -> set[str]:
    """Effective permission keys; empty unless the membership is active and unexpired."""
    stmt = (
        select(RolePermission.permission_key)
        .join(
            MembershipRole,
            and_(
                MembershipRole.tenant_id == RolePermission.tenant_id,
                MembershipRole.role_id == RolePermission.role_id,
            ),
        )
        .join(
            Membership,
            and_(
                Membership.tenant_id == MembershipRole.tenant_id,
                Membership.id == MembershipRole.membership_id,
            ),
        )
        .where(
            Membership.id == membership_id,
            Membership.status == "active",
            or_(Membership.expires_at.is_(None), Membership.expires_at > func.now()),
        )
        .distinct()
    )
    return set(session.scalars(stmt))


# --- scopes --------------------------------------------------------------------------------


def add_membership_scope(
    session: Session, membership_id: uuid.UUID, scope_type: str, scope_ref: uuid.UUID | None = None
) -> MembershipScope:
    """``school`` / ``class`` / ``section`` of this tenant (trigger-validated).

    Audit: ``membership.scope_added``.
    """
    return session.scalars(
        insert(MembershipScope)
        .values(
            id=new_id(),
            tenant_id=_current_tenant_id(session),
            membership_id=membership_id,
            scope_type=scope_type,
            scope_ref=scope_ref,
        )
        .returning(MembershipScope),
        execution_options={"populate_existing": True},
    ).one()


def list_membership_scopes(session: Session, membership_id: uuid.UUID) -> list[MembershipScope]:
    return list(
        session.scalars(
            select(MembershipScope)
            .where(MembershipScope.membership_id == membership_id)
            .order_by(MembershipScope.created_at, MembershipScope.id)
        )
    )


def remove_membership_scope(session: Session, scope_id: uuid.UUID) -> bool:
    """Audit: ``membership.scope_removed``."""
    result = session.execute(delete(MembershipScope).where(MembershipScope.id == scope_id))
    return bool(result.rowcount)  # type: ignore[attr-defined]


# --- read helpers for identity.service (authz wave) ------------------------------------------


def role_permission_keys(session: Session, role_ids: list[uuid.UUID]) -> dict[uuid.UUID, set[str]]:
    """Permission keys per role (roles without grants map to an empty set)."""
    out: dict[uuid.UUID, set[str]] = {rid: set() for rid in role_ids}
    if not role_ids:
        return out
    rows = session.execute(
        select(RolePermission.role_id, RolePermission.permission_key).where(
            RolePermission.role_id.in_(role_ids)
        )
    ).all()
    for role_id, key in rows:
        out.setdefault(role_id, set()).add(key)
    return out


def active_membership_ids_with_role(session: Session, role_id: uuid.UUID) -> list[uuid.UUID]:
    """Active, unexpired memberships holding ``role_id`` (last-owner guard)."""
    stmt = (
        select(Membership.id)
        .join(
            MembershipRole,
            and_(
                MembershipRole.tenant_id == Membership.tenant_id,
                MembershipRole.membership_id == Membership.id,
            ),
        )
        .where(
            MembershipRole.role_id == role_id,
            Membership.status == "active",
            or_(Membership.expires_at.is_(None), Membership.expires_at > func.now()),
        )
    )
    return list(session.scalars(stmt))


def role_keys_by_membership(
    session: Session, membership_ids: list[uuid.UUID]
) -> dict[uuid.UUID, set[str]]:
    """Role keys per membership of this school (one query; staff directory)."""
    out: dict[uuid.UUID, set[str]] = {}
    if not membership_ids:
        return out
    rows = session.execute(
        select(MembershipRole.membership_id, Role.key)
        .join(
            Role,
            and_(Role.tenant_id == MembershipRole.tenant_id, Role.id == MembershipRole.role_id),
        )
        .where(MembershipRole.membership_id.in_(membership_ids))
    ).all()
    for membership_id, key in rows:
        out.setdefault(membership_id, set()).add(key)
    return out
