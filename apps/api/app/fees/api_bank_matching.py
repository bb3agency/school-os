"""Cheque status and deposit list routes (docs/09 "Fees"; FM-08; FR-FEE-131..140; ADR-0042
decision 9). Cheque and bank transfer matching itself runs through the bank statement routes of
``app.fees.api_upi``.

Every route declares its guard; fee permissions are school-wide only (FR-FEE-024). While fees are
off for the school every route answers 404 ``module_hidden``. Query parameters are IST dates and
status codes only (no personal data in URLs).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.fees import service
from app.fees.bank_matching_schemas import (
    ChequeDepositIn,
    ChequeDepositOut,
    ChequeListOut,
    ChequeReturnIn,
    ChequeReturnOut,
    ChequeStatus,
)
from app.fees.templates import STYLE_CSP

router = APIRouter(prefix="/api/v1", tags=["fees"])

R = service
ChequeReader = Annotated[UserContext, Depends(require_any(R.COLLECT, R.RECONCILE))]
Collector = Annotated[UserContext, Depends(require(R.COLLECT, scope="school"))]
Reconciler = Annotated[UserContext, Depends(require(R.RECONCILE, scope="school"))]
_PAGE: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/html": {}}, "description": "A4 print page"}
}


@router.get("/fees/cheques", response_model=ChequeListOut)
def list_fee_cheques(
    ctx: ChequeReader,
    db: TenantDB,
    date_from: Annotated[dt.date | None, Query(alias="from")] = None,
    date_to: Annotated[dt.date | None, Query(alias="to")] = None,
    status: ChequeStatus | None = None,
) -> ChequeListOut:
    """Cheque receipts of a receipt-date range (default the last 30 days; at most 366) with
    their cheque status: received, deposited, cleared (seen in a bank statement) or returned
    (``fee.collect`` or ``fee.reconcile``)."""
    return service.list_cheques(db, ctx, date_from=date_from, date_to=date_to, status=status)


@router.post("/fees/cheques/deposited", response_model=ChequeDepositOut)
def mark_fee_cheques_deposited(
    ctx: Collector, db: TenantDB, body: ChequeDepositIn
) -> ChequeDepositOut:
    """Mark cheques as taken to the bank (``fee.collect``). Receipts that are not issued cheque
    receipts are listed in ``skipped``; marking again is harmless. Audited."""
    return service.mark_cheques_deposited(db, ctx, body)


@router.get("/fees/cheques/deposit-slip/print", response_class=HTMLResponse, responses=_PAGE)
def fee_cheque_deposit_slip(
    ctx: ChequeReader, db: TenantDB, day: dt.date | None = None
) -> HTMLResponse:
    """The A4 list of cheques marked deposited on ``day`` (default today) for the bank's pay-in
    slip (``fee.collect`` or ``fee.reconcile``). Audited."""
    page = service.cheque_deposit_slip_page(db, ctx, day)
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": STYLE_CSP,
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="cheques-deposited.html"',
        },
    )


@router.post("/fees/cheques/{receipt_id}/returned", response_model=ChequeReturnOut)
def mark_fee_cheque_returned(
    ctx: Reconciler, db: TenantDB, receipt_id: uuid.UUID, body: ChequeReturnIn
) -> ChequeReturnOut:
    """The bank returned a deposited cheque (``fee.reconcile``): a ``cheque_dishonoured``
    cancellation request is raised (another holder of ``fee.receipt.cancel`` decides; the ledger
    changes only when it is approved). 409 ``cheque_not_deposited``,
    ``cheque_already_returned``, ``cancellation_pending``; 404 for a non-cheque receipt."""
    return service.mark_cheque_returned(db, ctx, receipt_id, body)
