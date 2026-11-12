"""Family counter and fee structure notice routes (docs/09 "Fees"; FM-07 FR-FEE-101..112, FM-10
FR-FEE-113..125; lead decision D86).

Every route declares its guard. Fee permissions are school-wide only (FR-FEE-024): the
:func:`require_any` reads are re-checked school-wide by the service. While fees are off for the
school every route answers 404 ``module_hidden``. No personal data in URLs: ids only.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import HTMLResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import RequiredIdempotencyDep
from app.fees import service
from app.fees.family_schemas import FamilyCounterOut, FamilyPaymentCreate, FamilyPaymentOut
from app.fees.structure_notice import NOTICE_CSP
from app.fees.structure_notice_schemas import FeeStructureChangesOut, FeeStructureNoticeOut
from app.fees.templates import STYLE_CSP

router = APIRouter(prefix="/api/v1", tags=["fees"])

R = service
Viewer = Annotated[UserContext, Depends(require_any(R.COLLECT, R.READ))]
Collector = Annotated[UserContext, Depends(require(R.COLLECT, scope="school"))]
SetupReader = Annotated[UserContext, Depends(require_any(R.READ, R.MANAGE))]

_PAGE: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/html": {}}, "description": "Print-ready page (no scripts)"}
}


def _html(page: str, filename: str, csp: str) -> HTMLResponse:
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": csp,
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )


# --- FM-07 family counter -----------------------------------------------------------------------


@router.get("/fees/students/{student_id}/family", response_model=FamilyCounterOut)
def fee_family_counter(ctx: Viewer, db: TenantDB, student_id: uuid.UUID) -> FamilyCounterOut:
    """The student and each sibling the clerk may see (children sharing a guardian), with their
    open fee accounts, earlier years first (``fee.collect`` or ``finance.read``). 404 when the
    student is not visible. Nothing is written."""
    return service.family_counter(db, ctx, student_id)


@router.post("/fees/family-payments", response_model=FamilyPaymentOut, status_code=201)
def issue_fee_family_payment(
    ctx: Collector, db: TenantDB, body: FamilyPaymentCreate, idem: RequiredIdempotencyDep
) -> Response:
    """One payment split across siblings' accounts: one receipt per account, consecutive
    numbers, all or none (``fee.collect``; send ``Idempotency-Key``). Errors (422): ``mode``
    ``upi_one_receipt_per_utr``; ``parts`` ``too_many``, ``duplicate``, ``not_in_family``; mode
    details and allocations as a counter receipt. (409): ``amount_exceeds_due``,
    ``head_not_collectable``, ``day_closed``, ``account_closed``."""
    return idem.run(db, body, lambda: service.issue_family_payment(db, ctx, body))


@router.get("/fees/family-payments/slip", response_class=HTMLResponse, responses=_PAGE)
def fee_family_payment_slip(
    ctx: Viewer,
    db: TenantDB,
    receipt_ids: Annotated[list[uuid.UUID], Query(min_length=1, max_length=50)],
) -> HTMLResponse:
    """The hand-over slip of a family payment (A4, no scripts): the receipts given to the
    parent, from their frozen content (``fee.collect`` or ``finance.read``). Audited."""
    return _html(service.family_slip(db, ctx, receipt_ids), "family-payment.html", STYLE_CSP)


# --- FM-10 fee structure notice -----------------------------------------------------------------


@router.get("/fees/structure-notice", response_model=FeeStructureNoticeOut)
def fee_structure_notice(
    ctx: SetupReader,
    db: TenantDB,
    academic_year_id: uuid.UUID,
    class_id: uuid.UUID | None = None,
) -> FeeStructureNoticeOut:
    """The school's fee structure for the year by class: heads, annual amounts, instalments and
    the late-fee rule (``finance.read`` or ``fee.manage``). Plans only, no personal data."""
    return service.fee_structure_notice(db, ctx, academic_year_id, class_id=class_id)


@router.get("/fees/structure-notice/print", response_class=HTMLResponse, responses=_PAGE)
def fee_structure_notice_print(
    ctx: SetupReader,
    db: TenantDB,
    academic_year_id: uuid.UUID,
    class_id: uuid.UUID | None = None,
) -> HTMLResponse:
    """The notice as print-ready A4 pages, one per class; a draft plan is marked DRAFT.
    Audited."""
    page = service.fee_structure_notice_print(db, ctx, academic_year_id, class_id=class_id)
    return _html(page, "fee-structure.html", NOTICE_CSP)


@router.get("/fees/structure-changes", response_model=FeeStructureChangesOut)
def fee_structure_changes(
    ctx: SetupReader,
    db: TenantDB,
    academic_year_id: uuid.UUID,
    warn_percent: Annotated[
        Decimal | None, Query(ge=0, le=1000, max_digits=6, decimal_places=1)
    ] = None,
) -> FeeStructureChangesOut:
    """This year's plans against the previous academic year's, per class and head: added,
    removed and changed heads, amounts and percent; increases above ``warn_percent`` (default:
    the school review threshold in config) are flagged (``finance.read`` or ``fee.manage``)."""
    return service.fee_structure_changes(db, ctx, academic_year_id, warn_percent=warn_percent)
