"""UPI and bank statement routes (docs/09 "Fees"; US-2109; FR-FEE-014..017; ADR-0042 decision 9).

Every route declares its guard; fee permissions are school-wide only (FR-FEE-024): single-
permission routes use ``scope="school"`` and :func:`require_any` reads are re-checked school-wide
by the service. While fees are off for the school every route answers 404 ``module_hidden``. No
personal data in URLs: a UTR travels in a POST body; filters are ids and codes.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import OptionalIfMatch, etag
from app.fees import service
from app.fees.upi_schemas import (
    ConfirmIn,
    ConfirmOut,
    IgnoreIn,
    MatchIn,
    RematchOut,
    StatementDetailOut,
    StatementImportIn,
    StatementImportOut,
    StatementLineOut,
    StatementPresetOut,
    UpiExceptionsOut,
    UpiReportOut,
    UpiSettingsIn,
    UpiSettingsOut,
    UtrCheckIn,
    UtrCheckOut,
)

router = APIRouter(prefix="/api/v1", tags=["fees"])

R = service
UpiReader = Annotated[UserContext, Depends(require_any(R.COLLECT, R.MANAGE, R.READ, R.RECONCILE))]
Manager = Annotated[UserContext, Depends(require(R.MANAGE, scope="school"))]
Collector = Annotated[UserContext, Depends(require(R.COLLECT, scope="school"))]
Reconciler = Annotated[UserContext, Depends(require(R.RECONCILE, scope="school"))]
ReportReader = Annotated[UserContext, Depends(require_any(R.RECONCILE, R.READ))]

_SVG: dict[int | str, dict[str, Any]] = {
    200: {"content": {"image/svg+xml": {}}, "description": "The school's static UPI QR (SVG)"}
}
_SVG_HEADERS = {
    "Cache-Control": "private, no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
}


# --- settings and QR (FR-FEE-015) ----------------------------------------------------------------


@router.get("/fees/upi-settings", response_model=UpiSettingsOut)
def get_upi_settings(ctx: UpiReader, db: TenantDB, response: Response) -> UpiSettingsOut:
    """The school's payee name, UPI ID and confirmation window (``fee.collect``, ``fee.manage``,
    ``finance.read`` or ``fee.reconcile``). ``configured`` is false until set."""
    out = service.get_upi_settings(db, ctx)
    response.headers["ETag"] = etag(out.version)
    return out


@router.put("/fees/upi-settings", response_model=UpiSettingsOut)
def put_upi_settings(
    ctx: Manager, db: TenantDB, body: UpiSettingsIn, expected: OptionalIfMatch, response: Response
) -> UpiSettingsOut:
    """Set the payee name and UPI ID (shape checked only, never with a bank) and the days
    within which a UPI receipt should be seen in a bank statement (``fee.manage``; ``If-Match``
    once set)."""
    out = service.put_upi_settings(db, ctx, body, expected_version=expected)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get("/fees/upi-settings/qr.svg", response_class=Response, responses=_SVG)
def upi_qr(ctx: UpiReader, db: TenantDB) -> Response:
    """The school's static UPI QR (its UPI ID and payee name, no amount) as SVG for the counter
    and the settings preview. 404 ``upi_not_set`` until the settings exist."""
    return Response(
        content=service.upi_qr_svg(db, ctx), media_type="image/svg+xml", headers=_SVG_HEADERS
    )


@router.post("/fees/upi/utr-check", response_model=UtrCheckOut)
def check_utr(ctx: Collector, db: TenantDB, body: UtrCheckIn) -> UtrCheckOut:
    """Check a UTR before issuing a UPI receipt (``fee.collect``): 12 digits, not on another
    issued receipt, and whether the bank already reported it."""
    return service.check_utr(db, ctx, body.utr)


# --- statements (FR-FEE-016) ---------------------------------------------------------------------


@router.get("/fees/bank-statement-presets", response_model=list[StatementPresetOut])
def bank_statement_presets(ctx: Reconciler, db: TenantDB) -> list[StatementPresetOut]:
    """The bank statement formats SchoolOS reads (``fee.reconcile``)."""
    return service.statement_presets(db, ctx)


@router.post("/fees/bank-statements", response_model=StatementImportOut, status_code=201)
def import_bank_statement(ctx: Reconciler, body: StatementImportIn, response: Response) -> Any:
    """Read an uploaded bank statement (CSV or XLSX, at most 2 MB and 5,000 transactions;
    ``fee.reconcile``). The file is deleted as soon as it is read; credits are stored and the
    matching rules propose UPI receipts (nothing is confirmed). 422 with row numbers when the
    file cannot be read; 409 ``statement_already_imported``, ``document_not_ready``."""
    out = service.import_statement(ctx, body)
    response.headers["Location"] = f"/api/v1/fees/bank-statements/{out.id}"
    return out


@router.get("/fees/bank-statements", response_model=list[StatementImportOut])
def list_bank_statements(
    ctx: Reconciler, db: TenantDB, limit: Annotated[int, Query(ge=1, le=100)] = 25
) -> list[StatementImportOut]:
    """Imported statements, newest first (``fee.reconcile``)."""
    return service.list_statements(db, ctx, limit=limit)


@router.get("/fees/bank-statements/{statement_id}", response_model=StatementDetailOut)
def get_bank_statement(
    ctx: Reconciler,
    db: TenantDB,
    statement_id: uuid.UUID,
    status: Literal["unmatched", "proposed", "confirmed", "ignored"] | None = None,
) -> StatementDetailOut:
    """A statement's credits with their proposals and exceptions (``fee.reconcile``)."""
    return service.get_statement(db, ctx, statement_id, status=status)


