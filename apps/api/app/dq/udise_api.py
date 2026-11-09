"""UDISE+ correction request routes (FO-02; FR-UDI-001..FR-UDI-010; docs/09 Data quality).

``dq.readiness.manage`` prepares, lists, reads and tracks requests; the printable letter needs
``export.portal`` with a recent MFA sign-in (it carries students' names and dates of birth to
an office outside the school). Another school's request id is 404 (RLS). No route changes a
student record (invariant 6).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import HTMLResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import IdempotencyDep, IfMatch, etag
from app.dq import udise_corrections as udise
from app.dq.schemas import PROFILE_PATTERN
from app.dq.udise_letter import STYLE_CSP
from app.dq.udise_schemas import (
    UdiseCandidatesOut,
    UdiseCloseIn,
    UdiseRequestCreate,
    UdiseRequestDetailOut,
    UdiseRequestOut,
    UdiseSentIn,
)

router = APIRouter(prefix="/api/v1/dq/udise-corrections", tags=["data-quality"])

Manager = Annotated[UserContext, Depends(require(udise.MANAGE))]
Printer = Annotated[UserContext, Depends(require(udise.PRINT, step_up=True))]
ProfileQuery = Annotated[
    str, Query(pattern=PROFILE_PATTERN, max_length=64, description="Readiness profile key")
]

_LETTER_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": STYLE_CSP,
    "X-Content-Type-Options": "nosniff",
}


@router.get("/candidates", response_model=UdiseCandidatesOut)
def udise_candidates(
    *,
    ctx: Manager,
    db: TenantDB,
    profile_key: ProfileQuery,
    section_id: Annotated[
        list[uuid.UUID] | None, Query(max_length=200, description="Limit to these sections")
    ] = None,
) -> UdiseCandidatesOut:
    """What a new UDISE+ correction request would ask for now (permission
    ``dq.readiness.manage``): every difference whose fix is the school asking the MEO office to
    correct UDISE+, grouped by field, with the PEN, the value in UDISE+, the correct value and
    its record, the evidence held and whether it matches Aadhaar (yes/no only; never an Aadhaar
    number). Lines already in an open request carry ``requested_in``."""
    return udise.candidates(db, ctx, profile_key, section_ids=section_id)


@router.get("", response_model=list[UdiseRequestOut])
def list_udise_requests(ctx: Manager, db: TenantDB) -> list[UdiseRequestOut]:
    """The school's UDISE+ correction requests, newest first (permission
    ``dq.readiness.manage``; at most 200). Counts and dates only."""
    return udise.list_requests(db, ctx)


@router.post("", response_model=UdiseRequestDetailOut, status_code=201)
def prepare_udise_request(
    ctx: Manager, db: TenantDB, body: UdiseRequestCreate, idem: IdempotencyDep
) -> Response:
    """Prepare a request from the open UDISE+ differences (permission ``dq.readiness.manage``):
    the chosen ``lines`` or every open one not yet requested. 422 ``nothing_to_request`` when
    none is left, ``line_not_open`` for a chosen line without an open difference. Audited with
    counts only; nothing changes on any student record. Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: udise.prepare(db, ctx, body),
        headers=lambda item: {
            "Location": f"/api/v1/dq/udise-corrections/{item.id}",
            "ETag": etag(item.version),
        },
        # Student names and dates of birth: no body kept in Valkey; a replay reads it again.
        refetch=lambda item_id: udise.get_request(db, ctx, item_id),
    )


@router.get("/{correction_id}", response_model=UdiseRequestDetailOut)
def get_udise_request(
    ctx: Manager, db: TenantDB, correction_id: uuid.UUID, response: Response
) -> UdiseRequestDetailOut:
    """One request with each line's state now: ``open``, ``fixed`` (the readiness check no
    longer finds the difference, e.g. after the next UDISE+ list import) or ``unavailable``
    (permission ``dq.readiness.manage``). ``ETag`` is needed to record it sent or closed."""
    out = udise.get_request(db, ctx, correction_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/{correction_id}/sent", response_model=UdiseRequestDetailOut)
def mark_udise_request_sent(
    *,
    ctx: Manager,
    db: TenantDB,
    correction_id: uuid.UUID,
    body: UdiseSentIn,
    version: IfMatch,
    response: Response,
) -> UdiseRequestDetailOut:
    """Record that the request went to the MEO office: the date and the recipient's role
    (permission ``dq.readiness.manage``; ``If-Match``). Only from ``prepared`` (409
    ``udise_request_state``)."""
    out = udise.mark_sent(db, ctx, correction_id, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/{correction_id}/close", response_model=UdiseRequestDetailOut)
def close_udise_request(
    *,
    ctx: Manager,
    db: TenantDB,
    correction_id: uuid.UUID,
    body: UdiseCloseIn,
    version: IfMatch,
    response: Response,
) -> UdiseRequestDetailOut:
    """Close a sent request as ``done`` or ``rejected`` (permission ``dq.readiness.manage``;
    ``If-Match``). Only from ``sent`` (409 ``udise_request_state``)."""
    out = udise.close(db, ctx, correction_id, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get(
    "/{correction_id}/letter",
    response_class=HTMLResponse,
    responses={
        200: {"content": {"text/html": {}}, "description": "Print-ready A4 letter"},
        204: {"description": "With check=true: the letter can be printed now (nothing audited)"},
    },
)
def udise_request_letter(
    *,
    ctx: Printer,
    db: TenantDB,
    correction_id: uuid.UUID,
    check: Annotated[
        bool,
        Query(description="true: only check that the letter can be printed now; 204"),
    ] = False,
) -> Response:
    """The A4 letter to the MEO / MIS coordinator listing the request's open lines (permission
    ``export.portal`` with a recent MFA sign-in, 428 ``step_up_required``): school, PEN,
    student, field, the value in UDISE+, the correct value, its record, the evidence held and
    whether it matches Aadhaar (yes/no; never an Aadhaar number). Audited with counts; with
    ``check=true`` 204 only, not audited. 409 ``nothing_to_print`` when every line is fixed."""
    page = udise.letter(db, ctx, correction_id, check=check)
    if page is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    name = f"udise-correction-request-{str(correction_id)[:8]}.html"
    return HTMLResponse(
        page, headers={**_LETTER_HEADERS, "Content-Disposition": f'inline; filename="{name}"'}
    )
