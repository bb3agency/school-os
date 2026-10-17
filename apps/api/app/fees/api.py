"""Fee ledger and receipt routes (docs/09 "Fees"; US-2101..US-2106, US-2110; FR-FEE-*).

Every route declares its guard. Fee permissions are school-wide only (FR-FEE-024): single-
permission routes use ``scope="school"``; :func:`require_any` reads are re-checked school-wide by
the service. Decisions on concessions, adjustments, cancellations and refunds need step-up MFA
and are refused to the person who made the request (403 ``self_approval_forbidden``). While fees
are off for the school every route answers 404 ``module_hidden``. No personal data in URLs:
searches are POST bodies, filters are ids and codes.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import HTMLResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import (
    Cursor,
    IdempotencyDep,
    IfMatch,
    Limit,
    OptionalIfMatch,
    Page,
    etag,
)
from app.fees import service
from app.fees.schemas import (
    AccountOut,
    AccountSearch,
    AccountSummary,
    AdjustmentCreate,
    AdjustmentOut,
    ApprovalItem,
    CancellationOut,
    CancellationRequest,
    ConcessionCreate,
    ConcessionOut,
    DailyReportOut,
    DecisionIn,
    DownloadUrlOut,
    DuesOut,
    FeeSettingsIn,
    FeeSettingsOut,
    FeeStatusOut,
    HeadCreate,
    HeadOut,
    HeadPatch,
    PlanApplyIn,
    PlanApplyOut,
    PlanCreate,
    PlanDifferencesOut,
    PlanOut,
    PlanPatch,
    ProposeAdjustmentsIn,
    ProposeAdjustmentsOut,
    ReceiptCreate,
    ReceiptListItem,
    ReceiptMode,
    ReceiptOut,
    ReceiptStatus,
    RefundCreate,
    RefundOut,
    RejectIn,
    StatementOut,
)
from app.fees.templates import STYLE_CSP

router = APIRouter(prefix="/api/v1", tags=["fees"])

R = service
AnyFee = Annotated[
    UserContext,
    Depends(
        require_any(
            R.READ,
            R.COLLECT,
            R.MANAGE,
            R.CONCESSION_REQUEST,
            R.CONCESSION_APPROVE,
            R.RECEIPT_CANCEL,
            R.REFUND_REQUEST,
            R.REFUND_APPROVE,
            R.REPORT,
        )
    ),
]
Viewer = Annotated[UserContext, Depends(require_any(R.COLLECT, R.READ))]
SetupReader = Annotated[UserContext, Depends(require_any(R.READ, R.MANAGE))]
HeadReader = Annotated[UserContext, Depends(require_any(R.READ, R.COLLECT, R.MANAGE))]
FinanceReader = Annotated[UserContext, Depends(require(R.READ, scope="school"))]
Collector = Annotated[UserContext, Depends(require(R.COLLECT, scope="school"))]
Manager = Annotated[UserContext, Depends(require(R.MANAGE, scope="school"))]
CancelRequester = Annotated[UserContext, Depends(require_any(R.COLLECT, R.MANAGE))]
CancelApprover = Annotated[
    UserContext, Depends(require(R.RECEIPT_CANCEL, scope="school", step_up=True))
]
ConcessionRequester = Annotated[UserContext, Depends(require(R.CONCESSION_REQUEST, scope="school"))]
ConcessionReader = Annotated[
    UserContext, Depends(require_any(R.READ, R.CONCESSION_REQUEST, R.CONCESSION_APPROVE))
]
ConcessionApprover = Annotated[
    UserContext, Depends(require(R.CONCESSION_APPROVE, scope="school", step_up=True))
]
AdjustmentReader = Annotated[
    UserContext, Depends(require_any(R.READ, R.MANAGE, R.CONCESSION_APPROVE))
]
RefundRequester = Annotated[UserContext, Depends(require(R.REFUND_REQUEST, scope="school"))]
RefundApprover = Annotated[
    UserContext, Depends(require(R.REFUND_APPROVE, scope="school", step_up=True))
]
Reporter = Annotated[UserContext, Depends(require(R.REPORT, scope="school", step_up=True))]
ApprovalsReader = Annotated[
    UserContext,
    Depends(
        require_any(
            R.READ,
            R.COLLECT,
            R.MANAGE,
            R.CONCESSION_REQUEST,
            R.CONCESSION_APPROVE,
            R.RECEIPT_CANCEL,
            R.REFUND_REQUEST,
            R.REFUND_APPROVE,
        )
    ),
]

_HTML_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": STYLE_CSP,
    "X-Content-Type-Options": "nosniff",
}
_PAGE: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/html": {}}, "description": "Print-ready page (no scripts)"}
}
_CSV: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/csv": {"schema": {"type": "string"}}}, "description": "CSV file"}
}


def _html(page: str, filename: str) -> HTMLResponse:
    return HTMLResponse(
        page, headers={**_HTML_HEADERS, "Content-Disposition": f'inline; filename="{filename}"'}
    )


def _csv(filename: str, text: str) -> Response:
    return Response(
        content="﻿" + text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _etag(response: Response, version: int) -> None:
    response.headers["ETag"] = etag(version)


# --- status, settings ----------------------------------------------------------------------------


@router.get("/fees/status", response_model=FeeStatusOut)
def fee_status(ctx: AnyFee, db: TenantDB) -> FeeStatusOut:
    """Whether fees are on for the school (404 ``module_hidden`` when off), today's IST date
    and whether today is closed (any fee permission)."""
    return service.status(db, ctx)


@router.get("/fees/settings", response_model=FeeSettingsOut)
def get_fee_settings(ctx: SetupReader, db: TenantDB, response: Response) -> FeeSettingsOut:
    """Receipt paper size, GST note and reminder defaults (``finance.read`` or
    ``fee.manage``)."""
    out = service.get_settings(db, ctx)
    _etag(response, out.version)
    return out


@router.put("/fees/settings", response_model=FeeSettingsOut)
def put_fee_settings(
    ctx: Manager, db: TenantDB, body: FeeSettingsIn, expected: IfMatch, response: Response
) -> FeeSettingsOut:
    """Change the fee settings (``fee.manage``, ``If-Match``)."""
    out = service.put_settings(db, ctx, body, expected_version=expected)
    _etag(response, out.version)
    return out


# --- heads ---------------------------------------------------------------------------------------


@router.get("/fees/heads", response_model=list[HeadOut])
def list_fee_heads(ctx: HeadReader, db: TenantDB) -> list[HeadOut]:
    """Fee heads (``finance.read``, ``fee.collect`` or ``fee.manage``)."""
    return service.list_heads(db, ctx)


@router.post("/fees/heads", response_model=HeadOut, status_code=201)
def create_fee_head(ctx: Manager, db: TenantDB, body: HeadCreate, idem: IdempotencyDep) -> Response:
    """Add a fee head (``fee.manage``; ``Idempotency-Key``). 409 ``head_code_taken``."""
    return idem.run(
        db,
        body,
        lambda: service.create_head(db, ctx, body),
        headers=lambda o: {"ETag": etag(o.version)},
    )


@router.patch("/fees/heads/{head_id}", response_model=HeadOut)
def update_fee_head(
    *,
    ctx: Manager,
    db: TenantDB,
    head_id: uuid.UUID,
    body: HeadPatch,
    expected: IfMatch,
    response: Response,
) -> HeadOut:
    """Rename, re-order, deactivate or change the tax flag of a head (``fee.manage``,
    ``If-Match``)."""
    out = service.update_head(db, ctx, head_id, body, expected_version=expected)
    _etag(response, out.version)
    return out


# --- plans ---------------------------------------------------------------------------------------


@router.get("/fees/plans", response_model=list[PlanOut])
def list_fee_plans(
    ctx: SetupReader,
    db: TenantDB,
    academic_year_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
) -> list[PlanOut]:
    """Fee plans, optionally of one year and class (``finance.read`` or ``fee.manage``)."""
    return service.list_plans(db, ctx, academic_year_id=academic_year_id, class_id=class_id)


@router.post("/fees/plans", response_model=PlanOut, status_code=201)
def create_fee_plan(ctx: Manager, db: TenantDB, body: PlanCreate, idem: IdempotencyDep) -> Response:
    """A draft plan with lines and an instalment schedule (``fee.manage``;
    ``Idempotency-Key``). 422 ``instalments_do_not_add_up``; 409 ``plan_exists``."""
    return idem.run(
        db,
        body,
        lambda: service.create_plan(db, ctx, body),
        headers=lambda o: {"Location": f"/api/v1/fees/plans/{o.id}", "ETag": etag(o.version)},
    )


@router.get("/fees/plans/{plan_id}", response_model=PlanOut)
def get_fee_plan(ctx: SetupReader, db: TenantDB, plan_id: uuid.UUID, response: Response) -> PlanOut:
    out = service.get_plan(db, ctx, plan_id)
    _etag(response, out.version)
    return out


@router.patch("/fees/plans/{plan_id}", response_model=PlanOut)
def update_fee_plan(
    *,
    ctx: Manager,
    db: TenantDB,
    plan_id: uuid.UUID,
    body: PlanPatch,
    expected: IfMatch,
    response: Response,
) -> PlanOut:
    """Change a draft plan (``fee.manage``, ``If-Match``); 409 ``plan_in_use`` when active."""
    out = service.update_plan(db, ctx, plan_id, body, expected_version=expected)
    _etag(response, out.version)
    return out


@router.post("/fees/plans/{plan_id}/activate", response_model=PlanOut)
def activate_fee_plan(
    ctx: Manager, db: TenantDB, plan_id: uuid.UUID, expected: IfMatch, response: Response
) -> PlanOut:
    """Make a draft active; a new version supersedes the one it was made from (``fee.manage``,
    ``If-Match``)."""
    out = service.activate_plan(db, ctx, plan_id, expected_version=expected)
    _etag(response, out.version)
    return out


@router.post("/fees/plans/{plan_id}/new-version", response_model=PlanOut, status_code=201)
def new_fee_plan_version(ctx: Manager, db: TenantDB, plan_id: uuid.UUID) -> PlanOut:
    """A draft copy of an active plan with the next version (``fee.manage``)."""
    return service.new_version(db, ctx, plan_id)


@router.post("/fees/plans/{plan_id}/apply", response_model=PlanApplyOut)
def apply_fee_plan(
    ctx: Manager, db: TenantDB, plan_id: uuid.UUID, body: PlanApplyIn
) -> PlanApplyOut:
    """Open an account and post the charges for every active student of the class (or the
    given sections); ``dry_run`` only counts. Idempotent (``fee.manage``)."""
    return service.apply_plan(db, ctx, plan_id, body)


@router.get("/fees/plans/{plan_id}/differences", response_model=PlanDifferencesOut)
def fee_plan_differences(ctx: SetupReader, db: TenantDB, plan_id: uuid.UUID) -> PlanDifferencesOut:
    """What a new version changes per student (FR-FEE-004)."""
    return service.plan_differences(db, ctx, plan_id)


@router.post("/fees/plans/{plan_id}/adjustments", response_model=ProposeAdjustmentsOut)
def propose_fee_plan_adjustments(
    ctx: Manager, db: TenantDB, plan_id: uuid.UUID, body: ProposeAdjustmentsIn
) -> ProposeAdjustmentsOut:
    """Propose the differences of a new version as adjustment requests and move the accounts
    to it (``fee.manage``; the requests need approval)."""
    return service.propose_adjustments(db, ctx, plan_id, body)


# --- accounts ------------------------------------------------------------------------------------


@router.post("/fees/accounts/search", response_model=list[AccountSummary])
def search_fee_accounts(ctx: Viewer, db: TenantDB, body: AccountSearch) -> list[AccountSummary]:
    """Find students' fee accounts by part of a name or the admission number (body, never URL;
    ``fee.collect`` or ``finance.read``)."""
    return service.search_accounts(db, ctx, body)


@router.get("/fees/accounts/{account_id}", response_model=AccountOut)
def get_fee_account(ctx: Viewer, db: TenantDB, account_id: uuid.UUID) -> AccountOut:
    """Dues per instalment and head with late fees and the balance."""
    return service.get_account(db, ctx, account_id)


@router.get("/fees/accounts/{account_id}/statement", response_model=StatementOut)
def get_fee_statement(ctx: Viewer, db: TenantDB, account_id: uuid.UUID) -> StatementOut:
    """Every ledger entry with the running balance."""
    return service.statement(db, ctx, account_id)


@router.get("/students/{student_id}/fee-accounts", response_model=list[AccountSummary])
def student_fee_accounts(ctx: Viewer, db: TenantDB, student_id: uuid.UUID) -> list[AccountSummary]:
    """The student's fee accounts, newest year first."""
    return service.accounts_of_student(db, ctx, student_id)