@router.post("/fees/bank-statements/{statement_id}/confirm", response_model=ConfirmOut)
def confirm_bank_statement(
    ctx: Reconciler, db: TenantDB, statement_id: uuid.UUID, body: ConfirmIn
) -> ConfirmOut:
    """Confirm proposed matches in one step (``fee.reconcile``): each becomes a UPI
    confirmation; receipts are never changed. Lines whose proposal no longer holds are listed in
    ``skipped``; confirming again is harmless."""
    return service.confirm_statement(db, ctx, statement_id, body)


@router.post("/fees/bank-statement-lines/{line_id}/confirm", response_model=StatementLineOut)
def confirm_bank_statement_line(
    ctx: Reconciler, db: TenantDB, line_id: uuid.UUID, body: MatchIn
) -> StatementLineOut:
    """Match one credit with a UPI receipt a person chose and confirm it (``fee.reconcile``).
    409 ``amount_mismatch`` / ``utr_differs`` when they disagree (never corrected),
    ``receipt_already_confirmed``, ``line_decided``."""
    return service.match_line(db, ctx, line_id, body)


@router.post("/fees/bank-statement-lines/{line_id}/dismiss", response_model=StatementLineOut)
def dismiss_bank_statement_line(
    ctx: Reconciler, db: TenantDB, line_id: uuid.UUID, body: IgnoreIn
) -> StatementLineOut:
    """Mark a credit as not a fee payment with a reason code (``fee.reconcile``). 409
    ``line_decided`` for a confirmed credit."""
    return service.ignore_line(db, ctx, line_id, body)


@router.post("/fees/bank-statements/rematch", response_model=RematchOut)
def rematch_bank_statements(ctx: Reconciler, db: TenantDB) -> RematchOut:
    """Look again for UPI receipts for recent unmatched credits (receipts issued after the
    statement was imported); proposals only (``fee.reconcile``)."""
    return service.rematch(db, ctx)


# --- exceptions and report (FR-FEE-017) ----------------------------------------------------------


@router.get("/fees/upi-exceptions", response_model=UpiExceptionsOut)
def upi_exceptions(ctx: Reconciler, db: TenantDB) -> UpiExceptionsOut:
    """UPI receipts not seen in a bank statement within the school's window, credits nobody
    matched and confirmed credits whose receipt was cancelled (``fee.reconcile``)."""
    return service.upi_exceptions(db, ctx)


@router.get("/fees/reports/upi", response_model=UpiReportOut)
def upi_report(
    ctx: ReportReader,
    db: TenantDB,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
) -> UpiReportOut:
    """UPI receipts per day: confirmed from the bank vs not yet (``fee.reconcile`` or
    ``finance.read``). Default the last 30 days; at most a year."""
    return service.upi_report(db, ctx, date_from=date_from, date_to=date_to)
