"""Identity routes: ``/me``, staff accounts, roles, scopes and the permission catalog.

US-101, US-102; FR-IAM-010..014. Each route declares its permission with ``require()``;
state-changing user and role routes need step-up MFA (428 ``step_up_required``).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.authz import service as authz_service
from app.authz.catalog import AUTHENTICATED
from app.authz.context import UserContext
from app.authz.dependencies import (
    TenantDB,
    get_resolver,
    request_id_of,
    require,
    require_principal,
    tenant_hint,
)
from app.authz.http import (
    Cursor,
    IdempotencyDep,
    IfMatch,
    Limit,
    Page,
    decode_cursor,
    encode_cursor,
    etag,
    paginate,
)
from app.authz.resolver import AuthzResolver
from app.core.errors import Forbidden, ValidationFailed
from app.identity import service as identity
from app.identity.principal import Principal, get_principal
from app.identity.schemas import (
    AcceptedInvitationsOut,
    ActiveTenantIn,
    InviteIn,
    LoginEventOut,
    MembershipStatusIn,
    MeOut,
    PermissionOut,
    RoleOut,
    RolesIn,
    SchoolChoicesOut,
    ScopesIn,
    UserOut,
)

router = APIRouter(prefix="/api/v1", tags=["identity"])

Member = Annotated[UserContext, Depends(require(AUTHENTICATED))]
Caller = Annotated[Principal, Depends(require_principal())]
Resolver = Annotated[AuthzResolver, Depends(get_resolver)]
UserManager = Annotated[UserContext, Depends(require("user.manage"))]
UserManagerStepUp = Annotated[UserContext, Depends(require("user.manage", step_up=True))]
RoleAssigner = Annotated[UserContext, Depends(require("role.assign", step_up=True))]


def _user_headers(user: UserOut) -> dict[str, str]:
    return {"Location": f"/api/v1/users/{user.id}", "ETag": etag(user.version)}


# --- me ---------------------------------------------------------------------------------------


@router.get("/me", response_model=MeOut)
def get_me(
    ctx: Member,
    db: TenantDB,
    principal: Annotated[Principal, Depends(get_principal)],
    resolver: Resolver,
) -> MeOut:
    """The signed-in user in the active school: roles, effective permissions, scopes, language
    and the schools they can switch to (permission: any active member)."""
    tenant_ids = [c.tenant_id for c in resolver.choices(principal)]
    return identity.me(db, ctx, tenant_ids=tenant_ids)


@router.post("/me/accept-invitations", response_model=AcceptedInvitationsOut)
def accept_my_invitations(
    principal: Caller, resolver: Resolver, request: Request
) -> AcceptedInvitationsOut:
    """Accept the signed-in user's pending invitations (ADR-0019). The BFF calls this after the
    OIDC callback, before ``/me/login-event``. Works without ``X-Active-Tenant``; a privileged
    active membership without MFA gets 403 ``mfa_required`` (FR-IAM-002)."""
    choices = resolver.choices(principal)
    if not principal.mfa and any(resolver.snapshot(c).mfa_required for c in choices):
        raise Forbidden(
            "Your role needs two-step verification (MFA). Set it up, then sign in again.",
            code="mfa_required",
        )
    accepted = identity.accept_invitations(principal.subject, request_id=request_id_of(request))
    return AcceptedInvitationsOut(accepted=accepted)


@router.get("/me/schools", response_model=SchoolChoicesOut)
def list_my_schools(principal: Caller, resolver: Resolver) -> SchoolChoicesOut:
    """Schools the signed-in user can work in, for the school picker. Works without
    ``X-Active-Tenant`` (FR-IAM-013; permission: authenticated). A privileged membership
    without MFA gets 403 ``mfa_required``, as on every other route (FR-IAM-002)."""
    choices = resolver.choices(principal)
    if not principal.mfa and any(resolver.snapshot(c).mfa_required for c in choices):
        raise Forbidden(
            "Your role needs two-step verification (MFA). Set it up, then sign in again.",
            code="mfa_required",
        )
    return identity.school_choices(choices)


@router.post("/me/active-tenant", response_model=MeOut)
def set_active_tenant(
    body: ActiveTenantIn, principal: Caller, resolver: Resolver, request: Request
) -> MeOut:
    """Check that the user may work in ``tenant_id`` and return their context there
    (FR-IAM-013; permission: authenticated). The BFF then sends ``X-Active-Tenant``."""
    ctx, tenant_ids = authz_service.switch_active_tenant(
        resolver, principal, body.tenant_id, request_id=request_id_of(request)
    )
    return identity.me_in_tenant(ctx, tenant_ids=tenant_ids)


@router.post("/me/login-event", response_model=LoginEventOut)
def record_login_event(principal: Caller, resolver: Resolver, request: Request) -> LoginEventOut:
    """Called once by the BFF after sign-in: audits ``auth.login.succeeded`` (or
    ``auth.login.denied`` with the reason) in the school's log (permission: authenticated;
    rate-limited per user)."""
    ctx = authz_service.record_login(
        resolver, principal, tenant_hint=tenant_hint(request), request_id=request_id_of(request)
    )
    return LoginEventOut(recorded=True, tenant_id=ctx.tenant_id)


# --- users ------------------------------------------------------------------------------------


@router.get("/users", response_model=Page[UserOut])
def list_users(
    ctx: UserManager, db: TenantDB, limit: Limit = 50, cursor: Cursor = None
) -> Page[UserOut]:
    """Staff accounts of this school (permission ``user.manage``)."""
    after = decode_cursor(cursor)
    after_id: uuid.UUID | None = None
    if after is not None:
        try:
            after_id = uuid.UUID(str(after.get("m")))
        except ValueError as exc:
            raise ValidationFailed(
                [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid_cursor"}]
            ) from exc
    users, last = identity.list_users(db, limit=limit, after=after_id)
    return Page[UserOut](data=users, next_cursor=encode_cursor({"m": str(last)}) if last else None)


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(ctx: UserManager, db: TenantDB, user_id: uuid.UUID, response: Response) -> UserOut:
    """One staff account (permission ``user.manage``). Returns an ``ETag``."""
    user = identity.get_user(db, user_id)
    response.headers["ETag"] = etag(user.version)
    return user


@router.post("/users", response_model=UserOut, status_code=201)
def invite_user(
    ctx: UserManagerStepUp, db: TenantDB, body: InviteIn, idem: IdempotencyDep
) -> Response:
    """Invite a staff member with roles and scopes (permission ``user.manage``, step-up).

    Roles carrying permissions the inviter lacks are refused (403 ``role_not_grantable``).
    Accepts ``Idempotency-Key``.
    """
    return idem.run(db, body, lambda: identity.invite_user(db, ctx, body), headers=_user_headers)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user_status(
    *,
    ctx: UserManagerStepUp,
    db: TenantDB,
    user_id: uuid.UUID,
    body: MembershipStatusIn,
    version: IfMatch,
    response: Response,
) -> UserOut:
    """Activate, suspend or remove a staff member (permission ``user.manage``, step-up;
    ``If-Match`` required). The last active owner cannot be suspended (409 ``last_owner``)."""
    user = identity.set_membership_status(
        db, ctx, user_id, status=body.status, expected_version=version
    )
    response.headers["ETag"] = etag(user.version)
    return user


@router.put("/users/{user_id}/roles", response_model=UserOut)
def replace_user_roles(
    ctx: RoleAssigner, db: TenantDB, user_id: uuid.UUID, body: RolesIn
) -> UserOut:
    """Replace a staff member's roles (permission ``role.assign``, step-up). Effective within
    60 s (FR-IAM-014)."""
    return identity.set_roles(db, ctx, user_id, body.roles)


@router.put("/users/{user_id}/scopes", response_model=UserOut)
def replace_user_scopes(
    ctx: RoleAssigner, db: TenantDB, user_id: uuid.UUID, body: ScopesIn
) -> UserOut:
    """Replace a staff member's class/section scopes (permission ``role.assign``, step-up)."""
    return identity.set_scopes(db, ctx, user_id, body.scopes)


# --- roles and permissions ----------------------------------------------------------------------


@router.get("/roles", response_model=Page[RoleOut])
def list_roles(
    ctx: UserManager, db: TenantDB, limit: Limit = 50, cursor: Cursor = None
) -> Page[RoleOut]:
    """Roles of this school with their permissions (permission ``user.manage``)."""
    return paginate(identity.list_roles(db), key=lambda r: r.key, cursor=cursor, limit=limit)


@router.get("/permissions", response_model=Page[PermissionOut])
def list_permissions(
    ctx: UserManager, db: TenantDB, limit: Limit = 200, cursor: Cursor = None
) -> Page[PermissionOut]:
    """The grantable permission catalog (permission ``user.manage``)."""
    return paginate(identity.list_permissions(db), key=lambda p: p.key, cursor=cursor, limit=limit)
