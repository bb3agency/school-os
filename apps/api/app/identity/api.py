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
    require_any,
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
    InvitationAnswerOut,
    InvitationsOut,
    InviteIn,
    LoginEventOut,
    MeOut,
    PermissionOut,
    RoleOut,
    RolesIn,
    SchoolChoicesOut,
    ScopesIn,
    StaffMemberOut,
    UserOut,
    UserUpdateIn,
)

router = APIRouter(prefix="/api/v1", tags=["identity"])

Member = Annotated[UserContext, Depends(require(AUTHENTICATED))]
Caller = Annotated[Principal, Depends(require_principal())]
Resolver = Annotated[AuthzResolver, Depends(get_resolver)]
UserManager = Annotated[UserContext, Depends(require("user.manage"))]
UserManagerStepUp = Annotated[UserContext, Depends(require("user.manage", step_up=True))]
RoleAssigner = Annotated[UserContext, Depends(require("role.assign", step_up=True))]
DirectoryReader = Annotated[UserContext, Depends(require_any(*identity.DIRECTORY_PERMISSIONS))]


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
    """The signed-in user in the active school: roles, effective permissions, scopes, language,
    the schools they can switch to and the school's session settings (idle timeout, date format,
    languages) the web applies (permission: any active member)."""
    tenant_ids = [c.tenant_id for c in resolver.choices(principal)]
    return identity.me(db, ctx, tenant_ids=tenant_ids)


@router.post("/me/accept-invitations", response_model=AcceptedInvitationsOut)
def accept_my_invitations(
    principal: Caller, resolver: Resolver, request: Request
) -> AcceptedInvitationsOut:
    """Sign-in acceptance (ADR-0019): accepts a **brand-new account's only invitation**; an
    invitation to a person who already has a SchoolOS account waits for their explicit answer
    on ``/me/invitations`` (audit DL-09). The BFF calls this after the OIDC callback, before
    ``/me/login-event``. Works without ``X-Active-Tenant``; a privileged active membership
    without MFA gets 403 ``mfa_required`` (FR-IAM-002). SchoolOS support sign-ins never accept
    invitations (403 ``breakglass_only``, ADR-0023)."""
    _staff_sign_in_with_mfa_if_needed(principal, resolver)
    accepted = identity.accept_invitations(
        principal.subject, issuer=identity.staff_issuer(), request_id=request_id_of(request)
    )
    return AcceptedInvitationsOut(accepted=accepted)


def _staff_sign_in_with_mfa_if_needed(principal: Principal, resolver: AuthzResolver) -> None:
    """Staff sign-ins only (their tokens carry the staff issuer, which the invitation functions
    match: ADR-0023); support sign-ins are 403 ``breakglass_only``."""
    if principal.kind == "support":
        raise Forbidden(
            "SchoolOS support sign-in works only with the school's approved support access.",
            code="breakglass_only",
        )
    choices = resolver.choices(principal)
    if not principal.mfa and any(resolver.snapshot(c).mfa_required for c in choices):
        raise Forbidden(
            "Your role needs two-step verification (MFA). Set it up, then sign in again.",
            code="mfa_required",
        )


@router.get("/me/invitations", response_model=InvitationsOut)
def list_my_invitations(principal: Caller, resolver: Resolver) -> InvitationsOut:
    """The signed-in person's own open invitations (school name, roles, until when), to accept
    or decline (audit DL-09, ADR-0023 amendment; permission: authenticated, no school needed).
    Support sign-ins get 403 ``breakglass_only``."""
    _staff_sign_in_with_mfa_if_needed(principal, resolver)
    return identity.list_invitations(principal.subject, issuer=identity.staff_issuer())


@router.post("/me/invitations/{membership_id}/accept", response_model=InvitationAnswerOut)
def accept_my_invitation(
    membership_id: uuid.UUID, principal: Caller, resolver: Resolver, request: Request
) -> InvitationAnswerOut:
    """Accept one of your own open invitations: you become a member of that school (audit
    ``membership.invitation_accepted`` in its chain; DL-09). Not yours, already answered or
    expired: 404 ``invitation_not_found``. Support sign-ins: 403 ``breakglass_only``."""
    _staff_sign_in_with_mfa_if_needed(principal, resolver)
    return identity.respond_to_invitation(
        principal.subject,
        issuer=identity.staff_issuer(),
        membership_id=membership_id,
        accept=True,
        request_id=request_id_of(request),
    )


@router.post("/me/invitations/{membership_id}/decline", response_model=InvitationAnswerOut)
def decline_my_invitation(
    membership_id: uuid.UUID, principal: Caller, resolver: Resolver, request: Request
) -> InvitationAnswerOut:
    """Decline one of your own open invitations: the school's membership is removed and it never
    sees your contact details (audit ``membership.invitation_declined`` in its chain; DL-09).
    Not yours, already answered or expired: 404 ``invitation_not_found``."""
    _staff_sign_in_with_mfa_if_needed(principal, resolver)
    return identity.respond_to_invitation(
        principal.subject,
        issuer=identity.staff_issuer(),
        membership_id=membership_id,
        accept=False,
        request_id=request_id_of(request),
    )


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
def update_user(
    *,
    ctx: UserManagerStepUp,
    db: TenantDB,
    user_id: uuid.UUID,
    body: UserUpdateIn,
    version: IfMatch,
    response: Response,
) -> UserOut:
    """Activate, suspend or remove a staff member, and/or change their display name, email or
    language (permission ``user.manage``, step-up; ``If-Match`` required). The last active
    owner cannot be suspended (409 ``last_owner``); a removed member's profile cannot be
    edited (409 ``invalid_state``). Audited with the changed field names only."""
    user = identity.update_user(db, ctx, user_id, body, expected_version=version)
    response.headers["ETag"] = etag(user.version)
    return user


@router.put("/users/{user_id}/roles", response_model=UserOut)
def replace_user_roles(
    ctx: RoleAssigner, db: TenantDB, user_id: uuid.UUID, body: RolesIn
) -> UserOut:
    """Replace a staff member's roles (permission ``role.assign``, step-up). Effective within
    60 s (FR-IAM-014). An empty list answers 422 ``roles_required``: suspend or remove the
    member instead."""
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
    """Roles of this school with their permissions, whether you may give each one
    (``grantable``) and whether it is limited to classes/sections (``scoped``) (permission
    ``user.manage``)."""
    roles = identity.list_roles(db, ctx)
    return paginate(roles, key=lambda r: r.key, cursor=cursor, limit=limit)


@router.get("/permissions", response_model=Page[PermissionOut])
def list_permissions(
    ctx: UserManager, db: TenantDB, limit: Limit = 200, cursor: Cursor = None
) -> Page[PermissionOut]:
    """The grantable permission catalog (permission ``user.manage``)."""
    return paginate(identity.list_permissions(db), key=lambda p: p.key, cursor=cursor, limit=limit)


# --- staff directory ------------------------------------------------------------------------------


@router.get("/staff", response_model=Page[StaffMemberOut])
def list_staff(
    ctx: DirectoryReader, db: TenantDB, limit: Limit = 200, cursor: Cursor = None
) -> Page[StaffMemberOut]:
    """Active and invited staff with display name and role keys only, e.g. to choose a class
    teacher (permission ``tenant.structure.manage`` or ``user.manage``, school-wide; no
    step-up). No emails, phone numbers or sign-in details."""
    staff = identity.staff_directory(db, ctx)
    return paginate(staff, key=lambda m: str(m.membership_id), cursor=cursor, limit=limit)
