"""PrincipalResolver: verified principal -> UserContext (docs/04 §5 step 3; FR-IAM-002, -013, -014).

1. ``core.resolve_login(subject)`` (definer function, via ``identity.service``) lists the user's
   active, unexpired memberships; memberships that are invited, suspended, removed or expired are
   never returned, so those users are refused immediately (no cache involved).
2. Choose the tenant: the BFF's ``X-Active-Tenant`` hint, else the only membership. Several
   memberships and no hint: 409 ``active_tenant_required``. A hint that is not one of the user's
   schools: 403 ``no_membership`` (never reveal whether that school exists).
3. Refuse schools that are not active: 403 ``tenant_unavailable`` (e.g. still provisioning);
   a suspended or offboarding school answers 403 ``tenant_suspended`` unless the matched route
   and one of the member's roles are on :data:`SUSPENDED_SCHOOL_ALLOWLIST` (BR-08: the owner and
   principal keep "who am I", Plan & billing and the full data export).
4. Load the permission snapshot (cached 60 s per tenant + membership, invalidated on change).
5. FR-IAM-002: a membership holding a privileged role (roles.yaml ``mfa_required``) or flagged
   ``mfa_required`` needs the MFA claim, else 403 ``mfa_required``.
6. Break-glass (ADR-0023 option C, 07 §6.4): a SchoolOS support principal (support app client
   of the operator pool) resolves in the operator issuer and may use ONLY a membership that
   holds exactly ``platform_support`` and has an active grant in ``ops.break_glass_grants``
   (403 ``breakglass_only`` / ``breakglass_grant_inactive``). A staff principal never uses a
   ``platform_support`` membership (403 ``breakglass_only``). ``core.resolve_login`` applies the
   same filters in the database.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from app.authz import cache
from app.authz.cache import PermissionSnapshot
from app.authz.catalog import BREAKGLASS_ROLE, implicit_permissions, mfa_roles, system_roles
from app.authz.context import Scopes, UserContext
from app.core.errors import Conflict, Forbidden
from app.identity import service as identity
from app.identity.principal import Principal
from app.identity.schemas import LoginChoice, MembershipAccess

_SUSPENDED = frozenset({"suspended", "offboarding"})


@dataclass(frozen=True, slots=True)
class RouteKey:
    """The matched route of a request: HTTP method and path template (``{param}`` form)."""

    method: str
    path: str


@dataclass(frozen=True, slots=True)
class SuspendedAccess:
    """One route that members holding one of ``roles`` may still use in a suspended school."""

    method: str
    path: str
    roles: frozenset[str]

    @property
    def key(self) -> RouteKey:
        return RouteKey(self.method, self.path)


SUSPENDED_ROLES: Final = frozenset({"owner", "principal"})
"""System roles that keep billing and export access while a school is suspended (BR-08)."""

ME_ACTIVE_TENANT: Final = RouteKey("POST", "/api/v1/me/active-tenant")
ME_LOGIN_EVENT: Final = RouteKey("POST", "/api/v1/me/login-event")

SUSPENDED_SCHOOL_ALLOWLIST: Final[tuple[SuspendedAccess, ...]] = (
    # Who am I, choosing the school and the sign-in event (the BFF's session flow).
    SuspendedAccess("GET", "/api/v1/me", SUSPENDED_ROLES),
    SuspendedAccess(ME_ACTIVE_TENANT.method, ME_ACTIVE_TENANT.path, SUSPENDED_ROLES),
    SuspendedAccess(ME_LOGIN_EVENT.method, ME_LOGIN_EVENT.path, SUSPENDED_ROLES),
    # Plan & billing (FR-PLT-030): what is owed, so the school can pay and be reactivated.
    SuspendedAccess("GET", "/api/v1/tenant/billing", SUSPENDED_ROLES),
    SuspendedAccess("GET", "/api/v1/tenant/billing/invoices", SUSPENDED_ROLES),
    # FR-ADM-001 full data export: request it, follow it and download it while the archive
    # exists (the route permission tenant.export_all still applies: the owner by default).
    SuspendedAccess("POST", "/api/v1/admin/tenant-export", SUSPENDED_ROLES),
    SuspendedAccess("GET", "/api/v1/admin/tenant-export", SUSPENDED_ROLES),
    SuspendedAccess("GET", "/api/v1/admin/tenant-export/{tenant_export_id}", SUSPENDED_ROLES),
    SuspendedAccess(
        "GET", "/api/v1/admin/tenant-export/{tenant_export_id}/download-url", SUSPENDED_ROLES
    ),
)
"""The only (method, route template, roles) a suspended or offboarding school may still use.

