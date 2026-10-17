"""Daily collection report, day close and dues list (US-2110; FR-FEE-018..020; internal).

Views need ``finance.read``; the CSV downloads (bulk personal financial data) need
``fee.report`` with step-up and are audited like an export (ADR-0021). CSV cells are
formula-safe (SEC-017).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import ValidationFailed
from app.core.ids import new_id
from app.core.spreadsheet import safe_cell
from app.fees import _common as c
from app.fees import ledger, templates
from app.fees import repository as repo
from app.fees.models import DayClose
from app.fees.money import ZERO, group_indian, total
from app.fees.schemas import (
    CollectorTotal,
    DailyReportOut,
    DayCancellation,
    DayCloseOut,
    DayReceipt,
    DayRefund,
    DuesOut,
    DuesRow,
    ModeTotal,
)
from app.identity import service as identity
from app.students import service as students
from app.tenancy import service as tenancy

MODES = ("cash", "cheque", "bank_transfer", "upi_qr", "razorpay")


def _check_day(session: Session, day: dt.date) -> None:
    if day > c.today(session):
        raise ValidationFailed([c.error("date", "in_future")])


def _close_out(session: Session, row: DayClose | None) -> DayCloseOut | None:
    if row is None:
        return None
    names = identity.member_display_names(session, [row.closed_by])
    return DayCloseOut(
        close_date=row.close_date,
        receipt_count=row.receipt_count,
        receipt_total=row.receipt_total,
        closed_by=row.closed_by,
        closed_by_name=names.get(row.closed_by),
        closed_at=row.closed_at,
    )


def _report(session: Session, day: dt.date) -> DailyReportOut:
    rows = repo.receipts_on(session, day)
    names = identity.member_display_names(session, c.unique_ids(r.collected_by for r in rows))
    by_mode: dict[str, list[Decimal]] = defaultdict(list)
    by_collector: dict[uuid.UUID | None, list[Decimal]] = defaultdict(list)
    receipts = []
    for r in rows:
        by_mode[r.mode].append(r.amount)
        by_collector[r.collected_by].append(r.amount)
        student = r.content.get("student") or {}
        receipts.append(
            DayReceipt(
                id=r.id,
                receipt_number=r.receipt_number,
                amount=r.amount,
                mode=r.mode,
                status=r.status,
                student_name=str(student.get("name", "")),
                class_section=str(student.get("class_section", "")),
                collected_by_name=names.get(r.collected_by) if r.collected_by else None,
            )
        )
    cancellations = repo.cancellations_on(session, day)
    cancelled_receipts = repo.receipts_by_id(session, {x.receipt_id for x in cancellations})
    day_cancellations = [
        DayCancellation(
            cancellation_id=x.id,
            receipt_id=x.receipt_id,
            receipt_number=cancelled_receipts[x.receipt_id].receipt_number,
            receipt_date=cancelled_receipts[x.receipt_id].receipt_date,
            amount=cancelled_receipts[x.receipt_id].amount,
            mode=cancelled_receipts[x.receipt_id].mode,
            reason_code=x.reason_code,
        )
        for x in cancellations
    ]
    refunds = repo.refunds_on(session, day)
    refund_receipts = repo.receipts_by_id(session, {x.receipt_id for x in refunds})
    day_refunds = [
        DayRefund(
            refund_id=x.id,
            voucher_number=x.voucher_number or "",
            receipt_number=refund_receipts[x.receipt_id].receipt_number,
            amount=x.amount,
            mode=x.mode,
        )
        for x in refunds
    ]
    receipts_total = total(r.amount for r in rows)
    cancelled_total = total(x.amount for x in day_cancellations)
    refunded_total = total(x.amount for x in day_refunds)

    def hand_over(mode: str) -> Decimal:
        return (
            total(r.amount for r in rows if r.mode == mode)
            - total(x.amount for x in day_cancellations if x.mode == mode)
            - total(x.amount for x in day_refunds if x.mode == mode)
        )

    return DailyReportOut(
        date=day,
        by_mode=[
            ModeTotal(mode=m, count=len(by_mode[m]), total=total(by_mode[m]))
            for m in MODES
            if by_mode.get(m)
        ],
        by_collector=[
            CollectorTotal(
                membership_id=who,
                name=names.get(who) if who else None,
                count=len(amounts),
                total=total(amounts),
            )
            for who, amounts in sorted(by_collector.items(), key=lambda kv: str(kv[0]))
        ],
        receipts=receipts,
        cancellations=day_cancellations,
        refunds=day_refunds,
        receipts_total=receipts_total,
        cancelled_total=cancelled_total,
        refunded_total=refunded_total,
        net_total=receipts_total - cancelled_total - refunded_total,
        cash_to_hand_over=hand_over("cash"),
        cheque_to_hand_over=hand_over("cheque"),
        closed=_close_out(session, repo.get_day_close(session, day)),
    )


def daily_report(session: Session, ctx: UserContext, day: dt.date | None) -> DailyReportOut:
    """FR-FEE-018 (``finance.read``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ)
    when = day or c.today(session)
    _check_day(session, when)
    return _report(session, when)


