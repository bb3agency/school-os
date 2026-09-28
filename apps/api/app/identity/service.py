"""Identity public API: login resolution inputs, me, staff invites, status, roles and scopes.

Other modules (authz, routes) call only these functions, never ``identity.repository``.
Every mutation writes its audit events with ``audit.record`` in the caller's transaction
(CLAUDE.md §6.7) and schedules a permission-cache invalidation for after the commit
(FR-IAM-014). Sessions: login lookups use ``context_free_session``; everything else runs in the
caller's ``tenant_session(ctx.tenant_id, ctx.user_id)``.

Requirements: FR-IAM-010..014, FR-IAM-002 (mfa flag on privileged memberships), US-102.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz import cache
from app.authz.catalog import (
    BREAKGLASS_ROLE,
    CatalogError,
    PermissionDef,
    RoleDef,
    assign_any_roles,
    breakglass_role,
    implicit_permissions,
    mfa_roles,
    permission_catalog,
    system_roles,
)
from app.authz.context import UserContext
from app.core.config import get_settings
from app.core.db import context_free_session, tenant_session
from app.core.errors import Conflict, Forbidden, NotFound, PreconditionFailed, ValidationFailed
from app.core.logging import get_context
from app.identity import repository as repo
from app.identity.models import Membership, Role
from app.identity.schemas import (
    InviteIn,
    LoginChoice,
    MembershipAccess,
    MeOut,
    PermissionOut,
    RoleAccess,
    RoleOut,
    SchoolChoiceOut,
    SchoolChoicesOut,
    ScopeIn,
    ScopeOut,
    SessionSettingsOut,
    StaffMemberOut,
    UserOut,
    UserUpdateIn,
)
from app.tenancy import service as tenancy

OWNER_ROLE = "owner"
PROFILE_FIELDS = ("display_name", "email", "preferred_language")
# Permissions that open the staff directory (GET /staff); either must reach the whole school.
DIRECTORY_PERMISSIONS = ("tenant.structure.manage", "user.manage")

InvitedHook = Callable[[Session, uuid.UUID, uuid.UUID], None]
INVITED_HOOKS: list[InvitedHook] = []
"""``hook(session, tenant_id, user_id)`` after :func:`invite_user` created the membership and
its audit events, in the same transaction (notifications: queue the invitation email)."""
# Allowed membership status transitions (PATCH /users/{id}).
_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "invited": frozenset({"active", "removed"}),
    "active": frozenset({"suspended", "removed"}),
    "suspended": frozenset({"active", "removed"}),
    "removed": frozenset(),
}


# --- helpers ---------------------------------------------------------------------------------


def _request_id(ctx: UserContext | None = None) -> str | None:
    if ctx is not None and ctx.request_id:
        return ctx.request_id
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


@contextmanager
def _db_errors(field: str = "body") -> Iterator[None]:
    try:
        yield
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        if state == "23505":
            raise Conflict("This already exists.", code="duplicate") from exc
        if state == "23503":
            raise ValidationFailed(
                [{"field": field, "code": "not_found", "message_key": "errors.not_found"}]
            ) from exc
        if state in ("23514", "22P02"):
            raise ValidationFailed(
                [{"field": field, "code": "invalid", "message_key": "errors.invalid"}]
            ) from exc
        if state == "42501":
            raise Forbidden() from exc
        raise


def _record(
    session: Session,
    ctx: UserContext | None,
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    summary: Mapping[str, Any],
) -> None:
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        actor_type="user" if ctx is not None else "system",
        actor_id=ctx.user_id if ctx is not None else None,
        request_id=_request_id(ctx),
    )


def _membership_for_user(session: Session, user_id: uuid.UUID) -> Membership:
    rows = repo.list_memberships_for_user(session, user_id)
    if not rows:
        raise NotFound("User not found")
    return rows[0]


def _scope_out(scopes: Iterable[Any]) -> list[ScopeOut]:
    return [ScopeOut(type=s.scope_type, ref=s.scope_ref) for s in scopes]


def _user_out(session: Session, membership: Membership) -> UserOut:
    user = repo.get_user(session, membership.user_id)
    if user is None:  # pragma: no cover - RLS shows users with a membership here
        raise NotFound("User not found")
    return UserOut(
        id=user.id,
        membership_id=membership.id,
        display_name=user.display_name,
        email=user.email,
        preferred_language=user.preferred_language,
        status=membership.status,
        expires_at=membership.expires_at,
        roles=[r.key for r in repo.list_roles_for_membership(session, membership.id)],
        scopes=_scope_out(repo.list_membership_scopes(session, membership.id)),
        last_login_at=user.last_login_at,
        created_at=membership.created_at,
        version=membership.version,
        profile_shared=(repo.user_membership_count(session, user.id) or 0) > 1,
    )


def _roles_by_key(session: Session, keys: Sequence[str]) -> dict[str, Role]:
    if BREAKGLASS_ROLE in keys:
        # 07 §6.4: support access is granted only through the break-glass approval.
        raise Forbidden(
            "SchoolOS support access is given only from the Support access page.",
            code="role_not_grantable",
        )
    roles = {r.key: r for r in repo.list_roles(session)}
    missing = [k for k in keys if k not in roles]
    if missing:
        raise ValidationFailed(
            [{"field": "roles", "code": "unknown_role", "message_key": "errors.unknown_role"}]
        )
    return {k: roles[k] for k in keys}


class RolesRequired(ValidationFailed):
    """PUT roles with an empty list: a member keeps at least one role (use suspend/remove)."""

    code = "roles_required"


def _not_grantable() -> Forbidden:
    return Forbidden(
        "You cannot give or take away a role with more access than your own.",
        code="role_not_grantable",
    )


def _guard_grantable(session: Session, ctx: UserContext, roles: Iterable[Role]) -> None:
    """``role.assign``: nobody may grant or take away a role carrying permissions they do not
    hold, except holders of an ``assign_any_role`` role (the owner, roles.yaml)."""
    if ctx.roles & assign_any_roles():
        return
    role_list = list(roles)
    perms = repo.role_permission_keys(session, [r.id for r in role_list])
    for role in role_list:
        if not perms.get(role.id, set()) <= ctx.permissions:
            raise _not_grantable()


def _guard_invite_roles(session: Session, ctx: UserContext, roles: Iterable[Role]) -> None:
    """``user.manage`` invites (US-102 AC1): any non-privileged system role may be given.
    Privileged roles (MFA roles: owner, principal, office_admin) and custom roles additionally
    need ``role.assign`` and pass the :func:`_guard_grantable` rule."""
    role_list = list(roles)
    privileged = mfa_roles()
    sensitive = [r for r in role_list if not r.is_system or r.key in privileged]
    if not sensitive or ctx.roles & assign_any_roles():
        return
    if not ctx.has("role.assign"):
        raise _not_grantable()
    _guard_grantable(session, ctx, sensitive)


def _guard_not_breakglass(session: Session, membership: Membership) -> None:
    """Temporary support memberships change only through the break-glass workflow."""
    roles = repo.list_roles_for_membership(session, membership.id)
    if any(r.key == BREAKGLASS_ROLE for r in roles):
        raise Conflict(
            "This is temporary SchoolOS support access. End it from the Support access page.",
            code="breakglass_membership",
        )


def _guard_not_own(ctx: UserContext, user_id: uuid.UUID) -> None:
    """Nobody suspends or removes their own membership (as ``own_account`` for operators)."""
    if user_id == ctx.user_id:
        raise Conflict(
            "You cannot change your own access. Ask another administrator to do it.",
            code="own_account",
        )


def _guard_profile_not_shared(session: Session, user_id: uuid.UUID) -> None:
    """ADR-0028: a profile is shared by every school the person belongs to; a school may edit
    it only when the person belongs to that school alone."""
    count = repo.user_membership_count(session, user_id)
    if count is None:  # pragma: no cover - the membership was read in this transaction
        raise NotFound("User not found")
    if count > 1:
        raise Conflict(
            "This person also works at another school on SchoolOS, so their name, email and "
            "language are shared. Ask them to update their profile, or contact SchoolOS support.",
            code="profile_shared",
        )


def _guard_last_owner(session: Session, membership: Membership) -> None:
    owner = repo.get_role_by_key(session, OWNER_ROLE)
    if owner is None:
        return
    holders = repo.active_membership_ids_with_role(session, owner.id)
    if membership.id in holders and len(holders) == 1:
        raise Conflict(
            "The school must keep at least one active owner. Add another owner first.",
            code="last_owner",
        )


# --- login and permission resolution (used by authz) ------------------------------------------


def staff_issuer() -> str:
    """Issuer of school staff identities (``SOS_OIDC_ISSUER``; the tenant token verifier
    accepts only this issuer for staff tokens)."""
    return get_settings().oidc_issuer


def operator_issuer() -> str:
    """Issuer of SchoolOS operator identities: the operator pool, whose support app client signs
    operators in to a school during break-glass (ADR-0023)."""
    return get_settings().resolved_support_issuer


def login_memberships(subject: str, *, support: bool = False) -> list[LoginChoice]:
    """Active, unexpired memberships of the active user ``(issuer, subject)`` (FR-IAM-013).

    Staff principals (``support=False``) resolve in the staff issuer and never reach a
    ``platform_support`` membership. Support principals (ADR-0023) resolve in the operator
    issuer and reach ONLY unexpired memberships holding exactly ``platform_support``
    (``core.resolve_login`` filters; the authz resolver checks again)."""
    issuer = operator_issuer() if support else staff_issuer()
    with context_free_session() as session:
        return [
            LoginChoice(
                user_id=m.user_id,
                tenant_id=m.tenant_id,
                membership_id=m.membership_id,
                tenant_status=m.tenant_status,
            )
            for m in repo.resolve_login(session, subject, issuer=issuer, support_only=support)
        ]


def membership_access(
    tenant_id: uuid.UUID, user_id: uuid.UUID, membership_id: uuid.UUID
) -> MembershipAccess:
    """Roles (with their permissions), scopes and MFA flag of one membership."""
    with tenant_session(tenant_id, user_id) as session:
        membership = repo.get_membership(session, membership_id)
        if membership is None or membership.status != "active":
            return MembershipAccess(roles=(), scopes=(), mfa_required=False)
        roles = repo.list_roles_for_membership(session, membership_id)
        perms = repo.role_permission_keys(session, [r.id for r in roles])
        return MembershipAccess(
            roles=tuple(
                RoleAccess(key=r.key, is_system=r.is_system, permissions=frozenset(perms[r.id]))
                for r in roles
            ),
            scopes=tuple(
                (s.scope_type, s.scope_ref)
                for s in repo.list_membership_scopes(session, membership_id)
            ),
            mfa_required=membership.mfa_required,
        )


def record_login_event(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
    *,
    succeeded: bool,
    reason: str | None = None,
    request_id: str | None = None,
    session_id_present: bool = False,
    issuer_kind: str | None = None,
) -> None:
    """Audit ``auth.login.succeeded`` / ``auth.login.denied`` in the school's own chain.

    ``issuer_kind`` = ``operator_support`` for SchoolOS support sign-ins (ADR-0023)."""
    with tenant_session(tenant_id, user_id) as session:
        summary: dict[str, Any] = {"membership_id": membership_id, "session": session_id_present}
        if reason:
            summary["reason"] = reason
        if issuer_kind:
            summary["issuer_kind"] = issuer_kind
        audit.record(
            session,
            action="auth.login.succeeded" if succeeded else "auth.login.denied",
            resource_type="membership",
            resource_id=membership_id,
            summary=summary,
            actor_type="user",
            actor_id=user_id,
            request_id=request_id or _request_id(),
        )
        if succeeded:
            repo.record_login(session, user_id)


def accept_invitations(subject: str, *, request_id: str | None = None) -> list[uuid.UUID]:
    """Accept the signed-in user's pending invitations on first sign-in (ADR-0019).

    ``subject`` MUST come from a verified access token. Activation and the
    ``membership.invitation_accepted`` audit events share one transaction: the tenant context is
    switched per accepted school (transaction-local ``set_config``) so each event lands in that
    school's own chain. Only the caller's own memberships are involved. Returns the school IDs.
    """
    accepted: list[uuid.UUID] = []
    with context_free_session() as session:
        for tenant_id, membership_id, user_id in repo.accept_invitations(session, subject):
            session.execute(
                text(
                    "SELECT set_config('app.tenant_id', :t, true), "
                    "set_config('app.user_id', :u, true)"
                ),
                {"t": str(tenant_id), "u": str(user_id)},
            )
            audit.record(
                session,
                action="membership.invitation_accepted",
                resource_type="membership",
                resource_id=membership_id,
                summary={"membership_id": membership_id},
                actor_type="user",
                actor_id=user_id,
                request_id=request_id or _request_id(),
            )
            accepted.append(tenant_id)
        session.execute(
            text(
                "SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true)"
            )
        )
    return accepted


# --- me ---------------------------------------------------------------------------------------


def school_choices(choices: Iterable[LoginChoice]) -> SchoolChoicesOut:
    """Schools the principal holds an active membership in (FR-IAM-013).

    Works before a school is chosen. Each school is read in its own ``tenant_session`` (RLS
    ``own_tenant``), so only schools the user belongs to are ever touched. No audit event:
    this is a read of the caller's own memberships.
    """
    out: list[SchoolChoiceOut] = []
    for choice in choices:
        with tenant_session(choice.tenant_id, choice.user_id) as session:
            tenant = tenancy.get_tenant(session)
        out.append(
            SchoolChoiceOut(
                tenant_id=tenant.id, code=tenant.code, name=tenant.name, status=choice.tenant_status
            )
        )
    return SchoolChoicesOut(data=sorted(out, key=lambda s: s.name))


def me(session: Session, ctx: UserContext, *, tenant_ids: Sequence[uuid.UUID]) -> MeOut:
    user = repo.get_user(session, ctx.user_id)
    if user is None:
        raise NotFound("User not found")
    scopes = [ScopeOut(type="school", ref=None)] if ctx.scopes.school else []
    scopes += [ScopeOut(type="class", ref=c) for c in sorted(ctx.scopes.class_ids, key=str)]
    scopes += [ScopeOut(type="section", ref=s) for s in sorted(ctx.scopes.section_ids, key=str)]
    settings = tenancy.session_settings(session)
    return MeOut(
        user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        membership_id=ctx.membership_id,
        display_name=user.display_name,
        preferred_language=user.preferred_language,
        roles=sorted(ctx.roles),
        permissions=sorted(ctx.permissions),
        scopes=scopes,
        mfa=ctx.mfa,
        tenant_ids=sorted(set(tenant_ids), key=str),
        tenant_status=ctx.tenant_status,
        settings=SessionSettingsOut(
            idle_timeout_minutes=settings.idle_timeout_minutes,
            date_format=settings.date_format,
            languages=list(settings.languages),
        ),
    )


def me_in_tenant(ctx: UserContext, *, tenant_ids: Sequence[uuid.UUID]) -> MeOut:
    """:func:`me` in its own transaction (used right after switching the active school)."""
    with tenant_session(ctx.tenant_id, ctx.user_id) as session:
        return me(session, ctx, tenant_ids=tenant_ids)


# --- users (user.manage) ------------------------------------------------------------------------


def list_users(
    session: Session, *, limit: int, after: uuid.UUID | None = None
) -> tuple[list[UserOut], uuid.UUID | None]:
    """Staff of this school ordered by invite time; returns (page, cursor of the last item)."""
    memberships = sorted(repo.list_memberships(session), key=lambda m: (m.created_at, str(m.id)))
    if after is not None:
        ids = [m.id for m in memberships]
        if after not in ids:
            raise ValidationFailed(
                [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid"}]
            )
        memberships = memberships[ids.index(after) + 1 :]
    page = memberships[:limit]
    more = len(memberships) > limit
    return [_user_out(session, m) for m in page], (page[-1].id if more and page else None)


def member_display_names(
    session: Session, membership_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Display names of this school's members, for showing who did something (e.g. who
    requested an export). Unknown ids are left out. No permission check: callers show names
    only next to records the caller may already see."""
    return repo.display_names(session, sorted(set(membership_ids)))


