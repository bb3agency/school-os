"""Accountant and management report routes (docs/09 "Fees"; FM-04; FR-FEE-061..075; ADR-0042).

Read-only. Views declare ``finance.read`` (school-wide); downloads declare ``fee.report`` with
step-up and are audited like an export. While fees are off for the school every route answers
404 ``module_hidden``. Query parameters are IST dates, ids, a financial year label and format
choices only (no personal data in URLs).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import HTMLResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.fees import service
from app.fees.period_reports import Download
from app.fees.period_schemas import (
    AgeingOut,
    CollectionPart,
    CollectionRegisterOut,
    DcbOut,
    RegisterKind,
    RegistersOut,
    ReportFormat,
    SeriesOut,
)
from app.fees.templates import STYLE_CSP

router = APIRouter(prefix="/api/v1", tags=["fees"])

R = service
FinanceReader = Annotated[UserContext, Depends(require(R.READ, scope="school"))]
Reporter = Annotated[UserContext, Depends(require(R.REPORT, scope="school", step_up=True))]
DateFrom = Annotated[dt.date | None, Query(alias="from")]
DateTo = Annotated[dt.date | None, Query(alias="to")]
FinancialYear = Annotated[str, Query(min_length=7, max_length=7, pattern=r"^20[0-9]{2}-[0-9]{2}$")]
Format = Annotated[ReportFormat, Query(alias="format")]

_FILE: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {
            "text/csv": {"schema": {"type": "string"}},
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                "schema": {"type": "string", "format": "binary"}
            },
        },
        "description": "CSV or XLSX file",
    }
}
_ZIP: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        "description": "ZIP of CSV files",
    }
}
_PAGE: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/html": {}}, "description": "Print-ready page (no scripts)"}
}


def _send(file: Download) -> Response:
    return Response(
        content=file.content,
        media_type=file.media_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/fees/reports/collections", response_model=CollectionRegisterOut)
def fee_collection_register(
    ctx: FinanceReader, db: TenantDB, date_from: DateFrom = None, date_to: DateTo = None
) -> CollectionRegisterOut:
    """The collection register of an IST date range (``finance.read``; default this month to
    today; at most 366 days): by day, head, mode and collector; cancellations approved and
    refunds paid in the range are listed apart and subtracted. 422 ``date_to`` ``in_future``,
    ``date_from`` (``after_date_to``, ``range_too_long``)."""
    return service.collection_register(db, ctx, date_from, date_to)


@router.get("/fees/reports/collections/print", response_class=HTMLResponse, responses=_PAGE)
def fee_collection_register_print(
    ctx: FinanceReader, db: TenantDB, date_from: DateFrom = None, date_to: DateTo = None
) -> HTMLResponse:
    """The collection register as an A4 print page (``finance.read``). Audited."""
    page = service.collection_print(db, ctx, date_from, date_to)
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": STYLE_CSP,
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="collection-register.html"',
        },
    )


@router.get("/fees/reports/collections/download", response_class=Response, responses=_FILE)
def fee_collection_register_download(
    ctx: Reporter,
    db: TenantDB,
    *,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    part: CollectionPart = "receipts",
    fmt: Format = "csv",
) -> Response:
    """The collection register as CSV or XLSX (``fee.report``, step-up): ``part`` receipts (one
    row per receipt, cancellation and refund), heads, days, modes or collectors. Audited."""
    return _send(service.collection_download(db, ctx, date_from, date_to, part=part, fmt=fmt))


@router.get("/fees/reports/dcb", response_model=DcbOut)
def fee_dcb(ctx: FinanceReader, db: TenantDB, academic_year_id: uuid.UUID | None = None) -> DcbOut:
    """Demand, collection and balance per class and section for an academic year (default the
    current one; ``finance.read``), with write-offs apart and the earlier-year dues still open.
    404 for a year that is not this school's."""
    return service.dcb(db, ctx, academic_year_id)


@router.get("/fees/reports/dcb/download", response_class=Response, responses=_FILE)
def fee_dcb_download(
    ctx: Reporter, db: TenantDB, academic_year_id: uuid.UUID | None = None, fmt: Format = "csv"
) -> Response:
    """The DCB as CSV or XLSX (``fee.report``, step-up). Audited."""
    return _send(service.dcb_download(db, ctx, academic_year_id, fmt=fmt))


@router.get("/fees/reports/registers", response_model=RegistersOut)
def fee_registers(
    ctx: FinanceReader, db: TenantDB, date_from: DateFrom = None, date_to: DateTo = None
) -> RegistersOut:
    """Concessions and adjustments approved (write-offs included), cancellations approved and
    refunds paid in an IST date range (``finance.read``), with who asked and who approved. No
    free-text reasons."""
    return service.registers(db, ctx, date_from, date_to)


@router.get("/fees/reports/registers/download", response_class=Response, responses=_FILE)
def fee_registers_download(
    ctx: Reporter,
    db: TenantDB,
    *,
    kind: RegisterKind,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    fmt: Format = "csv",
    include_reasons: bool = False,
) -> Response:
    """One register as CSV or XLSX (``fee.report``, step-up); the typed reasons only with
    ``include_reasons``. Audited."""
    return _send(
        service.register_download(
            db, ctx, date_from, date_to, kind=kind, fmt=fmt, include_reasons=include_reasons
        )
    )


@router.get("/fees/reports/ageing", response_model=AgeingOut)
def fee_ageing(ctx: FinanceReader, db: TenantDB) -> AgeingOut:
    """Overdue amounts today by days past due (``finance.read``), earlier-year dues in their own
    column."""
    return service.ageing(db, ctx)


@router.get("/fees/reports/ageing/download", response_class=Response, responses=_FILE)
def fee_ageing_download(ctx: Reporter, db: TenantDB, fmt: Format = "csv") -> Response:
    """The ageing per student as CSV or XLSX (``fee.report``, step-up). Audited."""
    return _send(service.ageing_download(db, ctx, fmt=fmt))


@router.get("/fees/reports/receipt-series", response_model=SeriesOut)
def fee_receipt_series(
    ctx: FinanceReader,
    db: TenantDB,
    financial_year: Annotated[
        str | None, Query(min_length=7, max_length=7, pattern=r"^20[0-9]{2}-[0-9]{2}$")
    ] = None,
) -> SeriesOut:
    """The receipt and refund voucher series of a financial year (default the current one;
    ``finance.read``): first and last numbers, cancelled and any missing numbers. 422
    ``financial_year`` (``invalid``, ``in_future``)."""
    return service.series_check(db, ctx, financial_year)


@router.get("/fees/reports/year-end-pack", response_class=Response, responses=_ZIP)
def fee_year_end_pack(ctx: Reporter, db: TenantDB, financial_year: FinancialYear) -> Response:
    """Every report of a financial year as one ZIP of CSV files (``fee.report``, step-up), for
    the auditor and the trust's CA. Audited once."""
    return _send(service.year_end_pack(db, ctx, financial_year))
