"""Add-on fee routes: transport bands, hostel, activities (docs/09 "Fees"; FM-02;
FR-FEE-081..100; ADR-0042 follow-up; lead decision D85).

Every route declares its guard; fee permissions are school-wide only (FR-FEE-024): the
:func:`require_any` reads are re-checked school-wide by the service. While fees are off for the
school every route answers 404 ``module_hidden``. No personal data in URLs: whom to assign is a
POST body (class, sections or student ids).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import IdempotencyDep, IfMatch, etag
from app.fees import service
from app.fees.addons_schemas import (
    AddonAssignIn,
    AddonAssignOut,
    AddonCreate,
    AddonOut,
    AddonPatch,
    AddonsOut,
    AddonStudentsOut,
    AddonUnassignIn,
    AddonUnassignOut,
)

router = APIRouter(prefix="/api/v1", tags=["fees"])

R = service
AddonReader = Annotated[UserContext, Depends(require_any(R.ADDON_READ, R.READ, R.MANAGE))]
Manager = Annotated[UserContext, Depends(require(R.MANAGE, scope="school"))]
BASE = "/fees/addons"


@router.get(BASE, response_model=AddonsOut)
def list_fee_addons(
    ctx: AddonReader, db: TenantDB, academic_year_id: uuid.UUID | None = None
) -> AddonsOut:
    """Add-on fees of a year (current unless given) with how many students are on each
    (``fee.addon.read``, ``finance.read`` or ``fee.manage``). Amounts only for
    ``finance.read`` or ``fee.manage`` (``amounts_shown``)."""
    return service.list_addons(db, ctx, academic_year_id=academic_year_id)


@router.post(BASE, response_model=AddonOut, status_code=201)
def create_fee_addon(
    ctx: Manager, db: TenantDB, body: AddonCreate, idem: IdempotencyDep
) -> Response:
    """An add-on fee of one year: a collectable head of an allowed kind (transport, books,
    other), a label and the amount per instalment (``fee.manage``; ``Idempotency-Key``). 422
    ``head_id`` (``invalid``, ``head_kind_not_allowed``, ``head_not_collectable``); 409
    ``addon_label_taken``, ``too_many_addons``."""
    return idem.run(
        db,
        body,
        lambda: service.create_addon(db, ctx, body),
        headers=lambda o: {"ETag": etag(o.version)},
    )


@router.patch(BASE + "/{addon_id}", response_model=AddonOut)
def update_fee_addon(
    *,
    ctx: Manager,
    db: TenantDB,
    addon_id: uuid.UUID,
    body: AddonPatch,
    expected: IfMatch,
    response: Response,
) -> AddonOut:
    """Rename or switch off an add-on; change its amounts only while nobody was ever on it
    (``fee.manage``, ``If-Match``). 409 ``addon_in_use``."""
    out = service.update_addon(db, ctx, addon_id, body, expected_version=expected)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post(BASE + "/{addon_id}/assign", response_model=AddonAssignOut)
def assign_fee_addon(
    ctx: Manager, db: TenantDB, addon_id: uuid.UUID, body: AddonAssignIn
) -> AddonAssignOut:
    """Put a class, some sections or picked students on the add-on from ``start_instalment``
    and post its charges (``fee.manage``; idempotent; ``dry_run`` only counts). With
    ``replace`` (and a ``reason``) a student on another add-on of the same head changes band:
    the old one's unpaid charges from that instalment are proposed as credits for approval.
    409 ``addon_inactive``, ``too_many_students``."""
    return service.assign_addon(db, ctx, addon_id, body)


@router.post(BASE + "/{addon_id}/unassign", response_model=AddonUnassignOut)
def unassign_fee_addon(
    ctx: Manager, db: TenantDB, addon_id: uuid.UUID, body: AddonUnassignIn
) -> AddonUnassignOut:
    """Take students off the add-on from ``from_instalment`` (``fee.manage``; ``dry_run``
    only counts). Unpaid charges from that instalment become credit requests (reason
    ``addon_removal``) that someone else with ``fee.concession.approve`` decides; money already
    paid comes back only through a refund."""
    return service.unassign_addon(db, ctx, addon_id, body)


@router.get(BASE + "/{addon_id}/students", response_model=AddonStudentsOut)
def fee_addon_students(
    ctx: AddonReader, db: TenantDB, addon_id: uuid.UUID, include_ended: bool = False
) -> AddonStudentsOut:
    """Who is on the add-on: class and section, start and end instalment (``fee.addon.read``,
    ``finance.read`` or ``fee.manage``); the charged amount only for ``finance.read`` or
    ``fee.manage``. For a transport in-charge's bus lists."""
    return service.addon_students(db, ctx, addon_id, include_ended=include_ended)