def members_for_users(
    session: Session, user_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    """User id -> (membership id, display name) of this school's members, for showing who did
    something (e.g. who uploaded a document). Unknown ids are left out. No permission check and
    no contact details: callers show them only next to records the caller may already see."""
    return repo.members_by_user(session, sorted(set(user_ids)))


def get_user(session: Session, user_id: uuid.UUID) -> UserOut:
    return _user_out(session, _membership_for_user(session, user_id))


def invite_user(session: Session, ctx: UserContext, data: InviteIn) -> UserOut:
    """US-102: create (or find) the account, an ``invited`` membership, roles and scopes.

    Roles must exist in this school; privileged roles need ``role.assign`` (see
    :func:`_guard_invite_roles`). Memberships with
    a time-bound role (``auditor_readonly``) expire after the role's TTL (07 §6.2); privileged
    roles set ``mfa_required`` (FR-IAM-002). Audit: ``user.invited``, ``membership.created``,
    ``membership.role_granted`` per role and ``membership.scope_added`` per scope.
    """
    roles = _roles_by_key(session, data.roles)
    _guard_invite_roles(session, ctx, roles.values())
    templates: Mapping[str, RoleDef] = system_roles()
    defs = [templates[k] for k in data.roles if k in templates and roles[k].is_system]
    ttls = [d.membership_ttl for d in defs if d.membership_ttl is not None]
    expires_at = dt.datetime.now(dt.UTC) + min(ttls) if ttls else None
    with _db_errors():
        user_id = repo.create_user_for_invite(
            session,
            subject=data.idp_subject,
            issuer=staff_issuer(),
            display_name=data.display_name,
            email=data.email,
            language=data.preferred_language,
        )
        if repo.list_memberships_for_user(session, user_id):
            raise Conflict("This person already has access to this school.", code="duplicate")
        membership = repo.create_membership(
            session,
            user_id=user_id,
            status="invited",
            expires_at=expires_at,
            created_by=ctx.user_id,
            mfa_required=any(d.mfa_required for d in defs),
        )
    _record(
        session,
        ctx,
        action="user.invited",
        resource_type="user",
        resource_id=user_id,
        summary={"membership_id": membership.id, "roles": list(data.roles)},
    )
    _record(
        session,
        ctx,
        action="membership.created",
        resource_type="membership",
        resource_id=membership.id,
        summary={"user_id": user_id, "status": "invited", "time_bound": expires_at is not None},
    )
    for key, role in roles.items():
        repo.add_membership_role(session, membership.id, role.id, granted_by=ctx.user_id)
        _record(
            session,
            ctx,
            action="membership.role_granted",
            resource_type="membership",
            resource_id=membership.id,
            summary={"role_key": key, "role_id": role.id},
        )
    _add_scopes(session, ctx, membership.id, data.scopes)
    cache.invalidate_on_commit(session, ctx.tenant_id, membership.id)
    for hook in INVITED_HOOKS:
        hook(session, ctx.tenant_id, user_id)
    return _user_out(session, membership)


def set_membership_status(
    session: Session, ctx: UserContext, user_id: uuid.UUID, *, status: str, expected_version: int
) -> UserOut:
    """Activate, suspend or remove (FR-IAM-014). Audit: ``membership.status_changed``."""
    membership = _membership_for_user(session, user_id)
    _guard_not_breakglass(session, membership)
    if membership.version != expected_version:
        raise PreconditionFailed("This user was changed by someone else. Reload and try again.")
    previous = membership.status
    if status == previous:
        return _user_out(session, membership)
    if status not in _TRANSITIONS[previous]:
        raise Conflict(f"A {previous} user cannot be made {status}.", code="invalid_state")
    if previous == "active":
        _guard_last_owner(session, membership)
    _guard_not_own(ctx, user_id)
    updated = repo.set_membership_status(
        session, membership.id, status=status, expected_version=expected_version
    )
    _record(
        session,
        ctx,
        action="membership.status_changed",
        resource_type="membership",
        resource_id=membership.id,
        summary={"user_id": user_id, "from": previous, "to": status},
    )
    cache.invalidate_on_commit(session, ctx.tenant_id, membership.id)
    return _user_out(session, updated)


def _same(field: str, old: object, new: object) -> bool:
    if field == "email" and isinstance(old, str) and isinstance(new, str):
        return old.casefold() == new.casefold()  # citext: case-insensitive
    return old == new


def update_user(
    session: Session,
    ctx: UserContext,
    user_id: uuid.UUID,
    data: UserUpdateIn,
    *,
    expected_version: int,
) -> UserOut:
    """``PATCH /users/{id}`` (``user.manage``, step-up, ``If-Match`` on the membership version).

    ``status`` follows :func:`set_membership_status` (transitions, last owner). Profile fields
    (display name, email, language) change the person's account (visible only through their
    membership here, RLS ``users_in_tenant_update``); a removed member's profile is not edited
    (409 ``invalid_state``), and a profile shared with another school is not edited either
    (409 ``profile_shared``, ADR-0028; the whole request is refused). Nobody changes the status
    of their own membership (409 ``own_account``). Unchanged values are ignored; with nothing
    to change the member is returned as is. Otherwise the membership version is bumped once
    (the ETag changes).
    Audit: ``user.profile_updated`` with the changed field NAMES only (never values) and
    ``membership.status_changed`` {from, to}.
    """
    membership = _membership_for_user(session, user_id)
    _guard_not_breakglass(session, membership)
    if membership.version != expected_version:
        raise PreconditionFailed("This user was changed by someone else. Reload and try again.")
    user = repo.get_user(session, user_id)
    if user is None:  # pragma: no cover - RLS shows users with a membership here
        raise NotFound("User not found")
    fields = data.model_fields_set
    profile = {
        k: getattr(data, k)
        for k in PROFILE_FIELDS
        if k in fields and not _same(k, getattr(user, k), getattr(data, k))
    }
    previous = membership.status
    status = data.status if data.status is not None else previous
    if not profile and status == previous:
        return _user_out(session, membership)
    if profile and previous == "removed":
        raise Conflict("This user has been removed.", code="invalid_state")
    if status != previous:
        if status not in _TRANSITIONS[previous]:
            raise Conflict(f"A {previous} user cannot be made {status}.", code="invalid_state")
        if previous == "active":
            _guard_last_owner(session, membership)
        _guard_not_own(ctx, user_id)
    if profile:
        _guard_profile_not_shared(session, user_id)
        with _db_errors():
            repo.update_user_profile(
                session, user_id, expected_version=user.version, values=profile
            )
        _record(
            session,
            ctx,
            action="user.profile_updated",
            resource_type="user",
            resource_id=user_id,
            summary={"membership_id": membership.id, "fields": sorted(profile)},
        )
    updated = repo.set_membership_status(
        session, membership.id, status=status, expected_version=expected_version
    )
    if status != previous:
        _record(
            session,
            ctx,
            action="membership.status_changed",
            resource_type="membership",
            resource_id=membership.id,
            summary={"user_id": user_id, "from": previous, "to": status},
        )
        cache.invalidate_on_commit(session, ctx.tenant_id, membership.id)
    return _user_out(session, updated)


def set_roles(
    session: Session, ctx: UserContext, user_id: uuid.UUID, role_keys: Sequence[str]
) -> UserOut:
    """Replace a member's roles (``role.assign``, step-up). Audit per granted/revoked role.

    An empty list is refused (422 ``roles_required``): suspend or remove the member instead.
    """
    if not role_keys:
        raise RolesRequired(
            [{"field": "roles", "code": "roles_required", "message_key": "errors.roles_required"}],
            detail="A staff member needs at least one role. To stop their access, suspend or "
            "remove them instead.",
        )
    membership = _membership_for_user(session, user_id)
    _guard_not_breakglass(session, membership)
    if membership.status == "removed":
        raise Conflict("This user has been removed.", code="invalid_state")
    wanted = _roles_by_key(session, role_keys)
    current = {r.key: r for r in repo.list_roles_for_membership(session, membership.id)}
    added = [wanted[k] for k in wanted if k not in current]
    revoked = [current[k] for k in current if k not in wanted]
    _guard_grantable(session, ctx, [*added, *revoked])
    if any(r.key == OWNER_ROLE for r in revoked):
        _guard_last_owner(session, membership)
    for role in added:
        repo.add_membership_role(session, membership.id, role.id, granted_by=ctx.user_id)
        _record(
            session,
            ctx,
            action="membership.role_granted",
            resource_type="membership",
            resource_id=membership.id,
            summary={"user_id": user_id, "role_key": role.key, "role_id": role.id},
        )
    for role in revoked:
        repo.remove_membership_role(session, membership.id, role.id)
        _record(
            session,
            ctx,
            action="membership.role_revoked",
            resource_type="membership",
            resource_id=membership.id,
            summary={"user_id": user_id, "role_key": role.key, "role_id": role.id},
        )
    if added or revoked:
        cache.invalidate_on_commit(session, ctx.tenant_id, membership.id)
    return _user_out(session, membership)


def _add_scopes(
    session: Session, ctx: UserContext, membership_id: uuid.UUID, scopes: Iterable[ScopeIn]
) -> None:
    for scope in scopes:
        with _db_errors("scopes"):
            row = repo.add_membership_scope(session, membership_id, scope.type, scope.ref)
        _record(
            session,
            ctx,
            action="membership.scope_added",
            resource_type="membership",
            resource_id=membership_id,
            summary={"scope_id": row.id, "scope_type": scope.type, "scope_ref": scope.ref},
        )


def set_scopes(
    session: Session, ctx: UserContext, user_id: uuid.UUID, scopes: Sequence[ScopeIn]
) -> UserOut:
    """Replace a member's class/section scopes (``role.assign``, step-up; FR-IAM-012)."""
    membership = _membership_for_user(session, user_id)
    _guard_not_breakglass(session, membership)
    if membership.status == "removed":
        raise Conflict("This user has been removed.", code="invalid_state")
    current = {
        (s.scope_type, s.scope_ref): s for s in repo.list_membership_scopes(session, membership.id)
    }
    wanted = {(s.type, s.ref): s for s in scopes}
    for key, row in current.items():
        if key not in wanted:
            repo.remove_membership_scope(session, row.id)
            _record(
                session,
                ctx,
                action="membership.scope_removed",
                resource_type="membership",
                resource_id=membership.id,
                summary={"scope_id": row.id, "scope_type": key[0], "scope_ref": key[1]},
            )
    _add_scopes(session, ctx, membership.id, [s for k, s in wanted.items() if k not in current])
    if current.keys() != wanted.keys():
        cache.invalidate_on_commit(session, ctx.tenant_id, membership.id)
    return _user_out(session, membership)


# --- roles and permissions ----------------------------------------------------------------------


def _role_grantable(ctx: UserContext, role: Role, perms: Collection[str]) -> bool:
    """Whether ``ctx`` may give this role: the rules of :func:`_guard_invite_roles` and, for
    ``role.assign`` holders, :func:`_guard_grantable` (which implies the invite rule)."""
    if ctx.roles & assign_any_roles():
        return True
    if ctx.has("role.assign"):
        return set(perms) <= ctx.permissions
    return role.is_system and role.key not in mfa_roles()


def _role_scoped(role: Role, perms: Collection[str]) -> bool:
    """True when some permission of the role reaches only the member's scopes: the resolver
    treats a grant as school-wide only when roles.yaml grants it school-wide to that system
    role (``build_snapshot``); custom roles and extra grants are scoped."""
    template = system_roles().get(role.key) if role.is_system else None
    for perm in perms:
        grant = template.grant(perm) if template is not None else None
        if grant is None or grant.scoped:
            return True
    return False


def list_roles(session: Session, ctx: UserContext | None = None) -> list[RoleOut]:
    """Assignable roles; the break-glass ``platform_support`` role is never offered.

    ``grantable`` is computed for ``ctx`` (always False without a caller); ``scoped`` says
    whether the role's grants are limited to classes/sections.
    """
    roles = [r for r in repo.list_roles(session) if r.key != BREAKGLASS_ROLE]
    perms = repo.role_permission_keys(session, [r.id for r in roles])
    privileged = mfa_roles()
    return [
        RoleOut(
            id=r.id,
            key=r.key,
            name_en=r.name_en,
            name_te=r.name_te,
            is_system=r.is_system,
            permissions=sorted(perms[r.id]),
            grantable=ctx is not None and _role_grantable(ctx, r, perms[r.id]),
            scoped=_role_scoped(r, perms[r.id]),
            needs_mfa=r.is_system and r.key in privileged,
        )
        for r in roles
    ]


def staff_directory(session: Session, ctx: UserContext) -> list[StaffMemberOut]:
    """Staff who can be picked for a job such as class teacher: active or invited, unexpired
    members of this school with their display name and role keys only (no email, phone or
    sign-in details). Temporary support (break-glass) memberships are left out.

    Permission: ``tenant.structure.manage`` or ``user.manage`` reaching the whole school
    (a scoped grant of either is refused, 403). No step-up, no audit (a read of names staff
    already see next to records).
    """
    if not any(ctx.has(p) and ctx.scope_for(p).school_wide for p in DIRECTORY_PERMISSIONS):
        raise Forbidden()
    now = dt.datetime.now(dt.UTC)
    members = [
        m
        for m in repo.list_memberships(session)
        if m.status in ("active", "invited") and (m.expires_at is None or m.expires_at > now)
    ]
    ids = [m.id for m in members]
    names = repo.display_names(session, ids)
    roles = repo.role_keys_by_membership(session, ids)
    return [
        StaffMemberOut(
            membership_id=m.id,
            display_name=names[m.id],
            roles=sorted(roles.get(m.id, ())),
            status="invited" if m.status == "invited" else "active",
        )
        for m in members
        if m.id in names and BREAKGLASS_ROLE not in roles.get(m.id, ())
    ]


def list_permissions(session: Session) -> list[PermissionOut]:
    """The grantable tenant catalog (no platform or implicit permissions)."""
    implicit = implicit_permissions()
    return [
        PermissionOut(
            key=p.key, description=p.description, sensitivity=p.sensitivity, step_up=p.step_up
        )
        for p in repo.list_permissions(session)
        if not p.is_platform and p.key not in implicit
    ]


# --- break-glass support memberships (docs/07 §6.4, FR-OPS-004) ------------------------------


class BreakglassIdentityMissing(Conflict):
    """Emergency access without a school approver: the operator has no SchoolOS sign-in yet."""


def _ensure_breakglass_role(session: Session) -> Role:
    """The school's ``platform_support`` role, created from roles.yaml on first use."""
    template = breakglass_role()
    role = repo.get_role_by_key(session, BREAKGLASS_ROLE)
    if role is None:
        role = repo.create_role(
            session,
            key=BREAKGLASS_ROLE,
            name_en=template.name_en,
            name_te=template.name_te,
            is_system=True,
        )
        _record(
            session,
            None,
            action="role.created",
            resource_type="role",
            resource_id=role.id,
            summary={
                "role_key": BREAKGLASS_ROLE,
                "is_system": True,
                "permissions": sorted(template.permission_keys),
            },
        )
    have = repo.role_permission_keys(session, [role.id])[role.id]
    for perm in sorted(template.permission_keys - have):
        repo.grant_role_permission(session, role.id, perm)
        _record(
            session,
            None,
            action="role.permission_granted",
            resource_type="role",
            resource_id=role.id,
            summary={"role_key": BREAKGLASS_ROLE, "permission": perm},
        )
    return role


def open_breakglass_membership(
    session: Session,
    ctx: UserContext | None,
    *,
    subject: str,
    issuer: str,
    display_name: str,
    email: str | None,
    expires_at: dt.datetime,
    scopes: Sequence[tuple[str, uuid.UUID | None]],
) -> tuple[uuid.UUID, uuid.UUID]:
    """Give an operator temporary, read-only ``platform_support`` access until ``expires_at``.

    ``ctx`` is the approving owner/principal (``breakglass.approve``, step-up). The operator's
    identity ``(issuer, subject)`` (operator pool, ADR-0023) is created or found with
    ``core.create_user_for_invite`` (the approver is the inviter), so no new definer function is
    needed. A staff account that happens to use the same subject is a different identity and
    is never given the membership: while the subject is taken by another issuer the approval
    fails with 409 ``breakglass_identity_conflict`` (fail closed). ``ctx=None`` is the emergency
    path (two operators confirmed, no school approver): only an existing identity of that
    operator can be used, else :class:`BreakglassIdentityMissing` (fail closed).

    Refuses self-approval and people who already hold ordinary access to this school. A past
    support membership of the same person is reopened (one membership per person and school).
    The membership holds only ``platform_support``, requires MFA, expires with the grant and
    carries ``scopes`` (the request scope). Audit: ``membership.breakglass_opened``.
    Returns (user_id, membership_id).
    """
    if ctx is not None:
        try:
            with _db_errors():
                user_id = repo.create_user_for_invite(
                    session,
                    subject=subject,
                    issuer=issuer,
                    display_name=display_name,
                    email=email,
                    language="en",
                )
        except Conflict as exc:
            raise Conflict(
                "SchoolOS support cannot be given access with this sign-in. "
                "Deny this request and ask SchoolOS support to contact you.",
                code="breakglass_identity_conflict",
            ) from exc
        if user_id == ctx.user_id:
            raise Forbidden("You cannot approve support access for yourself.", code="self_approval")
    else:
        found = repo.find_user_id_by_subject(session, subject, issuer=issuer)
        if found is None:
            raise BreakglassIdentityMissing(
                "The SchoolOS support person has no sign-in for schools yet.",
                code="breakglass_identity_missing",
            )
        user_id = found
    role = _ensure_breakglass_role(session)
    existing = repo.list_memberships_for_user(session, user_id)
    if existing:
        membership = existing[0]
        held = {r.key for r in repo.list_roles_for_membership(session, membership.id)}
        if held != {BREAKGLASS_ROLE}:
            raise Conflict(
                "This person already has access to this school; support access is not needed.",
                code="already_member",
            )
        membership = repo.set_membership_window(
            session, membership.id, status="active", expires_at=expires_at
        )
        for old in repo.list_membership_scopes(session, membership.id):
            repo.remove_membership_scope(session, old.id)
    else:
        with _db_errors():
            membership = repo.create_membership(
                session,
                user_id=user_id,
                status="active",
                expires_at=expires_at,
                created_by=ctx.user_id if ctx is not None else None,
                mfa_required=True,
            )
    granted_by = ctx.user_id if ctx is not None else None
    repo.add_membership_role(session, membership.id, role.id, granted_by=granted_by)
    for scope_type, ref in scopes:
        with _db_errors("scope"):
            repo.add_membership_scope(session, membership.id, scope_type, ref)
    _record(
        session,
        ctx,
        action="membership.breakglass_opened",
        resource_type="membership",
        resource_id=membership.id,
        summary={
            "user_id": user_id,
            "role_key": BREAKGLASS_ROLE,
            "via_breakglass": True,
            "scopes": [{"scope_type": t, "scope_ref": r} for t, r in scopes],
            "approved": ctx is not None,
        },
    )
    cache.invalidate_on_commit(session, membership.tenant_id, membership.id)
    return user_id, membership.id


def close_breakglass_membership(
    session: Session, ctx: UserContext | None, membership_id: uuid.UUID, *, reason: str
) -> None:
    """End a ``platform_support`` membership now (``reason``: revoked | expired).

    The membership is marked removed with ``expires_at`` no later than now, so sign-in
    resolution (``core.resolve_login``) refuses it immediately; the permission cache is dropped
    after commit. Audit: ``membership.breakglass_closed``. ``ctx=None`` for the expiry job.
    """
    membership = repo.get_membership(session, membership_id)
    if membership is None:
        raise NotFound("Membership not found")
    held = {r.key for r in repo.list_roles_for_membership(session, membership_id)}
    if held != {BREAKGLASS_ROLE}:
        raise Conflict("Only support access can be ended here.", code="not_breakglass")
    now = dt.datetime.now(dt.UTC)
    ends = min(membership.expires_at, now) if membership.expires_at is not None else now
    ends = max(ends, membership.created_at + dt.timedelta(microseconds=1))
    repo.set_membership_window(session, membership_id, status="removed", expires_at=ends)
    _record(
        session,
        ctx,
        action="membership.breakglass_closed",
        resource_type="membership",
        resource_id=membership_id,
        summary={"user_id": membership.user_id, "reason": reason, "via_breakglass": True},
    )
    cache.invalidate_on_commit(session, membership.tenant_id, membership_id)


# --- provisioning hook: clone system roles (FR-IAM-011) ---------------------------------------


def clone_system_roles(session: Session, tenant_id: uuid.UUID) -> None:
    """Create the 9 system roles and their permissions in a new school (idempotent).

    Runs inside the new tenant's ``tenant_session`` as ``sos_app`` (``POST_PROVISION_HOOKS``).
    Audit (actor system): ``role.created`` per new role (with its permission keys) and
    ``role.permission_granted`` for grants added to an existing system role on a retry.
    """
    catalog = permission_catalog()
    existing = {r.key: r for r in repo.list_roles(session)}
    for key, template in system_roles().items():
        wanted = sorted(template.permission_keys)
        if any(catalog[p].is_platform for p in wanted):  # pragma: no cover - catalog validated
            raise RuntimeError("platform permission in a system role template")
        role = existing.get(key)
        if role is None:
            role = repo.create_role(
                session, key=key, name_en=template.name_en, name_te=template.name_te, is_system=True
            )
            for perm in wanted:
                repo.grant_role_permission(session, role.id, perm)
            _record(
                session,
                None,
                action="role.created",
                resource_type="role",
                resource_id=role.id,
                summary={"role_key": key, "is_system": True, "permissions": wanted},
            )
            continue
        have = repo.role_permission_keys(session, [role.id])[role.id]
        for perm in wanted:
            if perm not in have:
                repo.grant_role_permission(session, role.id, perm)
                _record(
                    session,
                    None,
                    action="role.permission_granted",
                    resource_type="role",
                    resource_id=role.id,
                    summary={"role_key": key, "permission": perm},
                )
    cache.invalidate_on_commit(session, tenant_id)


# --- system-role sync for existing schools (ADR-0022; FR-IAM-011, SEC-003, SEC-007) ----------

SYSTEM_ROLE_SYNC_VIA = "system_role_sync"
PROTECTED_GRANTS_FILE = "protected_grants.yaml"
"""Versioned list of system-role grants ``--prune`` never removes (``app/authz``, next to
roles.yaml; ADR-0022 amendment 2026-09-27)."""


def parse_protected_grants(
    raw: Any, templates: Mapping[str, RoleDef], catalog: Mapping[str, PermissionDef]
) -> frozenset[tuple[str, str]]:
    """Validate ``protected_grants.yaml``: version 1, system roles, tenant permissions."""
    name = PROTECTED_GRANTS_FILE
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise CatalogError(f"{name}: expected a mapping with version: 1")
    section = raw.get("protected_grants")
    if not isinstance(section, dict) or not section:
        raise CatalogError(f"{name}: 'protected_grants' must be a non-empty mapping")
    pairs: set[tuple[str, str]] = set()
    for role_key, perms in section.items():
        if role_key not in templates:
            raise CatalogError(f"{name}: {role_key!r} is not a system role in roles.yaml")
        if not isinstance(perms, list) or not perms:
            raise CatalogError(f"{name}: {role_key} must list at least one permission")
        for perm in perms:
            pdef = catalog.get(perm) if isinstance(perm, str) else None
            if pdef is None or pdef.is_platform or pdef.implicit:
                raise CatalogError(f"{name}: {role_key} lists {perm!r}, not a tenant permission")
            pairs.add((role_key, perm))
    return frozenset(pairs)


@lru_cache(maxsize=1)
def protected_system_grants() -> frozenset[tuple[str, str]]:
    """(role key, permission) pairs the system-role sync never removes, even with prune.

    Removing one would lock a school out of administering itself (ADR-0022 amendment
    2026-09-27; FR-IAM-011, SEC-003).
    """
    text_ = resources.files("app.authz").joinpath(PROTECTED_GRANTS_FILE).read_text("utf-8")
    return parse_protected_grants(yaml.safe_load(text_), system_roles(), permission_catalog())


@dataclass(frozen=True, slots=True)
class SystemRoleSyncPlan:
    """What bringing one school's system roles in line with ``roles.yaml`` does (keys only).

    ``create_roles``: (role key, permission keys) for system roles missing in the school;
    ``add_grants`` / ``remove_grants``: (role key, permission key) pairs (removals only with
    prune); ``extra_grants``: grants roles.yaml no longer lists that are kept (no prune);
    ``protected_grants``: grants prune would remove but ``protected_grants.yaml`` protects
    (kept; a conflict, like ``conflicts``);
    ``update_names``: system roles whose display names differ from roles.yaml;
    ``conflicts``: roles.yaml keys held by a school's custom role (never touched);
    ``unknown_system_roles``: system roles roles.yaml no longer defines (never deleted).
    """

    tenant_id: uuid.UUID
    applied: bool
    prune: bool
    create_roles: tuple[tuple[str, tuple[str, ...]], ...] = ()
    add_grants: tuple[tuple[str, str], ...] = ()
    remove_grants: tuple[tuple[str, str], ...] = ()
    extra_grants: tuple[tuple[str, str], ...] = ()
    protected_grants: tuple[tuple[str, str], ...] = ()
    update_names: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    unknown_system_roles: tuple[str, ...] = ()

    @property
    def has_conflicts(self) -> bool:
        """True when something needs a person: a custom role on a system key, or a protected
        grant that prune was asked to remove."""
        return bool(self.conflicts or self.protected_grants)

    @property
    def pending(self) -> bool:
        """True when the school differs from roles.yaml in a way this run would change."""
        return bool(self.create_roles or self.add_grants or self.remove_grants or self.update_names)


def missing_catalog_permissions(*, engine: Engine | None = None) -> list[str]:
    """System-role grants in roles.yaml that ``core.permissions`` lacks (migration not applied).

    Reads only the global catalog (no tenant rows) in a ``context_free_session``.
    """
    wanted = {p for r in system_roles().values() for p in r.permission_keys}
    with context_free_session(engine=engine) as session:
        have = {p.key for p in repo.list_permissions(session)}
    return sorted(wanted - have)


def database_role(*, engine: Engine | None = None) -> repo.DatabaseRole:
    """The database role the app engine connects as, with its RLS-relevant attributes."""
    with context_free_session(engine=engine) as session:
        return repo.database_role(session)


def sync_system_roles(
    session: Session, tenant_id: uuid.UUID, *, apply: bool, prune: bool = False
) -> SystemRoleSyncPlan:
    """Bring one school's SYSTEM roles in line with ``roles.yaml`` (ADR-0022).

    Runs in that school's own ``tenant_session`` as ``sos_app`` (RLS applies; no definer
    function). Only roles with ``is_system`` whose key roles.yaml defines are touched: missing
    roles are created, missing grants added and display names updated; grants roles.yaml no
    longer lists are removed only with ``prune``, and never those in ``protected_grants.yaml``
    (the lockout guard: they are reported in ``protected_grants`` as a conflict and kept).
    Custom roles, the break-glass ``platform_support`` role and system roles roles.yaml no
    longer defines are never changed. Scope (school/scoped) and step-up are not stored per
    grant: the resolver reads them from roles.yaml and ``core.permissions`` at request time, so
    they need no reconciliation here.

    ``apply=False`` (dry run) makes the transaction read-only and writes nothing. With
    ``apply=True`` every change is audited in this transaction (actor ``system``, keys only):
    ``role.created``, ``role.permission_granted``, ``role.permission_revoked``, ``role.updated``,
    then one ``role.system_sync_applied`` with the counts. A school already in line gets no
    events (idempotent). Concurrent runs for one school are serialised by an advisory lock.
    """
    if repo.current_tenant_id(session) != tenant_id:
        raise RuntimeError("sync_system_roles needs the school's own tenant_session")
    if apply:
        repo.lock_system_role_sync(session, tenant_id)
    else:
        repo.set_transaction_read_only(session)
    templates = system_roles()
    catalog = permission_catalog()
    roles = repo.list_roles(session)
    by_key = {r.key: r for r in roles}
    grants = repo.role_permission_keys(session, [r.id for r in roles])

    create: list[tuple[str, tuple[str, ...]]] = []
    add: list[tuple[str, str]] = []
    extra: list[tuple[str, str]] = []
    names: list[str] = []
    conflicts: list[str] = []
    for key in sorted(templates):
        template = templates[key]
        wanted = sorted(template.permission_keys)
        if any(catalog[p].is_platform for p in wanted):  # pragma: no cover - catalog validated
            raise RuntimeError("platform permission in a system role template")
        role = by_key.get(key)
        if role is None:
            create.append((key, tuple(wanted)))
            continue
        if not role.is_system:
            conflicts.append(key)
            continue
        have = grants.get(role.id, set())
        add.extend((key, p) for p in wanted if p not in have)
        extra.extend((key, p) for p in sorted(have - template.permission_keys))
        if (role.name_en, role.name_te) != (template.name_en, template.name_te):
            names.append(key)
    unknown = sorted(
        r.key for r in roles if r.is_system and r.key not in templates and r.key != BREAKGLASS_ROLE
    )
    guarded = protected_system_grants()
    plan = SystemRoleSyncPlan(
        tenant_id=tenant_id,
        applied=False,
        prune=prune,
        create_roles=tuple(create),
        add_grants=tuple(add),
        remove_grants=tuple(g for g in extra if g not in guarded) if prune else (),
        extra_grants=() if prune else tuple(extra),
        protected_grants=tuple(g for g in extra if g in guarded) if prune else (),
        update_names=tuple(names),
        conflicts=tuple(conflicts),
        unknown_system_roles=tuple(unknown),
    )
    if not apply or not plan.pending:
        return plan
    _apply_system_role_plan(session, plan, by_key)
    cache.invalidate_on_commit(session, tenant_id)
    return replace(plan, applied=True)


def _apply_system_role_plan(
    session: Session, plan: SystemRoleSyncPlan, by_key: Mapping[str, Role]
) -> None:
    """Write ``plan`` with one audit event per change, then the per-school summary event."""
    templates = system_roles()
    tenant_id, prune = plan.tenant_id, plan.prune
    via = SYSTEM_ROLE_SYNC_VIA
    for key, perms in plan.create_roles:
        template = templates[key]
        role = repo.create_role(
            session, key=key, name_en=template.name_en, name_te=template.name_te, is_system=True
        )
        for perm in perms:
            repo.grant_role_permission(session, role.id, perm)
        _record(
            session,
            None,
            action="role.created",
            resource_type="role",
            resource_id=role.id,
            summary={"role_key": key, "is_system": True, "permissions": list(perms), "via": via},
        )
    for key, perm in plan.add_grants:
        role_id = by_key[key].id
        repo.grant_role_permission(session, role_id, perm)
        _record(
            session,
            None,
            action="role.permission_granted",
            resource_type="role",
            resource_id=role_id,
            summary={"role_key": key, "permission": perm, "via": via},
        )
    for key, perm in plan.remove_grants:
        role_id = by_key[key].id
        if repo.revoke_role_permission(session, role_id, perm):
            _record(
                session,
                None,
                action="role.permission_revoked",
                resource_type="role",
                resource_id=role_id,
                summary={"role_key": key, "permission": perm, "via": via},
            )
    for key in plan.update_names:
        template = templates[key]
        role_id = by_key[key].id
        repo.update_role_names(session, role_id, name_en=template.name_en, name_te=template.name_te)
        _record(
            session,
            None,
            action="role.updated",
            resource_type="role",
            resource_id=role_id,
            summary={"role_key": key, "fields": ["name_en", "name_te"], "via": via},
        )
    _record(
        session,
        None,
        action="role.system_sync_applied",
        resource_type="tenant",
        resource_id=tenant_id,
        summary={
            "roles_created": len(plan.create_roles),
            "grants_added": len(plan.add_grants),
            "grants_removed": len(plan.remove_grants),
            "grants_protected": len(plan.protected_grants),
            "roles_updated": len(plan.update_names),
            "prune": prune,
            "via": via,
        },
    )


def _register_hooks() -> None:
    if clone_system_roles not in tenancy.POST_PROVISION_HOOKS:
        tenancy.POST_PROVISION_HOOKS.append(clone_system_roles)


_register_hooks()
