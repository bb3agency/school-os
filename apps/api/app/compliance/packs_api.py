"""Inspection readiness pack routes (docs/09 "Inspection readiness packs"; FC-07, FO-12;
FR-COMP-024..FR-COMP-045).

Reads declare ``require(compliance.read)``, making a pack ``require(compliance.manage)``,
school-wide. Making one accepts ``Idempotency-Key``. Another school's pack is 404 (RLS). The
index page is school-level (labels, states, dates, counts): no personal data.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Response
from fastapi.responses import HTMLResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import IdempotencyDep
from app.compliance import packs
from app.compliance.packs_page import STYLE_CSP
from app.compliance.packs_schemas import (
    InspectionPackCreate,
    InspectionPackDetailOut,
    InspectionPackOut,
    InspectionReadinessOut,
)

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])

Reader = Annotated[UserContext, Depends(require(packs.READ, scope="school"))]
Manager = Annotated[UserContext, Depends(require(packs.MANAGE, scope="school"))]

_HTML_HEADERS: Final = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": STYLE_CSP,
    "X-Content-Type-Options": "nosniff",
}


@router.get("/inspection-readiness", response_model=InspectionReadinessOut)
def get_inspection_readiness(ctx: Reader, db: TenantDB) -> InspectionReadinessOut:
    """Every inspection pack for the school's boards (DEO/MEO, APSERMC, CBSE, fire department;
    ``verified: false``) with each item's state now: certificates from the register, registers,
    the Appendix IX page, the parents' privacy notice, the DPDP checklist, and evidence not
    tracked in SchoolOS. Permission ``compliance.read``."""
    return packs.readiness(db, ctx)


@router.get("/inspection-packs", response_model=list[InspectionPackOut])
def list_inspection_packs(ctx: Reader, db: TenantDB) -> list[InspectionPackOut]:
    """The packs the office made, newest first (permission ``compliance.read``)."""
    return packs.list_packs(db, ctx)


@router.post("/inspection-packs", response_model=InspectionPackDetailOut, status_code=201)
def generate_inspection_pack(
    ctx: Manager, db: TenantDB, body: InspectionPackCreate, idem: IdempotencyDep
) -> Response:
    """Make a pack: each item's state is kept as it is now, with the academic year, the visit
    date and the registers chosen (choosing registers needs ``register.read``, 403 otherwise).
    Permission ``compliance.manage``. Errors (422): ``unknown_pack``, ``unknown_register``,
    ``academic_year_not_found``. Audited (``compliance.inspection_pack.generated``). Accepts
    ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: packs.generate(db, ctx, body),
        headers=lambda item: {"Location": f"/api/v1/compliance/inspection-packs/{item.id}"},
    )


@router.get("/inspection-packs/{pack_id}", response_model=InspectionPackDetailOut)
def get_inspection_pack(ctx: Reader, db: TenantDB, pack_id: uuid.UUID) -> InspectionPackDetailOut:
    """One pack as it was made (permission ``compliance.read``)."""
    return packs.get_pack(db, ctx, pack_id)


@router.get(
    "/inspection-packs/{pack_id}/index",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}, "description": "Print-ready A4 index"}},
)
def inspection_pack_index(ctx: Reader, db: TenantDB, pack_id: uuid.UUID) -> HTMLResponse:
    """The pack's index as an A4 print page: school, authority, visit date, summary counts and
    one row per item with its state, details and who fixes it (permission ``compliance.read``;
    no personal data)."""
    page = packs.index_page(db, ctx, pack_id)
    name = f"inspection-pack-{str(pack_id)[:8]}.html"
    return HTMLResponse(
        page, headers={**_HTML_HEADERS, "Content-Disposition": f'inline; filename="{name}"'}
    )