def daily_print(session: Session, ctx: UserContext, day: dt.date | None) -> str:
    """The A4 print view of the daily report (``finance.read``); audit
    ``fee.report.print_viewed``."""
    report = daily_report(session, ctx, day)
    tenant = tenancy.get_tenant(session)
    closed = None
    if report.closed is not None:
        closed = (
            f"Day closed by {report.closed.closed_by_name or '—'}: "
            f"{report.closed.receipt_count} receipts, ₹{group_indian(report.closed.receipt_total)}"
        )
    page = templates.render_daily_report(
        school_name=tenant.name,
        day=report.date,
        by_mode=[(m.mode, m.count, group_indian(m.total)) for m in report.by_mode],
        by_collector=[
            (x.name or "Online", x.count, group_indian(x.total)) for x in report.by_collector
        ],
        receipts=[
            (
                r.receipt_number,
                r.student_name,
                r.class_section,
                r.mode,
                r.collected_by_name or "Online",
                group_indian(r.amount),
                r.status == "cancelled",
            )
            for r in report.receipts
        ],
        cancellations=[
            (
                x.receipt_number,
                templates.format_date(x.receipt_date),
                x.reason_code,
                group_indian(x.amount),
            )
            for x in report.cancellations
        ],
        refunds=[
            (x.voucher_number, x.receipt_number, x.mode, group_indian(x.amount))
            for x in report.refunds
        ],
        totals=[
            ("Receipts", group_indian(report.receipts_total)),
            ("Cancelled", group_indian(report.cancelled_total)),
            ("Refunded", group_indian(report.refunded_total)),
            ("Net", group_indian(report.net_total)),
            ("Cash to hand over", group_indian(report.cash_to_hand_over)),
            ("Cheques to hand over", group_indian(report.cheque_to_hand_over)),
        ],
        closed=closed,
    )
    c.record(
        session,
        action="fee.report.print_viewed",
        resource_type="fee_report",
        resource_id=None,
        summary={"report": "daily", "date": report.date.isoformat()},
    )
    return page


def _csv(header: list[str], rows: list[list[object]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(v) for v in row])
    return out.getvalue()


def daily_csv(session: Session, ctx: UserContext, day: dt.date | None) -> tuple[str, str]:
    """The day book CSV for entry into Tally (``fee.report``, step-up; audited
    ``fee.report.downloaded``). Returns (filename, text)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.REPORT)
    c.require_recent_mfa(ctx)
    when = day or c.today(session)
    _check_day(session, when)
    report = _report(session, when)
    rows: list[list[object]] = [
        [
            r.receipt_number,
            when.isoformat(),
            r.student_name,
            r.class_section,
            r.mode,
            f"{r.amount:.2f}",
            r.status,
            r.collected_by_name or "",
        ]
        for r in report.receipts
    ]
    rows += [
        [x.receipt_number, when.isoformat(), "", "", x.mode, f"-{x.amount:.2f}", "cancellation", ""]
        for x in report.cancellations
    ]
    rows += [
        [x.voucher_number, when.isoformat(), "", "", x.mode, f"-{x.amount:.2f}", "refund", ""]
        for x in report.refunds
    ]
    text = _csv(
        ["number", "date", "student", "class", "mode", "amount", "kind", "collected_by"], rows
    )
    c.record(
        session,
        action="fee.report.downloaded",
        resource_type="fee_report",
        resource_id=None,
        summary={"report": "day_book", "date": when.isoformat(), "rows": len(rows)},
    )
    return f"day-book-{when.isoformat()}.csv", text


def close_day(session: Session, ctx: UserContext, day: dt.date) -> DailyReportOut:
    """Close a day (``fee.manage``): records its totals and refuses later receipts,
    cancellations and refunds dated that day (409 ``day_closed``). Today or up to
    ``day_close.max_days_back`` days back. Audit ``fee.day.closed``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    today = c.today(session)
    if day > today:
        raise ValidationFailed([c.error("date", "in_future")])
    if day < today - dt.timedelta(days=c.settings().day_close.max_days_back):
        raise ValidationFailed([c.error("date", "too_old")])
    repo.lock_day(session, day, exclusive=True)
    if repo.get_day_close(session, day) is not None:
        raise c.DayClosed()
    report = _report(session, day)
    totals = {
        "by_mode": {m.mode: {"count": m.count, "total": f"{m.total:.2f}"} for m in report.by_mode},
        "cancelled_total": f"{report.cancelled_total:.2f}",
        "refunded_total": f"{report.refunded_total:.2f}",
        "net_total": f"{report.net_total:.2f}",
        "cash_to_hand_over": f"{report.cash_to_hand_over:.2f}",
        "cheque_to_hand_over": f"{report.cheque_to_hand_over:.2f}",
    }
    with c.db_errors():
        row = repo.insert_day_close(
            session,
            id=new_id(),
            tenant_id=repo.current_tenant_id(session),
            close_date=day,
            receipt_count=len(report.receipts),
            receipt_total=report.receipts_total,
            totals=totals,
            closed_by=ctx.membership_id,
        )
    c.record(
        session,
        action="fee.day.closed",
        resource_type="fee_day",
        resource_id=row.id,
        summary={
            "date": day.isoformat(),
            "receipts": len(report.receipts),
            "net_total": c.money_text(report.net_total),
        },
    )
    return _report(session, day)


