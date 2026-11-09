"""Tally voucher export and ledger-vs-Tally check routes (FM-03; FR-TALLY-011..030; docs/09
Tally connector).

SchoolOS never writes to Tally: the export is a file the accountant imports by hand. These routes
follow the fee module's gate (404 ``module_hidden`` while fees are off), not the connector flag:
the file is useful without the edge agent. The check reads the connector's last snapshot, so it
also needs ``tally.connector.enabled`` (404 otherwise).

- Mapping: read with ``tally.configure`` or ``fee.report``; save with ``tally.configure``.
- Preview and file: ``fee.report`` (the file with a recent MFA sign-in, like every bulk fee
  export; audited ``fee.report.downloaded``). The file is a POST: it records what it carried.
- History: ``fee.report`` or ``finance.read``. Check: ``finance.read``. School-wide only.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import OptionalIfMatch, etag
from app.tally import vouchers
from app.tally.service import CONFIGURE, READ
from app.tally.vouchers_schemas import (
    LedgerCheckOut,
    VoucherExportIn,
    VoucherExportOut,
    VoucherPreviewOut,
    VoucherSettingsIn,
    VoucherSettingsOut,
)

router = APIRouter(prefix="/api/v1/tally/vouchers", tags=["tally"])

SettingsReader = Annotated[UserContext, Depends(require_any(CONFIGURE, vouchers.REPORT))]
Configurer = Annotated[UserContext, Depends(require(CONFIGURE, scope="school"))]
Reporter = Annotated[UserContext, Depends(require(vouchers.REPORT, scope="school"))]
Exporter = Annotated[UserContext, Depends(require(vouchers.REPORT, scope="school", step_up=True))]
HistoryReader = Annotated[UserContext, Depends(require_any(vouchers.REPORT, READ))]
FinanceReader = Annotated[UserContext, Depends(require(READ, scope="school"))]

_XML: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {"application/xml": {"schema": {"type": "string"}}},
        "description": "Tally XML import file (Import Data > Vouchers)",
    }
}


@router.get("/settings", response_model=VoucherSettingsOut)
def get_voucher_settings(
    ctx: SettingsReader, db: TenantDB, response: Response
) -> VoucherSettingsOut:
    """The ledger mapping: party mode, receivable ledger, a ledger per receipt mode and per fee
    head, the ledger for late fees, whether fee demand is exported and the Tally company. Until
    it is saved, the defaults (``configured`` false). ETag ``W/"<version>"``."""
    out = vouchers.get_settings(db, ctx)
    response.headers["ETag"] = etag(out.version)
    return out


@router.put("/settings", response_model=VoucherSettingsOut)
def put_voucher_settings(
    ctx: Configurer,
    db: TenantDB,
    body: VoucherSettingsIn,
    version: OptionalIfMatch,
    response: Response,
) -> VoucherSettingsOut:
    """Save the mapping (``tally.configure``; optional ``If-Match``). Names must match the Tally
    ledgers exactly; they are refused, never changed, when they hold hidden characters or an
    Aadhaar-like number. 422 ``unknown_head``; 412 when changed meanwhile."""
    out = vouchers.put_settings(db, ctx, body, version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get("/preview", response_model=VoucherPreviewOut)
def preview_vouchers(
    ctx: Reporter,
    db: TenantDB,
    period_from: dt.date,
    period_to: dt.date,
    include_exported: bool = False,
) -> VoucherPreviewOut:
    """What a file for the period would hold: vouchers per kind (new and exported before), cash
    in and out, days not closed yet and ledgers not mapped yet. Records nothing."""
    body = VoucherExportIn(
        period_from=period_from, period_to=period_to, include_exported=include_exported
    )
    return vouchers.preview(db, ctx, body)


@router.post("/export", response_class=Response, responses=_XML)
def export_vouchers(ctx: Exporter, db: TenantDB, body: VoucherExportIn) -> Response:
    """The Tally XML file for the period (``fee.report``, recent MFA sign-in). Only closed days
    (409 ``days_not_closed``); every ledger mapped (422 ``mapping_incomplete``); vouchers already
    in an earlier file only with ``include_exported`` (then marked; 409 ``nothing_to_export``
    otherwise). 409 ``not_configured`` before the mapping is saved."""
    filename, body_bytes = vouchers.export(db, ctx, body)
    return Response(
        content=body_bytes,
        media_type="application/xml; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/exports", response_model=list[VoucherExportOut])
def list_voucher_exports(ctx: HistoryReader, db: TenantDB) -> list[VoucherExportOut]:
    """Earlier files, newest first: period, vouchers (new ones), net cash, who and when."""
    return vouchers.list_exports(db, ctx)


@router.get("/check", response_model=LedgerCheckOut)
def ledger_check(ctx: FinanceReader, db: TenantDB) -> LedgerCheckOut:
    """Tally's balances in the connector's last snapshot against what SchoolOS says the same
    students owed on Tally's as-of date: the linked ledgers (party mode ``student``) and the
    receivable ledger. Ledgers that differ, largest first; read-only, nothing is corrected.
    404 while the connector is off."""
    return vouchers.check(db, ctx)
