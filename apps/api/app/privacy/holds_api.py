"""Legal hold routes (docs/09 R3 "Legal holds"; ADR-0059; FR-DPDP-039..FR-DPDP-052).

Every route declares ``require(privacy.hold.manage)``, school-wide (owner and principal); every
write asks for step-up and ``If-Match`` (except placing, which creates). Another school's hold
is 404 (RLS). Placing and lifting are maker-checker: the approval must come from another person
(403 ``self_approval_forbidden``).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import IdempotencyDep, IfMatch, etag
from app.privacy import holds_service as service
from app.privacy.holds_schemas import HoldCreate, HoldListOut, HoldOut

router = APIRouter(prefix="/api/v1/privacy", tags=["legal-holds"])

Reader = Annotated[UserContext, Depends(require(service.MANAGE, scope="school"))]
Manager = Annotated[UserContext, Depends(require(service.MANAGE, scope="school", step_up=True))]


def _etag(response: Response, item: HoldOut) -> HoldOut:
    response.headers["ETag"] = etag(item.version)
    return item


@router.get("/holds", response_model=HoldListOut)
def list_holds(ctx: Reader, db: TenantDB) -> HoldListOut:
    """The school's legal holds: open ones by review date, then recent lifted and rejected
    ones, with the reason codes and record classes a hold may name (permission
    ``privacy.hold.manage``)."""
    return service.list_holds(db, ctx)


@router.post("/holds", response_model=HoldOut, status_code=201)
def place_hold(ctx: Manager, db: TenantDB, body: HoldCreate, idem: IdempotencyDep) -> Response:
    """Ask to place a hold on one student or on a record class (permission
    ``privacy.hold.manage``, MFA within 5 minutes). It takes effect when another person with
    the permission approves it. The reference is encrypted and never logged. Errors:
    ``unknown_reason_code``, ``unknown_record_class``, ``student_required``,
    ``record_class_required``, ``not_found``, ``date_in_past``, ``review_too_far``,
    ``aadhaar_full_number_rejected`` (422). Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: service.place(db, ctx, body),
        headers=lambda item: {
            "Location": f"/api/v1/privacy/holds/{item.id}",
            "ETag": etag(item.version),
        },
        # C3 reference: no body kept in Valkey (as breaches, H-01).
        refetch=lambda hold_id: service.get_hold(db, ctx, hold_id),
    )


@router.get("/holds/{hold_id}", response_model=HoldOut)
def get_hold(ctx: Reader, db: TenantDB, hold_id: uuid.UUID, response: Response) -> HoldOut:
    """One hold (permission ``privacy.hold.manage``). ``ETag`` for ``If-Match``."""
    return _etag(response, service.get_hold(db, ctx, hold_id))


@router.post("/holds/{hold_id}:approve", response_model=HoldOut)
def approve_hold(
    ctx: Manager, db: TenantDB, hold_id: uuid.UUID, version: IfMatch, response: Response
) -> HoldOut:
    """Approve a pending hold: it becomes active (another person than the one who asked;
    step-up, ``If-Match``). Errors: ``self_approval_forbidden`` (403), ``hold_wrong_state``
    (409), stale ``If-Match`` (412)."""
    return _etag(response, service.approve(db, ctx, hold_id, version))


@router.post("/holds/{hold_id}:reject", response_model=HoldOut)
def reject_hold(
    ctx: Manager, db: TenantDB, hold_id: uuid.UUID, version: IfMatch, response: Response
) -> HoldOut:
    """Reject a pending hold, or withdraw your own request (step-up, ``If-Match``). Errors:
    ``hold_wrong_state`` (409), stale ``If-Match`` (412)."""
    return _etag(response, service.reject(db, ctx, hold_id, version))


@router.post("/holds/{hold_id}:request-lift", response_model=HoldOut)
def request_lift(
    ctx: Manager, db: TenantDB, hold_id: uuid.UUID, version: IfMatch, response: Response
) -> HoldOut:
    """Ask to lift an active hold; it keeps holding until another person approves (step-up,
    ``If-Match``). Errors: ``hold_wrong_state`` (409), stale ``If-Match`` (412)."""
    return _etag(response, service.request_lift(db, ctx, hold_id, version))


@router.post("/holds/{hold_id}:approve-lift", response_model=HoldOut)
def approve_lift(
    ctx: Manager, db: TenantDB, hold_id: uuid.UUID, version: IfMatch, response: Response
) -> HoldOut:
    """Approve a lift (another person than the one who asked; step-up, ``If-Match``):
    retention and erasure apply again from the next run. Errors: ``self_approval_forbidden``
    (403), ``hold_wrong_state`` (409), stale ``If-Match`` (412)."""
    return _etag(response, service.approve_lift(db, ctx, hold_id, version))


@router.post("/holds/{hold_id}:cancel-lift", response_model=HoldOut)
def cancel_lift(
    ctx: Manager, db: TenantDB, hold_id: uuid.UUID, version: IfMatch, response: Response
) -> HoldOut:
    """Cancel a lift request: the hold stays active (step-up, ``If-Match``). Errors:
    ``hold_wrong_state`` (409), stale ``If-Match`` (412)."""
    return _etag(response, service.cancel_lift(db, ctx, hold_id, version))
