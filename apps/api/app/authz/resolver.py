"""PrincipalResolver: verified principal -> UserContext (docs/04 §5 step 3; FR-IAM-002, -013, -014).

1. ``core.resolve_login(subject)`` (definer function, via ``identity.service``) lists the user's
   active, unexpired memberships; memberships that are invited, suspended, removed or expired are
   never returned, so those users are refused immediately (no cache involved).
2. Choose the tenant: the BFF's ``X-Active-Tenant`` hint, else the only membership. Several
   memberships and no hint: 409 ``active_tenant_required``. A hint that is not one of the user's
   schools: 403 ``no_membership`` (never reveal whether that school exists).
3. Refuse schools that are not active: 403 ``tenant_suspended`` (suspended/offboarding) or
   ``tenant_unavailable``.
4. Load the permission snapshot (cached 60 s per tenant + membership, invalidated on change).
5. FR-IAM-002: a membership holding a privileged role (roles.yaml ``mfa_required``) or flagged
   ``mfa_required`` needs the MFA claim, else 403 ``mfa_required``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.authz import cache
from app.authz.cache import PermissionSnapshot
from app.authz.catalog import BREAKGLASS_ROLE, implicit_permissions, mfa_roles, system_roles
from app.authz.context import Scopes, UserContext
from app.core.errors import Conflict, Forbidden
from app.identity import service as identity
from app.identity.principal import Principal
from app.identity.schemas import LoginChoice, MembershipAccess

_SUSPENDED = frozenset({"suspended", "offboarding"})


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


class AuthzResolver:
    """Implements ``identity.principal.PrincipalResolver[UserContext]``."""

    def choices(self, principal: Principal) -> list[LoginChoice]:
        return identity.login_memberships(principal.subject)

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
        self, principal: Principal, choice: LoginChoice, *, request_id: str | None = None
    ) -> UserContext:
        if choice.tenant_status in _SUSPENDED:
            raise AccessDenied(
                "This school's access is suspended. Contact SchoolOS support.",
                code="tenant_suspended",
                choice=choice,
            )
        if choice.tenant_status != "active":
            raise AccessDenied(
                "This school is not available.", code="tenant_unavailable", choice=choice
            )
        snap = self.snapshot(choice)
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
        )

    def resolve(
        self, principal: Principal, *, tenant_hint: uuid.UUID | None, request_id: str | None = None
    ) -> UserContext:
        choice = self.choose(self.choices(principal), tenant_hint)
        return self.context_for(principal, choice, request_id=request_id)