# --- dues (FR-FEE-020) ---------------------------------------------------------------------------


def _dues(
    session: Session,
    ctx: UserContext,
    *,
    section_id: uuid.UUID | None,
    class_id: uuid.UUID | None,
) -> DuesOut:
    today = c.today(session)
    year = tenancy.get_current_academic_year(session)
    if year is None:
        return DuesOut(as_of=today, rows=[], students=0, total_overdue=ZERO)
    if section_id is None and class_id is None:
        accounts = repo.accounts_for_year(session, year.id)
    else:
        ids = students.list_students_in_scope(
            session,
            ctx,
            section_ids=[section_id] if section_id else None,
            class_ids=[class_id] if class_id else None,
        )
        accounts = repo.accounts_for_year(session, year.id, ids)
    dues = ledger.dues_of(session, accounts, today)
    owing = [a for a in accounts if dues[a.id].overdue > 0]
    people = students.summaries(session, ctx, {a.student_id for a in owing})
    last = repo.last_payment_dates(session, [a.id for a in owing])
    rows = []
    for a in owing:
        d = dues[a.id]
        s = people.get(a.student_id)
        rows.append(
            DuesRow(
                account_id=a.id,
                student_id=a.student_id,
                display_name=s.display_name if s else None,
                admission_no=s.admission_no if s else None,
                class_section=s.class_section if s else None,
                overdue=d.overdue,
                oldest_due_on=d.oldest_due_on,
                balance=d.balance,
                last_payment_on=last.get(a.id),
            )
        )
    rows.sort(key=lambda r: (r.oldest_due_on or dt.date.max, -r.overdue, r.display_name or ""))
    limit = c.settings().limits.dues_max_rows
    return DuesOut(
        as_of=today,
        rows=rows[:limit],
        students=len(rows),
        total_overdue=total(r.overdue for r in rows),
    )


def dues_list(
    session: Session, ctx: UserContext, *, section_id: uuid.UUID | None, class_id: uuid.UUID | None
) -> DuesOut:
    """Students with overdue amounts, oldest due first (``finance.read``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ)
    return _dues(session, ctx, section_id=section_id, class_id=class_id)


def dues_csv(
    session: Session, ctx: UserContext, *, section_id: uuid.UUID | None, class_id: uuid.UUID | None
) -> tuple[str, str]:
    """The dues list as CSV (``fee.report``, step-up; audited like an export)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.REPORT)
    c.require_recent_mfa(ctx)
    out = _dues(session, ctx, section_id=section_id, class_id=class_id)
    text = _csv(
        [
            "student",
            "admission_no",
            "class",
            "overdue",
            "oldest_due_on",
            "balance",
            "last_payment_on",
        ],
        [
            [
                r.display_name or "",
                r.admission_no or "",
                r.class_section or "",
                f"{r.overdue:.2f}",
                r.oldest_due_on.isoformat() if r.oldest_due_on else "",
                f"{r.balance:.2f}",
                r.last_payment_on.isoformat() if r.last_payment_on else "",
            ]
            for r in out.rows
        ],
    )
    c.record(
        session,
        action="fee.report.downloaded",
        resource_type="fee_report",
        resource_id=None,
        summary={
            "report": "dues",
            "rows": len(out.rows),
            "section_id": section_id,
            "class_id": class_id,
        },
    )
    return f"fee-dues-{out.as_of.isoformat()}.csv", text


__all__ = ["close_day", "daily_csv", "daily_print", "daily_report", "dues_csv", "dues_list"]
