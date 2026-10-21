"""Breach register routes (docs/09 R3 "Breach register and incidents"; ADR-0050; US-2802..US-2804,
FR-DPDP-032..038).

Every route declares its guard: ``require(privacy.breach.read)`` for reads and drafts,
``require(privacy.breach.manage, step_up=True)`` for every write. Both are school-wide (owner,
principal; auditors read). Another school's breach is 404 (RLS).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import IdempotencyDep, IfMatch, etag
from app.privacy import breach_service as service
from app.privacy.breach_schemas import (
    AffectedIn,
    AffectedOut,
    BreachCreate,
    BreachDetailOut,
    BreachOut,
    BreachUpdate,
    DraftOut,
    NoticeDraftOut,
)

router = APIRouter(prefix="/api/v1/privacy", tags=["breaches"])

Reader = Annotated[UserContext, Depends(require(service.READ, scope="school"))]
Manager = Annotated[UserContext, Depends(require(service.MANAGE, scope="school", step_up=True))]


def _headers(item: BreachDetailOut) -> dict[str, str]:
    return {"Location": f"/api/v1/privacy/breaches/{item.id}", "ETag": etag(item.version)}


@router.get("/breaches", response_model=list[BreachOut])
def list_breaches(ctx: Reader, db: TenantDB) -> list[BreachOut]:
    """The school's breach register: open breaches first by their next duty's clock (time left
    in hours), then closed ones (permission ``privacy.breach.read``)."""
    return service.list_breaches(db)


@router.post("/breaches", response_model=BreachDetailOut, status_code=201)
def create_breach(ctx: Manager, db: TenantDB, body: BreachCreate, idem: IdempotencyDep) -> Response:
    """Record a breach: when it was noticed, what happened (encrypted, never logged), the kinds
    of data and approximate numbers of children, guardians and staff (permission
    ``privacy.breach.manage``, MFA within 5 minutes). Accepts ``Idempotency-Key``. Errors:
    ``date_in_future``, ``pulled_automatically``, ``aadhaar_full_number_rejected`` (422)."""
    return idem.run(db, body, lambda: service.create_breach(db, ctx, body), headers=_headers)


@router.get("/breaches/{breach_id}", response_model=BreachDetailOut)
def get_breach(
    ctx: Reader, db: TenantDB, breach_id: uuid.UUID, response: Response
) -> BreachDetailOut:
    """One breach with its texts, three duties and their clocks, and affected counts
    (permission ``privacy.breach.read``). ``ETag`` for ``If-Match`` on PATCH."""
    out = service.get_breach(db, breach_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.patch("/breaches/{breach_id}", response_model=BreachDetailOut)
def update_breach(
    *,
    ctx: Manager,
    db: TenantDB,
    breach_id: uuid.UUID,
    body: BreachUpdate,
    version: IfMatch,
    response: Response,
) -> BreachDetailOut:
    """Change facts or status, or record a duty as done with when and how (permission
    ``privacy.breach.manage``, step-up, ``If-Match``). Errors: ``breach_closed``,
    ``duties_open`` (409), stale ``If-Match`` (412)."""
    out = service.update_breach(db, ctx, breach_id, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get("/breaches/{breach_id}/affected", response_model=AffectedOut)
def get_affected(ctx: Reader, db: TenantDB, breach_id: uuid.UUID) -> AffectedOut:
    """The affected students, guardians and staff (ids only; permission
    ``privacy.breach.read``)."""
    return service.get_affected(db, breach_id)


@router.put("/breaches/{breach_id}/affected", response_model=AffectedOut)
def set_affected(ctx: Manager, db: TenantDB, breach_id: uuid.UUID, body: AffectedIn) -> AffectedOut:
    """Replace the list of affected people, to prepare individual notices (ids of this school
    only, at most 5,000; permission ``privacy.breach.manage``, step-up)."""
    return service.set_affected(db, ctx, breach_id, body)


@router.get("/breaches/{breach_id}/drafts/{draft}", response_model=DraftOut)
def get_draft(
    ctx: Reader,
    db: TenantDB,
    breach_id: uuid.UUID,
    draft: Annotated[str, Path(max_length=20, pattern=r"^(family_notice|board_outline)$")],
) -> DraftOut:
    """The family notice or the Board outline filled from the record and the school's privacy
    contact (``[not set]`` where the school has not filled a value). Nothing is sent
    (permission ``privacy.breach.read``)."""
    return service.draft(
        db, breach_id, "family_notice" if draft == "family_notice" else "board_outline"
    )


@router.post("/breaches/{breach_id}/notice-draft", response_model=NoticeDraftOut, status_code=201)
def create_notice_draft(ctx: Manager, db: TenantDB, breach_id: uuid.UUID) -> NoticeDraftOut:
    """Turn the family notice into a parent notice draft (M4), to be completed, approved and
    sent like any notice; SchoolOS never sends a breach notice by itself (permission
    ``privacy.breach.manage`` with step-up, and ``notice.draft``: 403 otherwise)."""
    return service.create_notice_draft(db, ctx, breach_id)
