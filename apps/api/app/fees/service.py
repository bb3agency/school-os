"""Fee ledger and receipts public API (R2-1B; ADR-0042; US-2101..US-2106, US-2110;
FR-FEE-001..014, FR-FEE-018..030). Other modules call only this module.

- **Gate (FR-FEE-001).** Fees are on for a school only when the platform flag ``fees.enabled``
  is on for it AND the school's own setting ``fees_enabled`` is true (the alongside mode lists
  ``fees`` as hidden and the setting is how the owner turns it on, ADR-0041). Otherwise every
  route answers 404 ``module_hidden``.
- **Permissions (docs/07 §6.2).** School-wide only (FR-FEE-024); teachers have none.
- **Money (FR-FEE-028).** ``Decimal`` rupees, ``numeric(14,2)``; no float anywhere, no paise.
- **Ledger (FR-FEE-006).** Append-only; balances are sums of entries.
- **Receipts (FR-FEE-010/011).** One gap-free series per school and financial year; content
  frozen with a SHA-256; never edited (cancellation is a new record).
- **Never on a TC (FR-FEE-030).** Nothing here is called by the certificate flow to block or
  print anything; :func:`account_summary` is information for holders of ``finance.read``.

Internal modules: ``setup`` (settings, heads, plans), ``ledger`` (accounts and dues),
``receipts``, ``approvals`` (maker-checker requests), ``late_fees``, ``reports``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection

from sqlalchemy.orm import Session

from app.core import purge as purging
from app.core.errors import Conflict
from app.core.records import RecordTable
from app.fees import _common as c
from app.fees import approvals, ledger, receipts, reports, setup
from app.fees import late_fees as late
from app.fees import repository as repo
from app.fees._common import (
    COLLECT,
    CONCESSION_APPROVE,
    CONCESSION_REQUEST,
    MANAGE,
    READ,
    RECEIPT_CANCEL,
    REFUND_APPROVE,
    REFUND_REQUEST,
    REPORT,
    DayClosed,
    ModuleHidden,
    SelfApprovalForbidden,
    fees_on,
)
from app.fees.money import ZERO
from app.fees.receipts import RENDER_EVENT, RENDER_TASK
from app.fees.schemas import DueLine, FeeAccountSummary, InstalmentDue, StudentDues
from app.tenancy import service as tenancy

# --- routes' operations (re-exported for app.fees.api) --------------------------------------------

status = setup.status
get_settings = setup.get_settings
put_settings = setup.put_settings
list_heads = setup.list_heads
create_head = setup.create_head
update_head = setup.update_head
list_plans = setup.list_plans
get_plan = setup.get_plan
create_plan = setup.create_plan
update_plan = setup.update_plan
activate_plan = setup.activate_plan
new_version = setup.new_version
apply_plan = setup.apply_plan
plan_differences = setup.plan_differences
propose_adjustments = setup.propose_adjustments

search_accounts = ledger.search_accounts
get_account = ledger.get_account
statement = ledger.statement
accounts_of_student = ledger.accounts_of_student

issue_receipt = receipts.issue_receipt
get_receipt = receipts.get_receipt
list_receipts = receipts.list_receipts
print_page = receipts.print_page
download_url = receipts.download_url
retry_render = receipts.retry_render
render_pdf = receipts.render_pdf
mark_pdf_failed = receipts.mark_pdf_failed

list_concessions = approvals.list_concessions
request_concession = approvals.request_concession
approve_concession = approvals.approve_concession
reject_concession = approvals.reject_concession
withdraw_concession = approvals.withdraw_concession
list_adjustments = approvals.list_adjustments
request_adjustment = approvals.request_adjustment
approve_adjustment = approvals.approve_adjustment
reject_adjustment = approvals.reject_adjustment
withdraw_adjustment = approvals.withdraw_adjustment
request_cancellation = approvals.request_cancellation
approve_cancellation = approvals.approve_cancellation
reject_cancellation = approvals.reject_cancellation
withdraw_cancellation = approvals.withdraw_cancellation
request_refund = approvals.request_refund
approve_refund = approvals.approve_refund
reject_refund = approvals.reject_refund
withdraw_refund = approvals.withdraw_refund
approvals_inbox = approvals.approvals

daily_report = reports.daily_report
daily_print = reports.daily_print
daily_csv = reports.daily_csv
close_day = reports.close_day
dues_list = reports.dues_list
dues_csv = reports.dues_csv

post_late_fees = late.post_for_school


# --- data contract for other modules (R2-2F Ask, R2-3G sending, R2-3H parent pages) -------------


def enabled(session: Session, tenant_id: uuid.UUID) -> bool:
    """Whether fees are on for the school of ``session`` (flag and school setting)."""
    return fees_on(session, tenant_id)


def account_summary(
    session: Session,
    student_ids: Collection[uuid.UUID],
    *,
    academic_year_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, FeeAccountSummary]:
    """Balance, overdue amount, oldest due date and last payment of each student's fee account
    for the year (current unless given). IDs and amounts only; no permission check: callers
    decide who may see fee data (``finance.read``, FR-FEE-024) and never print it on a
    certificate or block one with it (FR-FEE-030)."""
    year_id = academic_year_id
    if year_id is None:
        current = tenancy.get_current_academic_year(session)
        if current is None:
            return {}
        year_id = current.id
    accounts = repo.accounts_for_year(session, year_id, list(student_ids))
    dues = ledger.dues_of(session, accounts, c.today(session))
    last = repo.last_payment_dates(session, [a.id for a in accounts])
    return {
        a.student_id: FeeAccountSummary(
            student_id=a.student_id,
            account_id=a.id,
            academic_year_id=a.academic_year_id,
            balance=dues[a.id].balance,
            overdue=dues[a.id].overdue,
            oldest_due_on=dues[a.id].oldest_due_on,
            last_payment_on=last.get(a.id),
        )
        for a in accounts
    }


def dues_for_student(
    session: Session, student_id: uuid.UUID, *, academic_year_id: uuid.UUID | None = None
) -> StudentDues | None:
    """What a student owes per instalment now (the parent dues page and fee reminders; no
    permission check, the caller decides who sees it). ``None`` without an account."""
    summary = account_summary(session, [student_id], academic_year_id=academic_year_id)
    found = summary.get(student_id)
    if found is None:
        return None
    account = ledger.load_account(session, found.account_id)
    today = c.today(session)
    dues = ledger.dues_for(session, account, today)
    per: dict[int, list[DueLine]] = {}
    for _, line in dues.lines:
        if line.collectable:
            per.setdefault(line.instalment_no, []).append(line)
    instalments = []
    for no in sorted(per):
        lines = per[no]
        due = sum((x.due for x in lines), ZERO)
        if due <= 0:
            continue
        first = lines[0]
        instalments.append(
            InstalmentDue(
                instalment_no=no,
                label=first.label,
                due_on=first.due_on,
                due=due,
                overdue=any(x.overdue for x in lines),
            )
        )
    return StudentDues(
        student_id=student_id,
        account_id=account.id,
        instalments=instalments,
        total_due=sum((i.due for i in instalments), ZERO),
        as_of=today,
    )


class PaymentsNotConnected(Conflict):
    code = "payments_not_connected"

    def __init__(self) -> None:
        super().__init__(
            "Online payments are not connected for this school. Collect the fee at the counter."
        )


def create_payment_intent(
    session: Session, account_id: uuid.UUID, instalments: Collection[int], *, created_via: str
) -> uuid.UUID:
    """A payment link for the given instalments (R2-2D builds it on the school's Razorpay
    account). Until then: 409 ``payments_not_connected``."""
    raise PaymentsNotConnected()


def receipt_issued_event() -> str:
    """Outbox event written in the issuing transaction (payload: ``receipt_id``,
    ``account_id``, ``user_id``); the PDF render consumes it today, the receipt message later."""
    return RENDER_EVENT


# --- full data export and offboarding ------------------------------------------------------------


def export_records(session: Session) -> list[RecordTable]:
    """Worker only: every fees table of the current school for its full data export
    (FR-FEE-027; the caller checked ``tenant.export_all`` and audits the export)."""
    return repo.export_tables(session)


# Children before parents (migration 0057_fees_core). Registered with app.tenancy at import;
# purged before students, documents, the academic structure and the members they reference.
_PURGE = purging.PurgeTables(
    deleted=(
        "fin.day_closes",
        "fin.ledger_entries",
        "fin.refunds",
        "fin.receipt_cancellations",
        "fin.receipt_allocations",
        "fin.receipts",
        "fin.receipt_counters",
        "fin.adjustments",
        "fin.concessions",
        "fin.student_fee_accounts",
        "fin.fee_plan_instalments",
        "fin.fee_plan_lines",
        "fin.fee_plans",
        "fin.fee_heads",
        "fin.fee_settings",
    )
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="fees", count=tenant_data_counts, purge=purge_tenant_data)
)


def today(session: Session) -> dt.date:
    return c.today(session)


__all__ = [
    "COLLECT",
    "CONCESSION_APPROVE",
    "CONCESSION_REQUEST",
    "MANAGE",
    "READ",
    "RECEIPT_CANCEL",
    "REFUND_APPROVE",
    "REFUND_REQUEST",
    "RENDER_EVENT",
    "RENDER_TASK",
    "REPORT",
    "DayClosed",
    "ModuleHidden",
    "PaymentsNotConnected",
    "SelfApprovalForbidden",
    "account_summary",
    "create_payment_intent",
    "dues_for_student",
    "enabled",
    "export_records",
    "purge_tenant_data",
    "tenant_data_counts",
]