# --- receipts ------------------------------------------------------------------------------------


@router.post("/fees/receipts", response_model=ReceiptOut, status_code=201)
def issue_fee_receipt(
    ctx: Collector, db: TenantDB, body: ReceiptCreate, idem: IdempotencyDep
) -> Response:
    """Take a payment and issue the receipt (``fee.collect``; send ``Idempotency-Key``).
    Errors (409): ``amount_exceeds_due``, ``head_not_collectable``, ``utr_in_use``,
    ``day_closed``, ``account_closed``; (422) mode details, UTR, allocations."""
    return idem.run(
        db,
        body,
        lambda: service.issue_receipt(db, ctx, body),
        headers=lambda o: {"Location": f"/api/v1/fees/receipts/{o.id}"},
    )


@router.get("/fees/receipts", response_model=Page[ReceiptListItem])
def list_fee_receipts(
    *,
    ctx: Viewer,
    db: TenantDB,
    date: dt.date | None = None,
    mode: ReceiptMode | None = None,
    collected_by: uuid.UUID | None = None,
    status: Annotated[ReceiptStatus | None, Query()] = None,
    account_id: uuid.UUID | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[ReceiptListItem]:
    """Receipts, newest first (``fee.collect`` or ``finance.read``)."""
    return service.list_receipts(
        db,
        ctx,
        receipt_date=date,
        mode=mode,
        collected_by=collected_by,
        status=status,
        account_id=account_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/fees/receipts/{receipt_id}", response_model=ReceiptOut)
def get_fee_receipt(
    ctx: Viewer, db: TenantDB, receipt_id: uuid.UUID, response: Response
) -> ReceiptOut:
    out = service.get_receipt(db, ctx, receipt_id)
    _etag(response, out.version)
    return out


@router.get("/fees/receipts/{receipt_id}/print", response_class=HTMLResponse, responses=_PAGE)
def print_fee_receipt(ctx: Viewer, db: TenantDB, receipt_id: uuid.UUID) -> HTMLResponse:
    """The receipt as a print-ready A5 (or A4) page; cancelled receipts are marked. Audited."""
    return _html(service.print_page(db, ctx, receipt_id), f"receipt-{str(receipt_id)[:8]}.html")


@router.get("/fees/receipts/{receipt_id}/download-url", response_model=DownloadUrlOut)
def fee_receipt_download_url(ctx: Viewer, db: TenantDB, receipt_id: uuid.UUID) -> DownloadUrlOut:
    """A short-lived link to the stored PDF. 409 ``pdf_not_ready``, ``receipt_cancelled``."""
    return service.download_url(db, ctx, receipt_id)


@router.post("/fees/receipts/{receipt_id}/render", response_model=ReceiptOut)
def render_fee_receipt(ctx: Collector, db: TenantDB, receipt_id: uuid.UUID) -> ReceiptOut:
    """Make the PDF again after it failed (``fee.collect``)."""
    return service.retry_render(db, ctx, receipt_id)


# --- cancellations -------------------------------------------------------------------------------


@router.post(
    "/fees/receipts/{receipt_id}/cancellation", response_model=CancellationOut, status_code=201
)
def request_fee_receipt_cancellation(
    ctx: CancelRequester,
    db: TenantDB,
    receipt_id: uuid.UUID,
    body: CancellationRequest,
    idem: IdempotencyDep,
) -> Response:
    """Ask for a receipt to be cancelled (``fee.collect`` or ``fee.manage``)."""
    return idem.run(db, body, lambda: service.request_cancellation(db, ctx, receipt_id, body))


@router.post("/fees/cancellations/{cancellation_id}/approve", response_model=CancellationOut)
def approve_fee_cancellation(
    ctx: CancelApprover,
    db: TenantDB,
    cancellation_id: uuid.UUID,
    body: DecisionIn,
    expected: OptionalIfMatch,
) -> CancellationOut:
    """Cancel the receipt (``fee.receipt.cancel``, step-up, not the requester)."""
    return service.approve_cancellation(db, ctx, cancellation_id, body, expected_version=expected)


@router.post("/fees/cancellations/{cancellation_id}/reject", response_model=CancellationOut)
def reject_fee_cancellation(
    ctx: CancelApprover,
    db: TenantDB,
    cancellation_id: uuid.UUID,
    body: RejectIn,
    expected: OptionalIfMatch,
) -> CancellationOut:
    return service.reject_cancellation(db, ctx, cancellation_id, body, expected_version=expected)


@router.post("/fees/cancellations/{cancellation_id}/withdraw", response_model=CancellationOut)
def withdraw_fee_cancellation(
    ctx: CancelRequester, db: TenantDB, cancellation_id: uuid.UUID
) -> CancellationOut:
    """Withdraw your own pending request."""
    return service.withdraw_cancellation(db, ctx, cancellation_id)


# --- concessions ---------------------------------------------------------------------------------


@router.get("/fees/accounts/{account_id}/concessions", response_model=list[ConcessionOut])
def list_fee_concessions(
    ctx: ConcessionReader, db: TenantDB, account_id: uuid.UUID
) -> list[ConcessionOut]:
    return service.list_concessions(db, ctx, account_id)


@router.post(
    "/fees/accounts/{account_id}/concessions", response_model=ConcessionOut, status_code=201
)
def request_fee_concession(
    ctx: ConcessionRequester,
    db: TenantDB,
    account_id: uuid.UUID,
    body: ConcessionCreate,
    idem: IdempotencyDep,
) -> Response:
    """Ask for a concession (``fee.concession.request``)."""
    return idem.run(db, body, lambda: service.request_concession(db, ctx, account_id, body))


@router.post("/fees/concessions/{concession_id}/approve", response_model=ConcessionOut)
def approve_fee_concession(
    ctx: ConcessionApprover,
    db: TenantDB,
    concession_id: uuid.UUID,
    body: DecisionIn,
    expected: OptionalIfMatch,
) -> ConcessionOut:
    return service.approve_concession(db, ctx, concession_id, body, expected_version=expected)


@router.post("/fees/concessions/{concession_id}/reject", response_model=ConcessionOut)
def reject_fee_concession(
    ctx: ConcessionApprover,
    db: TenantDB,
    concession_id: uuid.UUID,
    body: RejectIn,
    expected: OptionalIfMatch,
) -> ConcessionOut:
    return service.reject_concession(db, ctx, concession_id, body, expected_version=expected)


@router.post("/fees/concessions/{concession_id}/withdraw", response_model=ConcessionOut)
def withdraw_fee_concession(
    ctx: ConcessionRequester, db: TenantDB, concession_id: uuid.UUID
) -> ConcessionOut:
    return service.withdraw_concession(db, ctx, concession_id)


# --- adjustments ---------------------------------------------------------------------------------


@router.get("/fees/accounts/{account_id}/adjustments", response_model=list[AdjustmentOut])
def list_fee_adjustments(
    ctx: AdjustmentReader, db: TenantDB, account_id: uuid.UUID
) -> list[AdjustmentOut]:
    return service.list_adjustments(db, ctx, account_id)


@router.post(
    "/fees/accounts/{account_id}/adjustments", response_model=AdjustmentOut, status_code=201
)
def request_fee_adjustment(
    ctx: Manager,
    db: TenantDB,
    account_id: uuid.UUID,
    body: AdjustmentCreate,
    idem: IdempotencyDep,
) -> Response:
    """Ask for an adjustment or a late-fee waiver (``fee.manage``)."""
    return idem.run(db, body, lambda: service.request_adjustment(db, ctx, account_id, body))


@router.post("/fees/adjustments/{adjustment_id}/approve", response_model=AdjustmentOut)
def approve_fee_adjustment(
    ctx: ConcessionApprover,
    db: TenantDB,
    adjustment_id: uuid.UUID,
    body: DecisionIn,
    expected: OptionalIfMatch,
) -> AdjustmentOut:
    return service.approve_adjustment(db, ctx, adjustment_id, body, expected_version=expected)


@router.post("/fees/adjustments/{adjustment_id}/reject", response_model=AdjustmentOut)
def reject_fee_adjustment(
    ctx: ConcessionApprover,
    db: TenantDB,
    adjustment_id: uuid.UUID,
    body: RejectIn,
    expected: OptionalIfMatch,
) -> AdjustmentOut:
    return service.reject_adjustment(db, ctx, adjustment_id, body, expected_version=expected)


@router.post("/fees/adjustments/{adjustment_id}/withdraw", response_model=AdjustmentOut)
def withdraw_fee_adjustment(ctx: Manager, db: TenantDB, adjustment_id: uuid.UUID) -> AdjustmentOut:
    return service.withdraw_adjustment(db, ctx, adjustment_id)


# --- refunds -------------------------------------------------------------------------------------


@router.post("/fees/receipts/{receipt_id}/refunds", response_model=RefundOut, status_code=201)
def request_fee_refund(
    ctx: RefundRequester,
    db: TenantDB,
    receipt_id: uuid.UUID,
    body: RefundCreate,
    idem: IdempotencyDep,
) -> Response:
    """Ask for a refund against a receipt (``fee.refund.request``)."""
    return idem.run(db, body, lambda: service.request_refund(db, ctx, receipt_id, body))


@router.post("/fees/refunds/{refund_id}/approve", response_model=RefundOut)
def approve_fee_refund(
    ctx: RefundApprover,
    db: TenantDB,
    refund_id: uuid.UUID,
    body: DecisionIn,
    expected: OptionalIfMatch,
) -> RefundOut:
    """Approve: the voucher is written and the refund posted (``fee.refund.approve``,
    step-up, not the requester)."""
    return service.approve_refund(db, ctx, refund_id, body, expected_version=expected)


@router.post("/fees/refunds/{refund_id}/reject", response_model=RefundOut)
def reject_fee_refund(
    ctx: RefundApprover,
    db: TenantDB,
    refund_id: uuid.UUID,
    body: RejectIn,
    expected: OptionalIfMatch,
) -> RefundOut:
    return service.reject_refund(db, ctx, refund_id, body, expected_version=expected)


@router.post("/fees/refunds/{refund_id}/withdraw", response_model=RefundOut)
def withdraw_fee_refund(ctx: RefundRequester, db: TenantDB, refund_id: uuid.UUID) -> RefundOut:
    return service.withdraw_refund(db, ctx, refund_id)


# --- approvals inbox -----------------------------------------------------------------------------


@router.get("/fees/approvals", response_model=list[ApprovalItem])
def fee_approvals(ctx: ApprovalsReader, db: TenantDB) -> list[ApprovalItem]:
    """Every pending fee request, oldest first, with ``mine`` and ``can_decide``."""
    return service.approvals_inbox(db, ctx)


# --- reports and day close -----------------------------------------------------------------------


@router.get("/fees/reports/daily", response_model=DailyReportOut)
def fee_daily_report(
    ctx: FinanceReader, db: TenantDB, date: dt.date | None = None
) -> DailyReportOut:
    """The daily collection report for an IST date (default today; ``finance.read``)."""
    return service.daily_report(db, ctx, date)


@router.get("/fees/reports/daily/print", response_class=HTMLResponse, responses=_PAGE)
def fee_daily_report_print(
    ctx: FinanceReader, db: TenantDB, date: dt.date | None = None
) -> HTMLResponse:
    """The daily report as an A4 print page (``finance.read``). Audited."""
    page = service.daily_print(db, ctx, date)
    return _html(page, "daily-collection.html")


@router.get("/fees/reports/daily.csv", response_class=Response, responses=_CSV)
def fee_day_book_csv(ctx: Reporter, db: TenantDB, date: dt.date | None = None) -> Response:
    """The day book as CSV for entry into Tally (``fee.report``, step-up). Audited."""
    filename, text = service.daily_csv(db, ctx, date)
    return _csv(filename, text)


@router.post("/fees/days/{date}/close", response_model=DailyReportOut)
def close_fee_day(ctx: Manager, db: TenantDB, date: dt.date) -> DailyReportOut:
    """Close a day (``fee.manage``): nothing more can be dated that day. 409 ``day_closed``."""
    return service.close_day(db, ctx, date)


@router.get("/fees/dues", response_model=DuesOut)
def fee_dues(
    ctx: FinanceReader,
    db: TenantDB,
    section_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
) -> DuesOut:
    """Students with overdue amounts, oldest due first (``finance.read``)."""
    return service.dues_list(db, ctx, section_id=section_id, class_id=class_id)


@router.get("/fees/dues.csv", response_class=Response, responses=_CSV)
def fee_dues_csv(
    ctx: Reporter,
    db: TenantDB,
    section_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
) -> Response:
    """The dues list as CSV (``fee.report``, step-up). Audited like an export."""
    filename, text = service.dues_csv(db, ctx, section_id=section_id, class_id=class_id)
    return _csv(filename, text)