Product decision of 2026-09-27 (BR-08, FR-PLT-004, docs/16 §5.5). Every other school route
answers 403 ``tenant_suspended`` for every role. Routes that never resolve a school
(``GET /me/schools``, ``POST /me/accept-invitations``) are not affected. The route permission
check still applies on top (e.g. ``tenant.billing.read``). Pinned by
``tests/authz/test_suspended_allowlist.py``: change both together."""

SUSPENDED_MESSAGE: Final = (
    "This school's access is suspended. The owner and principal can still open Plan & billing "
    "and download the school's data export. Contact SchoolOS support to restore access."
)


def suspended_access_allowed(route: RouteKey | None, roles: frozenset[str]) -> bool:
    """True when ``route`` is allowlisted for at least one of ``roles`` (unknown route: False)."""
    if route is None:
        return False
    return any(
        entry.key == route and not entry.roles.isdisjoint(roles)
        for entry in SUSPENDED_SCHOOL_ALLOWLIST
    )


class AccessDenied(Forbidden):
    """A 403 for a known membership (the login-event route audits it in that school)."""

    def __init__(self, detail: str, *, code: str, choice: LoginChoice) -> None:
        super().__init__(detail, code=code)
        self.choice = choice


def build_snapshot(access: MembershipAccess) -> PermissionSnapshot:
    """Effective permissions; a permission is school-wide if any system role grants it ✓."""
    templates = system_roles()
    implicit = implicit_permissions()
    permissions: set[str] = set(implicit)
    school_wide: set[str] = set(implicit)
    for role in access.roles:
        template = templates.get(role.key) if role.is_system else None
        for perm in role.permissions:
            permissions.add(perm)
            grant = template.grant(perm) if template is not None else None
            if grant is not None and not grant.scoped:
                school_wide.add(perm)
    scopes = Scopes(
        school=any(t == "school" for t, _ in access.scopes),
        class_ids=frozenset(r for t, r in access.scopes if t == "class" and r is not None),
        section_ids=frozenset(r for t, r in access.scopes if t == "section" and r is not None),
    )
    privileged = mfa_roles()
    return PermissionSnapshot(
        roles=frozenset(r.key for r in access.roles),
        permissions=frozenset(permissions),
        scoped_permissions=frozenset(permissions - school_wide),
        scopes=scopes,
        mfa_required=access.mfa_required
        or any(r.is_system and r.key in privileged for r in access.roles),
    )


BREAKGLASS_ONLY_MESSAGE: Final = (
    "SchoolOS support sign-in works only with the school's approved support access."
)


SupportGrantCheck = Callable[[uuid.UUID, uuid.UUID], bool]
"""(tenant_id, membership_id) -> is there an active break-glass grant for this membership now."""

_support_grant_check: SupportGrantCheck | None = None


def register_support_grant_check(check: SupportGrantCheck) -> None:
    """Installed by ``app.breakglass.service`` at import (authz must not import it: the
    break-glass module bridges to the control plane). Until installed, every support principal
    is refused (fail closed)."""
    global _support_grant_check  # noqa: PLW0603 - one process-wide hook, set at import
    _support_grant_check = check


def _support_grant_active(choice: LoginChoice) -> bool:
    check = _support_grant_check
    return check is not None and check(choice.tenant_id, choice.membership_id)


def _check_breakglass(principal: Principal, choice: LoginChoice, snap: PermissionSnapshot) -> None:
    """ADR-0023: support principals only on break-glass memberships with an active grant; staff
    principals never on a break-glass membership."""
    if principal.kind == "support":
        if snap.roles != frozenset({BREAKGLASS_ROLE}):
            raise AccessDenied(BREAKGLASS_ONLY_MESSAGE, code="breakglass_only", choice=choice)
        if not _support_grant_active(choice):
            raise AccessDenied(
                "The school's approval for SchoolOS support access has ended.",
                code="breakglass_grant_inactive",
                choice=choice,
            )
        return
    if BREAKGLASS_ROLE in snap.roles:
        raise AccessDenied(BREAKGLASS_ONLY_MESSAGE, code="breakglass_only", choice=choice)


class AuthzResolver:
    """Implements ``identity.principal.PrincipalResolver[UserContext]``."""

    def choices(self, principal: Principal) -> list[LoginChoice]:
        return identity.login_memberships(principal.subject, support=principal.kind == "support")

    def choose(self, choices: Sequence[LoginChoice], tenant_hint: uuid.UUID | None) -> LoginChoice:
        if not choices:
            raise Forbidden("You do not have access to any school.", code="no_membership")
        if tenant_hint is not None:
            for choice in choices:
                if choice.tenant_id == tenant_hint:
                    return choice
            raise Forbidden("You do not have access to this school.", code="no_membership")
        if len(choices) > 1:
            raise Conflict("Choose which school to work in.", code="active_tenant_required")
        return choices[0]

    def snapshot(self, choice: LoginChoice) -> PermissionSnapshot:
        cached = cache.get_snapshot(choice.tenant_id, choice.membership_id)
        if cached is not None:
            return cached
        snap = build_snapshot(
            identity.membership_access(choice.tenant_id, choice.user_id, choice.membership_id)
        )
        cache.put_snapshot(choice.tenant_id, choice.membership_id, snap)
        return snap

    def context_for(
        self,
        principal: Principal,
        choice: LoginChoice,
        *,
        request_id: str | None = None,
        route: RouteKey | None = None,
    ) -> UserContext:
        """The request context in ``choice``'s school. ``route`` (the matched route) matters only
        for a suspended school: it must be on :data:`SUSPENDED_SCHOOL_ALLOWLIST` for one of the
        member's roles; ``None`` is refused."""
        suspended = choice.tenant_status in _SUSPENDED
        if not suspended and choice.tenant_status != "active":
            raise AccessDenied(
                "This school is not available.", code="tenant_unavailable", choice=choice
            )
        if suspended and route is None:
            raise AccessDenied(SUSPENDED_MESSAGE, code="tenant_suspended", choice=choice)
        snap = self.snapshot(choice)
        _check_breakglass(principal, choice, snap)
        if suspended and not suspended_access_allowed(route, snap.roles):
            raise AccessDenied(SUSPENDED_MESSAGE, code="tenant_suspended", choice=choice)
        if snap.mfa_required and not principal.mfa:
            raise AccessDenied(
                "Your role needs two-step verification (MFA). Set it up, then sign in again.",
                code="mfa_required",
                choice=choice,
            )
        return UserContext(
            user_id=choice.user_id,
            tenant_id=choice.tenant_id,
            membership_id=choice.membership_id,
            roles=snap.roles,
            permissions=snap.permissions,
            scopes=snap.scopes,
            mfa=principal.mfa,
            auth_time=principal.auth_time,
            request_id=request_id,
            session_id=principal.session_id,
            scoped_permissions=snap.scoped_permissions,
            via_breakglass=BREAKGLASS_ROLE in snap.roles,
            tenant_status=choice.tenant_status,
        )

    def resolve(
        self,
        principal: Principal,
        *,
        tenant_hint: uuid.UUID | None,
        request_id: str | None = None,
        route: RouteKey | None = None,
    ) -> UserContext:
        choice = self.choose(self.choices(principal), tenant_hint)
        return self.context_for(principal, choice, request_id=request_id, route=route)
