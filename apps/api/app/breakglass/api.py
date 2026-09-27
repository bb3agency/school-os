"""School-side break-glass routes (US-103, FR-OPS-004, SEC-021; docs/09 §4 Break-glass).

All routes need ``breakglass.approve`` (owner, principal); every change needs step-up MFA.
``{request_id}`` and ``{grant_id}`` are the same object (a row of ``ops.break_glass_grants``):
a request becomes a grant when it is approved. Another school's ID answers 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, Limit, Page
from app.breakglass import service
from app.breakglass.schemas import GrantOut

router = APIRouter(prefix="/api/v1/breakglass", tags=["break-glass"])

Reader = Annotated[UserContext, Depends(require("breakglass.approve"))]
Approver = Annotated[UserContext, Depends(require("breakglass.approve", step_up=True))]
StatusFilter = Literal["requested", "approved", "active", "expired", "revoked", "denied"]


@router.get("/requests", response_model=Page[GrantOut])
def list_requests(
    ctx: Reader,
    db: TenantDB,
    limit: Limit = 50,
    cursor: Cursor = None,
    status: Annotated[StatusFilter | None, Query(description="Only this status.")] = None,
) -> Page[GrantOut]:
    """Support-access requests and grants of this school, newest first. New requests from
    SchoolOS support are fetched first, so a pending request shows up here right away."""
    service.refresh_requests(ctx.tenant_id)
    return service.list_grants(db, limit=limit, cursor=cursor, status=status)


@router.get("/requests/{request_id}", response_model=GrantOut)
def get_request(ctx: Reader, db: TenantDB, request_id: uuid.UUID) -> GrantOut:
    """One request or grant: reason, scope, duration, who asked and its current status."""
    return service.get_grant(db, request_id)


@router.post("/requests/{request_id}/approve", response_model=GrantOut)
def approve_request(ctx: Approver, db: TenantDB, request_id: uuid.UUID) -> GrantOut:
    """Approve (step-up MFA). Access is read-only, limited to the request scope, starts now
    and ends by itself after the requested duration (at most 8 hours)."""
    return service.approve(db, ctx, request_id)


@router.post("/requests/{request_id}/deny", response_model=GrantOut)
def deny_request(ctx: Approver, db: TenantDB, request_id: uuid.UUID) -> GrantOut:
    """Deny (step-up MFA). SchoolOS support gets no access."""
    return service.deny(db, ctx, request_id)


@router.post("/grants/{grant_id}/revoke", response_model=GrantOut)
def revoke_grant(ctx: Approver, db: TenantDB, grant_id: uuid.UUID) -> GrantOut:
    """End support access now (step-up MFA), including emergency access."""
    return service.revoke(db, ctx, grant_id)
