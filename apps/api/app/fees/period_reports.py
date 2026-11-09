"""Accountant and management reports (FM-04; FR-FEE-061..075; ADR-0042; internal: other modules
use ``app.fees.service``).

Read-only, school-wide reports over the ``fin`` ledger for month-end and year-end questions:

- :func:`collection_register`: receipts numbered in an IST date range, by day, head, mode and
  collector, less the cancellations approved and refunds paid in the range. Cancelled receipts
  keep their number (the series stays gap-free) and are listed again apart (FR-FEE-061).
- :func:`dcb`: demand, concessions, adjustments, write-offs, collected and balance per class and
  section for an academic year, with the earlier-year dues still open (FR-FEE-064/065).
- :func:`registers`: approved concessions and adjustments (write-offs as "Written off by
  management"), cancellations and refunds of a range (FR-FEE-066/067).
- :func:`ageing`: overdue amounts by age, earlier-year dues in their own column (FR-FEE-068).
- :func:`series_check`: the receipt and refund voucher series of a financial year, first and last
  numbers and any missing number (FR-FEE-070).
- :func:`year_end_pack`: all of the above for a financial year as one ZIP (FR-FEE-071).

Views need ``finance.read`` school-wide; downloads need ``fee.report`` school-wide with step-up
and are audited ``fee.report.downloaded`` in the request's transaction (like the day book and
dues CSV); the print view is audited ``fee.report.print_viewed``. Cells are formula-safe and
Aadhaar-masked (``core.spreadsheet``). Student names come from ``students.summaries`` under the
caller's own scope (inv. 3): a row outside it shows no name. Free-text reasons are never in a
view and are in a register download only when asked for. Tuition tax certificates are FM-06.
"""

from __future__ import annotations

import datetime as dt
import io
import uuid
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import NotFound, ValidationFailed
from app.core.spreadsheet import CSV_MIME, XLSX_MIME, write_csv, write_xlsx
from app.fees import _common as c
from app.fees import arrears, ledger, period_templates
from app.fees import period_repository as prepo
from app.fees import repository as repo
from app.fees.models import Refund
from app.fees.money import IST, ZERO, group_indian, total
from app.fees.period_schemas import (
    AdjustmentEntry,
    AgeingBucket,
    AgeingClassRow,
    AgeingOut,
    AgeingRow,
    CollectionPart,
    CollectionRegisterOut,
    ConcessionEntry,
    DayTotal,
    DcbOut,
    DcbRow,
    EarlierYearBalance,
    HeadTotal,
    PeriodCancellation,
    PeriodCollectorTotal,
    PeriodModeTotal,
    PeriodReceipt,
    PeriodRefund,
    RegisterKind,
    RegistersOut,
    ReportFormat,
    SeriesCheck,
    SeriesOut,
    TypeTotal,
)
from app.fees.reports_config import ReportsConfig, load_config
from app.fees.tax_config import fy_bounds
from app.fees.templates import MODE_LABELS, format_date
from app.identity import service as identity
from app.students import service as students
from app.tenancy import service as tenancy

ADJUSTMENT_LABELS: Final = {
    "late_fee_waiver": "Late fee waived",
    "plan_change": "Fee plan changed",
    "concession_reversal": "Concession reversed",
    "correction": "Correction",
    "write_off": arrears.WRITE_OFF_LABEL,
    "other": "Other",
}
NO_SECTION: Final = "Not enrolled (left or moved)"
MODE_ORDER: Final = ("cash", "cheque", "bank_transfer", "upi_qr", "razorpay", "original_method")


def settings() -> ReportsConfig:
    return load_config()


@dataclass(frozen=True, slots=True)
class Download:
    filename: str
    media_type: str
    content: bytes


# --- shared --------------------------------------------------------------------------------------


def _view(session: Session, ctx: UserContext) -> None:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ)


def _download_gate(session: Session, ctx: UserContext) -> None:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.REPORT)
    c.require_recent_mfa(ctx)


def period(
    session: Session, date_from: dt.date | None, date_to: dt.date | None
) -> tuple[dt.date, dt.date]:
    """The IST range asked for: ``date_to`` defaults to today, ``date_from`` to the first of
    ``date_to``'s month. 422 ``date_to`` ``in_future``, ``date_from`` ``after_date_to``,
    ``date_from`` ``range_too_long`` (FR-FEE-061, ``max_range_days``)."""
    today = c.today(session)
    last = date_to or today
    first = date_from or last.replace(day=1)
    if last > today:
        raise ValidationFailed([c.error("date_to", "in_future")])
    if first > last:
        raise ValidationFailed([c.error("date_from", "after_date_to")])
    if (last - first).days + 1 > settings().max_range_days:
        raise ValidationFailed([c.error("date_from", "range_too_long")])
    return first, last


def _instants(first: dt.date, last: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """``[first 00:00 IST, last+1 00:00 IST)`` as aware datetimes."""
    start = dt.datetime.combine(first, dt.time.min, tzinfo=IST)
    end = dt.datetime.combine(last + dt.timedelta(days=1), dt.time.min, tzinfo=IST)
    return start, end


def _names(session: Session, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    return identity.member_display_names(session, c.unique_ids(ids))


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _file(stem: str, header: list[str], rows: list[list[object]], fmt: ReportFormat) -> Download:
    if fmt == "xlsx":
        return Download(
            f"{stem}.xlsx",
            XLSX_MIME,
            write_xlsx(header, rows, title=stem[:31], watermark="SchoolOS · keep in the office"),
        )
    return Download(f"{stem}.csv", f"{CSV_MIME}; charset=utf-8", write_csv(header, rows))


def _cap(rows: list[list[object]]) -> list[list[object]]:
    return rows[: settings().download_max_rows]


def _audit_download(session: Session, report: str, rows: int, **extra: object) -> None:
    c.record(
        session,
        action="fee.report.downloaded",
        resource_type="fee_report",
        resource_id=None,
        summary={"report": report, "rows": rows, **extra},
    )


# --- cancellations and refunds (shared by the register and the collection report) ----------------


def _cancellations(session: Session, first: dt.date, last: dt.date) -> list[PeriodCancellation]:
    rows = prepo.cancellations_between(session, first, last)
    receipts = repo.receipts_by_id(session, {x.receipt_id for x in rows})
    names = _names(session, [*(x.requested_by for x in rows), *(x.decided_by for x in rows)])
    return [
        PeriodCancellation(
            cancellation_id=x.id,
            receipt_id=x.receipt_id,
            receipt_number=receipts[x.receipt_id].receipt_number,
            receipt_date=receipts[x.receipt_id].receipt_date,
            cancelled_on=x.cancelled_on or first,
            amount=receipts[x.receipt_id].amount,
            mode=receipts[x.receipt_id].mode,
            reason_code=x.reason_code,
            requested_by_name=names.get(x.requested_by),
            approved_by_name=names.get(x.decided_by) if x.decided_by else None,
        )
        for x in rows
    ]


def _refunds(
    session: Session, first: dt.date, last: dt.date
) -> tuple[list[PeriodRefund], list[Refund]]:
    rows = prepo.refunds_between(session, first, last)
    receipts = repo.receipts_by_id(session, {x.receipt_id for x in rows})
    names = _names(session, [*(x.requested_by for x in rows), *(x.decided_by for x in rows)])
    out = [
        PeriodRefund(
            refund_id=x.id,
            voucher_number=x.voucher_number or "",
            voucher_date=x.voucher_date or first,
            receipt_number=receipts[x.receipt_id].receipt_number,
            amount=x.amount,
            mode=x.mode,
            requested_by_name=names.get(x.requested_by),
            approved_by_name=names.get(x.decided_by) if x.decided_by else None,
        )
        for x in rows
    ]
    return out, rows


# --- collection register (FR-FEE-061..063) -------------------------------------------------------


@dataclass
class _Collection:
    """The view (lists cut at the screen limit) and every row for downloads."""

    out: CollectionRegisterOut
    receipts: list[PeriodReceipt]
    cancellations: list[PeriodCancellation]
    refunds: list[PeriodRefund]


def _collection(session: Session, first: dt.date, last: dt.date) -> _Collection:
    lines = prepo.receipts_between(session, first, last)
    cancellations = _cancellations(session, first, last)
    refunds, refund_rows = _refunds(session, first, last)
    names = _names(session, (r.collected_by for r in lines))
    receipts = [
        PeriodReceipt(
            id=r.id,
            receipt_number=r.receipt_number,
            receipt_date=r.receipt_date,
            amount=r.amount,
            mode=r.mode,
            status=r.status,
            student_name=str(r.student.get("name", "")),
            class_section=str(r.student.get("class_section", "")),
            collected_by_name=names.get(r.collected_by) if r.collected_by else None,
        )
        for r in lines
    ]
    # by day
    days: dict[dt.date, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO, ZERO])
    counts: dict[dt.date, int] = defaultdict(int)
    for r in lines:
        days[r.receipt_date][0] += r.amount
        counts[r.receipt_date] += 1
    for x in cancellations:
        days[x.cancelled_on][1] += x.amount
    for y in refunds:
        days[y.voucher_date][2] += y.amount
    by_day = [
        DayTotal(
            date=d,
            receipts=counts.get(d, 0),
            gross=v[0],
            cancelled=v[1],
            refunded=v[2],
            net=v[0] - v[1] - v[2],
        )
        for d, v in sorted(days.items())
    ]
    # by mode
    modes: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO, ZERO])
    mode_counts: dict[str, int] = defaultdict(int)
    for r in lines:
        modes[r.mode][0] += r.amount
        mode_counts[r.mode] += 1
    for x in cancellations:
        modes[x.mode][1] += x.amount
    for y in refunds:
        modes[y.mode][2] += y.amount
    by_mode = [
        PeriodModeTotal(
            mode=m,
            receipts=mode_counts.get(m, 0),
            gross=v[0],
            cancelled=v[1],
            refunded=v[2],
            net=v[0] - v[1] - v[2],
        )
        for m, v in sorted(
            modes.items(),
            key=lambda kv: MODE_ORDER.index(kv[0]) if kv[0] in MODE_ORDER else len(MODE_ORDER),
        )
    ]
    # by collector
    who: dict[uuid.UUID | None, list[Decimal]] = defaultdict(list)
    for r in lines:
        who[r.collected_by].append(r.amount)
    by_collector = [
        PeriodCollectorTotal(
            membership_id=m,
            name=names.get(m) if m else None,
            receipts=len(amounts),
            gross=total(amounts),
        )
        for m, amounts in sorted(
            who.items(), key=lambda kv: ((names.get(kv[0]) if kv[0] else None) or "", str(kv[0]))
        )
    ]
    # by head
    collected = prepo.allocated_by_head(session, first=first, last=last)
    cancelled = prepo.allocations_of_receipts_by_head(
        session, {x.receipt_id for x in cancellations}
    )
    refunded = prepo.refunded_by_head(session, refund_rows)
    heads = repo.heads_by_id(session)
    keys = set(collected) | set(cancelled) | set(refunded)

    def head_key(k: uuid.UUID | None) -> tuple[int, int, str]:
        head = heads.get(k) if k else None
        if head is None:
            return (1, 0, "")
        return (0, head.sort_order, head.code)

    by_head = []
    for k in sorted(keys, key=head_key):
        head = heads.get(k) if k else None
        got, back, paid_out = collected.get(k, ZERO), cancelled.get(k, ZERO), refunded.get(k, ZERO)
        by_head.append(
            HeadTotal(
                head_id=k,
                head_code=head.code if head else None,
                head_name=head.name if head else ledger.LATE_FEE_NAME,
                collected=got,
                cancelled=back,
                refunded=paid_out,
                net=got - back - paid_out,
            )
        )
    gross = total(r.amount for r in lines)
    cancelled_total = total(x.amount for x in cancellations)
    refunded_total = total(y.amount for y in refunds)
    limit = settings().view_max_rows
    out = CollectionRegisterOut(
        date_from=first,
        date_to=last,
        by_day=by_day,
        by_head=by_head,
        by_mode=by_mode,
        by_collector=by_collector,
        receipts=receipts[:limit],
        receipts_count=len(receipts),
        truncated=len(receipts) > limit,
        cancellations=cancellations[:limit],
        refunds=refunds[:limit],
        receipts_total=gross,
        cancelled_total=cancelled_total,
        refunded_total=refunded_total,
        net_total=gross - cancelled_total - refunded_total,
    )
    return _Collection(out, receipts, cancellations, refunds)


def collection_register(
    session: Session, ctx: UserContext, date_from: dt.date | None, date_to: dt.date | None
) -> CollectionRegisterOut:
    """FR-FEE-061 (``finance.read``)."""
    _view(session, ctx)
    first, last = period(session, date_from, date_to)
    return _collection(session, first, last).out


def _period_text(first: dt.date, last: dt.date) -> str:
    return f"{format_date(first)} to {format_date(last)}"


def collection_print(
    session: Session, ctx: UserContext, date_from: dt.date | None, date_to: dt.date | None
) -> str:
    """FR-FEE-062: the A4 print page (``finance.read``); audit ``fee.report.print_viewed``."""
    _view(session, ctx)
    first, last = period(session, date_from, date_to)
    report = _collection(session, first, last).out
    g = group_indian
    page = period_templates.render_collection_register(
        school_name=tenancy.get_tenant(session).name,
        period=_period_text(first, last),
        receipts_note=(
            f"{report.receipts_count} receipts numbered in the period. The receipt list is in "
            "the download."
        ),
        totals=[
            ("Receipts (gross)", g(report.receipts_total)),
            ("Cancelled", g(report.cancelled_total)),
            ("Refunded", g(report.refunded_total)),
            ("Net collected", g(report.net_total)),
        ],
        by_day=[
            (
                format_date(d.date),
                str(d.receipts),
                g(d.gross),
                g(d.cancelled),
                g(d.refunded),
                g(d.net),
            )
            for d in report.by_day
        ],
        by_head=[
            (h.head_name, g(h.collected), g(h.cancelled), g(h.refunded), g(h.net))
            for h in report.by_head
        ],
        by_mode=[
            (
                MODE_LABELS.get(m.mode, "Original method"),
                str(m.receipts),
                g(m.gross),
                g(m.cancelled),
                g(m.refunded),
                g(m.net),
            )
            for m in report.by_mode
        ],
        by_collector=[
            (x.name or "Online", str(x.receipts), g(x.gross)) for x in report.by_collector
        ],
        cancellations=[
            (
                x.receipt_number,
                format_date(x.receipt_date),
                format_date(x.cancelled_on),
                x.reason_code,
                g(x.amount),
            )
            for x in report.cancellations
        ],
        refunds=[
            (y.voucher_number, format_date(y.voucher_date), y.receipt_number, y.mode, g(y.amount))
            for y in report.refunds
        ],
    )
    c.record(
        session,
        action="fee.report.print_viewed",
        resource_type="fee_report",
        resource_id=None,
        summary={"report": "collections", "from": first.isoformat(), "to": last.isoformat()},
    )
    return page


def _collection_table(
    data: _Collection, part: CollectionPart
) -> tuple[list[str], list[list[object]]]:
    out = data.out
    if part == "heads":
        return (
            ["head_code", "head", "collected", "cancelled", "refunded", "net"],
            [
                [
                    h.head_code or "",
                    h.head_name,
                    _money(h.collected),
                    _money(h.cancelled),
                    _money(h.refunded),
                    _money(h.net),
                ]
                for h in out.by_head
            ],
        )
    if part == "days":
        return (
            ["date", "receipts", "gross", "cancelled", "refunded", "net"],
            [
                [
                    d.date.isoformat(),
                    d.receipts,
                    _money(d.gross),
                    _money(d.cancelled),
                    _money(d.refunded),
                    _money(d.net),
                ]
                for d in out.by_day
            ],
        )
    if part == "modes":
        return (
            ["mode", "receipts", "gross", "cancelled", "refunded", "net"],
            [
                [
                    m.mode,
                    m.receipts,
                    _money(m.gross),
                    _money(m.cancelled),
                    _money(m.refunded),
                    _money(m.net),
                ]
                for m in out.by_mode
            ],
        )
    if part == "collectors":
        return (
            ["collected_by", "receipts", "gross"],
            [[x.name or "Online", x.receipts, _money(x.gross)] for x in out.by_collector],
        )
    rows: list[list[object]] = [
        [
            r.receipt_number,
            r.receipt_date.isoformat(),
            r.student_name,
            r.class_section,
            r.mode,
            _money(r.amount),
            "receipt",
            r.status,
            r.collected_by_name or "",
        ]
        for r in data.receipts
    ]
    rows += [
        [
            x.receipt_number,
            x.cancelled_on.isoformat(),
            "",
            "",
            x.mode,
            f"-{x.amount:.2f}",
            "cancellation",
            x.reason_code,
            x.approved_by_name or "",
        ]
        for x in data.cancellations
    ]
    rows += [
        [
            y.voucher_number,
            y.voucher_date.isoformat(),
            "",
            "",
            y.mode,
            f"-{y.amount:.2f}",
            "refund",
            y.receipt_number,
            y.approved_by_name or "",
        ]
        for y in data.refunds
    ]
    header = ["number", "date", "student", "class", "mode", "amount", "kind", "status_or_ref", "by"]
    return header, rows


def collection_download(
    session: Session,
    ctx: UserContext,
    date_from: dt.date | None,
    date_to: dt.date | None,
    *,
    part: CollectionPart,
    fmt: ReportFormat,
) -> Download:
    """FR-FEE-063 (``fee.report``, step-up; audited ``fee.report.downloaded``)."""
    _download_gate(session, ctx)
    first, last = period(session, date_from, date_to)
    data = _collection(session, first, last)
    header, rows = _collection_table(data, part)
    rows = _cap(rows)
    _audit_download(
        session,
        "collections",
        len(rows),
        part=part,
        format=fmt,
        **{"from": first.isoformat(), "to": last.isoformat()},
    )
    return _file(
        f"fee-collections-{part}-{first.isoformat()}-{last.isoformat()}", header, rows, fmt
    )


# --- demand, collection, balance (FR-FEE-064/065) ------------------------------------------------


@dataclass
class _Dcb:
    accounts: int = 0
    demand: Decimal = ZERO
    concessions: Decimal = ZERO
    late_fees: Decimal = ZERO
    adjustments: Decimal = ZERO
    written_off: Decimal = ZERO
    collected: Decimal = ZERO
    refunded: Decimal = ZERO

    def add(self, s: prepo.AccountSums) -> None:
        g = s.sums.get
        self.accounts += 1
        self.demand += g("charge", ZERO)
        self.concessions += -g("concession", ZERO)
        self.late_fees += g("late_fee", ZERO)
        self.adjustments += g("adjustment", ZERO) - s.write_off
        self.written_off += -s.write_off
        self.collected += -(g("payment", ZERO) + g("payment_reversal", ZERO))
        self.refunded += g("refund", ZERO)

    def row(
        self,
        class_id: uuid.UUID | None,
        class_label: str,
        section_id: uuid.UUID | None,
        section_label: str | None,
    ) -> DcbRow:
        net = self.demand - self.concessions + self.late_fees + self.adjustments - self.written_off
        return DcbRow(
            class_id=class_id,
            class_label=class_label,
            section_id=section_id,
            section_label=section_label,
            accounts=self.accounts,
            demand=self.demand,
            concessions=self.concessions,
            late_fees=self.late_fees,
            adjustments=self.adjustments,
            written_off=self.written_off,
            net_demand=net,
            collected=self.collected,
            refunded=self.refunded,
            balance=net - self.collected + self.refunded,
        )


def _dcb(session: Session, academic_year_id: uuid.UUID | None) -> DcbOut:
    if academic_year_id is None:
        year = tenancy.get_current_academic_year(session)
        if year is None:
            raise ValidationFailed([c.error("academic_year_id", "missing")])
    else:
        try:
            year = tenancy.get_academic_year(session, academic_year_id)
        except NotFound:
            raise NotFound("Academic year not found") from None
    all_years = tenancy.list_academic_years(session)
    earlier = [y for y in all_years if y.starts_on < year.starts_on]
    sums = prepo.account_sums(
        session, [year.id, *(y.id for y in earlier)], open_only_for=[y.id for y in earlier]
    )
    mine = [s for s in sums if s.academic_year_id == year.id]
    sections = {s.id: s for s in tenancy.list_sections(session, academic_year_id=year.id)}
    placed = {
        e.student_id: e.section_id
        for e in students.enrolled_in_sections(
            session, academic_year_id=year.id, section_ids=list(sections)
        )
    }
    plans = repo.plans_by_id(session, {s.plan_id for s in mine})
    classes = {k.id: k for k in tenancy.list_classes(session)}
    groups: dict[tuple[uuid.UUID | None, uuid.UUID | None], _Dcb] = defaultdict(_Dcb)
    grand = _Dcb()
    for s in mine:
        section_id = placed.get(s.student_id)
        section = sections.get(section_id) if section_id else None
        plan = plans.get(s.plan_id)
        class_id = section.class_id if section else (plan.class_id if plan else None)
        groups[(class_id, section.id if section else None)].add(s)
        grand.add(s)

    def order(key: tuple[uuid.UUID | None, uuid.UUID | None]) -> tuple[int, str, int, str]:
        klass = classes.get(key[0]) if key[0] else None
        section = sections.get(key[1]) if key[1] else None
        return (
            klass.sort_order if klass else 10_000,
            klass.code if klass else "",
            0 if section else 1,
            section.name if section else "",
        )

    rows = []
    for key in sorted(groups, key=order):
        klass = classes.get(key[0]) if key[0] else None
        section = sections.get(key[1]) if key[1] else None
        rows.append(
            groups[key].row(
                key[0],
                klass.display_en if klass else "—",
                key[1],
                section.name if section else NO_SECTION,
            )
        )
    # earlier years still open (FM-01)
    labels = {y.id: y.label for y in earlier}
    per_year: dict[uuid.UUID, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    per_count: dict[uuid.UUID, int] = defaultdict(int)
    for s in sums:
        if s.academic_year_id == year.id:
            continue
        balance = total(s.sums.values())
        if balance == 0 and s.write_off == 0:
            continue
        per_year[s.academic_year_id][0] += balance
        per_year[s.academic_year_id][1] += -s.write_off
        per_count[s.academic_year_id] += 1
    starts = {y.id: y.starts_on for y in earlier}
    earlier_rows = [
        EarlierYearBalance(
            academic_year_id=yid,
            academic_year_label=labels[yid],
            accounts=per_count[yid],
            balance=v[0],
            written_off=v[1],
        )
        for yid, v in sorted(per_year.items(), key=lambda kv: starts[kv[0]])
    ]
    return DcbOut(
        academic_year_id=year.id,
        academic_year_label=year.label,
        as_of=c.today(session),
        rows=rows,
        totals=grand.row(None, "All classes", None, None),
        earlier_years=earlier_rows,
        earlier_years_balance=total(r.balance for r in earlier_rows),
    )


def dcb(session: Session, ctx: UserContext, academic_year_id: uuid.UUID | None) -> DcbOut:
    """FR-FEE-064/065 (``finance.read``); 404 for a year that is not this school's."""
    _view(session, ctx)
    return _dcb(session, academic_year_id)


_DCB_HEADER: Final = [
    "class",
    "section",
    "accounts",
    "demand",
    "concessions",
    "late_fees",
    "adjustments",
    "written_off",
    "net_demand",
    "collected",
    "refunded",
    "balance",
]


def _dcb_rows(out: DcbOut) -> list[list[object]]:
    def line(r: DcbRow) -> list[object]:
        return [
            r.class_label,
            r.section_label or "",
            r.accounts,
            _money(r.demand),
            _money(r.concessions),
            _money(r.late_fees),
            _money(r.adjustments),
            _money(r.written_off),
            _money(r.net_demand),
            _money(r.collected),
            _money(r.refunded),
            _money(r.balance),
        ]

    rows = [line(r) for r in out.rows] + [line(out.totals)]
    rows += [
        [
            f"Earlier year {e.academic_year_label}",
            "",
            e.accounts,
            "",
            "",
            "",
            "",
            _money(e.written_off),
            "",
            "",
            "",
            _money(e.balance),
        ]
        for e in out.earlier_years
    ]
    return rows


def dcb_download(
    session: Session, ctx: UserContext, academic_year_id: uuid.UUID | None, *, fmt: ReportFormat
) -> Download:
    _download_gate(session, ctx)
    out = _dcb(session, academic_year_id)
    rows = _cap(_dcb_rows(out))
    _audit_download(
        session, "dcb", len(rows), format=fmt, academic_year_id=str(out.academic_year_id)
    )
    return _file(f"fee-dcb-{out.academic_year_label}", _DCB_HEADER, rows, fmt)


# --- registers (FR-FEE-066/067) ------------------------------------------------------------------


@dataclass
class _Registers:
    out: RegistersOut
    concessions: list[ConcessionEntry]
    adjustments: list[AdjustmentEntry]
    cancellations: list[PeriodCancellation]
    refunds: list[PeriodRefund]
    reasons: dict[uuid.UUID, str]


def _registers(session: Session, ctx: UserContext, first: dt.date, last: dt.date) -> _Registers:
    start, end = _instants(first, last)
    concessions = prepo.concessions_decided_between(session, start, end)
    adjustments = prepo.adjustments_decided_between(session, start, end)
    accounts = repo.accounts_by_id(
        session, list({*(x.account_id for x in concessions), *(x.account_id for x in adjustments)})
    )
    people = students.summaries(session, ctx, {a.student_id for a in accounts.values()})
    years = {y.id: y.label for y in tenancy.list_academic_years(session)}
    heads = repo.heads_by_id(session)
    names = _names(
        session,
        [
            *(x.requested_by for x in concessions),
            *(x.decided_by for x in concessions),
            *(x.requested_by for x in adjustments),
            *(x.decided_by for x in adjustments),
        ],
    )

    def who(account_id: uuid.UUID) -> dict[str, object]:
        a = accounts[account_id]
        s = people.get(a.student_id)
        return {
            "account_id": a.id,
            "student_id": a.student_id,
            "display_name": s.display_name if s else None,
            "admission_no": s.admission_no if s else None,
            "class_section": s.class_section if s else None,
            "academic_year_label": years.get(a.academic_year_id),
        }

    def day(at: dt.datetime | None, fallback: dt.datetime) -> dt.date:
        return (at or fallback).astimezone(IST).date()

    con_rows = [
        ConcessionEntry(
            concession_id=x.id,
            concession_type=x.concession_type,
            basis=x.basis,
            value=x.value,
            amount=x.total_amount or ZERO,
            requested_by_name=names.get(x.requested_by),
            approved_by_name=names.get(x.decided_by) if x.decided_by else None,
            approved_on=day(x.decided_at, x.requested_at),
            **who(x.account_id),
        )
        for x in concessions
    ]
    adj_rows = [
        AdjustmentEntry(
            adjustment_id=x.id,
            reason_code=x.reason_code,
            reason_label=ADJUSTMENT_LABELS.get(x.reason_code, x.reason_code),
            head_name=(
                heads[x.head_id].name if x.head_id and x.head_id in heads else ledger.LATE_FEE_NAME
            ),
            instalment_no=x.instalment_no,
            amount=x.amount,
            requested_by_name=names.get(x.requested_by),
            approved_by_name=names.get(x.decided_by) if x.decided_by else None,
            approved_on=day(x.decided_at, x.requested_at),
            **who(x.account_id),
        )
        for x in adjustments
    ]
    cancellations = _cancellations(session, first, last)
    refunds, refund_rows = _refunds(session, first, last)
    cancel_rows = prepo.cancellations_between(session, first, last)
    reasons: dict[uuid.UUID, str] = {x.id: x.reason for x in concessions}
    reasons.update({x.id: x.reason for x in adjustments})
    reasons.update({x.id: x.reason for x in cancel_rows})
    reasons.update({x.id: x.reason for x in refund_rows})

    def by(rows: Sequence[tuple[str, Decimal]]) -> list[TypeTotal]:
        acc: dict[str, list[Decimal]] = defaultdict(list)
        for key, amount in rows:
            acc[key].append(amount)
        return [TypeTotal(code=k, count=len(v), total=total(v)) for k, v in sorted(acc.items())]

    limit = settings().view_max_rows
    out = RegistersOut(
        date_from=first,
        date_to=last,
        concessions=con_rows[:limit],
        concessions_by_type=by([(x.concession_type, x.amount) for x in con_rows]),
        concessions_total=total(x.amount for x in con_rows),
        adjustments=adj_rows[:limit],
        adjustments_by_reason=by([(x.reason_code, x.amount) for x in adj_rows]),
        written_off_total=-total(x.amount for x in adj_rows if x.reason_code == "write_off"),
        cancellations=cancellations[:limit],
        cancelled_total=total(x.amount for x in cancellations),
        refunds=refunds[:limit],
        refunded_total=total(x.amount for x in refunds),
        truncated=max(len(con_rows), len(adj_rows), len(cancellations), len(refunds)) > limit,
    )
    return _Registers(out, con_rows, adj_rows, cancellations, refunds, reasons)


def registers(
    session: Session, ctx: UserContext, date_from: dt.date | None, date_to: dt.date | None
) -> RegistersOut:
    """FR-FEE-066/067 (``finance.read``). No free-text reasons."""
    _view(session, ctx)
    first, last = period(session, date_from, date_to)
    return _registers(session, ctx, first, last).out


def _register_table(
    data: _Registers, kind: RegisterKind, *, include_reasons: bool
) -> tuple[list[str], list[list[object]]]:
    def reason(row_id: uuid.UUID) -> list[object]:
        return [data.reasons.get(row_id, "")] if include_reasons else []

    extra = ["reason"] if include_reasons else []
    if kind == "concessions":
        return (
            [
                "approved_on",
                "student",
                "admission_no",
                "class",
                "academic_year",
                "type",
                "basis",
                "value",
                "amount",
                "requested_by",
                "approved_by",
                *extra,
            ],
            [
                [
                    x.approved_on.isoformat(),
                    x.display_name or "",
                    x.admission_no or "",
                    x.class_section or "",
                    x.academic_year_label or "",
                    x.concession_type,
                    x.basis,
                    _money(x.value),
                    _money(x.amount),
                    x.requested_by_name or "",
                    x.approved_by_name or "",
                    *reason(x.concession_id),
                ]
                for x in data.concessions
            ],
        )
    if kind == "adjustments":
        return (
            [
                "approved_on",
                "student",
                "admission_no",
                "class",
                "academic_year",
                "reason_code",
                "reason_label",
                "head",
                "instalment",
                "amount",
                "requested_by",
                "approved_by",
                *extra,
            ],
            [
                [
                    x.approved_on.isoformat(),
                    x.display_name or "",
                    x.admission_no or "",
                    x.class_section or "",
                    x.academic_year_label or "",
                    x.reason_code,
                    x.reason_label,
                    x.head_name or "",
                    x.instalment_no,
                    _money(x.amount),
                    x.requested_by_name or "",
                    x.approved_by_name or "",
                    *reason(x.adjustment_id),
                ]
                for x in data.adjustments
            ],
        )
    if kind == "cancellations":
        return (
            [
                "receipt",
                "receipt_date",
                "cancelled_on",
                "mode",
                "amount",
                "reason_code",
                "requested_by",
                "approved_by",
                *extra,
            ],
            [
                [
                    x.receipt_number,
                    x.receipt_date.isoformat(),
                    x.cancelled_on.isoformat(),
                    x.mode,
                    _money(x.amount),
                    x.reason_code,
                    x.requested_by_name or "",
                    x.approved_by_name or "",
                    *reason(x.cancellation_id),
                ]
                for x in data.cancellations
            ],
        )
    return (
        [
            "voucher",
            "voucher_date",
            "receipt",
            "mode",
            "amount",
            "requested_by",
            "approved_by",
            *extra,
        ],
        [
            [
                y.voucher_number,
                y.voucher_date.isoformat(),
                y.receipt_number,
                y.mode,
                _money(y.amount),
                y.requested_by_name or "",
                y.approved_by_name or "",
                *reason(y.refund_id),
            ]
            for y in data.refunds
        ],
    )


def register_download(
    session: Session,
    ctx: UserContext,
    date_from: dt.date | None,
    date_to: dt.date | None,
    *,
    kind: RegisterKind,
    fmt: ReportFormat,
    include_reasons: bool,
) -> Download:
    """FR-FEE-066/067 downloads; reasons only with ``include_reasons`` (audited with it)."""
    _download_gate(session, ctx)
    first, last = period(session, date_from, date_to)
    data = _registers(session, ctx, first, last)
    header, rows = _register_table(data, kind, include_reasons=include_reasons)
    rows = _cap(rows)
    _audit_download(
        session,
        f"register_{kind}",
        len(rows),
        format=fmt,
        include_reasons=include_reasons,
        **{"from": first.isoformat(), "to": last.isoformat()},
    )
    return _file(f"fee-{kind}-{first.isoformat()}-{last.isoformat()}", header, rows, fmt)


# --- ageing (FR-FEE-068) -------------------------------------------------------------------------


def _bucket_index(age: int, days: Sequence[int]) -> int:
    for i, limit in enumerate(days):
        if age <= limit:
            return i
    return len(days)


def _ageing(session: Session, ctx: UserContext) -> tuple[AgeingOut, list[AgeingRow]]:
    today = c.today(session)
    days = settings().ageing_days
    n = len(days) + 1
    bands = [
        AgeingBucket(
            from_days=(days[i - 1] + 1 if i else 0), to_days=days[i] if i < len(days) else None
        )
        for i in range(n)
    ]
    years = arrears.years_in_view(session)
    accounts = arrears.accounts_of(session, years) if years is not None else []
    dues = ledger.dues_of(session, accounts, today)
    per: dict[uuid.UUID, list[Decimal]] = defaultdict(lambda: [ZERO] * (n + 1))
    oldest: dict[uuid.UUID, dt.date] = {}
    not_due = ZERO
    for a in accounts:
        d = dues[a.id]
        earlier = years is not None and years.earlier(a.academic_year_id)
        if earlier:
            if d.overdue > 0:
                per[a.student_id][n] += d.overdue
        else:
            for _, line in d.lines:
                if not line.collectable or line.due <= 0:
                    continue
                if line.overdue and line.due_on is not None:
                    per[a.student_id][_bucket_index((today - line.due_on).days, days)] += line.due
                else:
                    not_due += line.due
        if d.overdue > 0 and d.oldest_due_on is not None:
            prev = oldest.get(a.student_id)
            oldest[a.student_id] = min(prev, d.oldest_due_on) if prev else d.oldest_due_on
    people = students.summaries(session, ctx, set(per))
    rows = []
    for sid, amounts in per.items():
        overdue = total(amounts)
        if overdue <= 0:
            continue
        s = people.get(sid)
        rows.append(
            AgeingRow(
                student_id=sid,
                display_name=s.display_name if s else None,
                admission_no=s.admission_no if s else None,
                class_section=s.class_section if s else None,
                bands=amounts[:n],
                earlier_years=amounts[n],
                overdue=overdue,
                oldest_due_on=oldest.get(sid),
            )
        )
    rows.sort(key=lambda r: (r.oldest_due_on or dt.date.max, -r.overdue, r.display_name or ""))
    groups: dict[str | None, list[AgeingRow]] = defaultdict(list)
    for r in rows:
        groups[r.class_section].append(r)
    by_class = [
        AgeingClassRow(
            class_section=k,
            students=len(v),
            bands=[total(r.bands[i] for r in v) for i in range(n)],
            earlier_years=total(r.earlier_years for r in v),
            overdue=total(r.overdue for r in v),
        )
        for k, v in sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))
    ]
    limit = settings().view_max_rows
    out = AgeingOut(
        as_of=today,
        bands=bands,
        rows=rows[:limit],
        truncated=len(rows) > limit,
        by_class=by_class,
        totals=[total(r.bands[i] for r in rows) for i in range(n)],
        earlier_years_total=total(r.earlier_years for r in rows),
        overdue_total=total(r.overdue for r in rows),
        not_yet_due_total=not_due,
    )
    return out, rows


def ageing(session: Session, ctx: UserContext) -> AgeingOut:
    """FR-FEE-068 (``finance.read``): overdue today by age, earlier years apart."""
    _view(session, ctx)
    return _ageing(session, ctx)[0]


def _bucket_title(b: AgeingBucket) -> str:
    return (
        f"days_{b.from_days}_{b.to_days}"
        if b.to_days is not None
        else f"days_over_{b.from_days - 1}"
    )


def _ageing_table(out: AgeingOut, rows: list[AgeingRow]) -> tuple[list[str], list[list[object]]]:
    header = [
        "student",
        "admission_no",
        "class",
        *(_bucket_title(b) for b in out.bands),
        "earlier_years",
        "overdue",
        "oldest_due_on",
    ]
    return header, [
        [
            r.display_name or "",
            r.admission_no or "",
            r.class_section or "",
            *(_money(v) for v in r.bands),
            _money(r.earlier_years),
            _money(r.overdue),
            r.oldest_due_on.isoformat() if r.oldest_due_on else "",
        ]
        for r in rows
    ]


def ageing_download(session: Session, ctx: UserContext, *, fmt: ReportFormat) -> Download:
    _download_gate(session, ctx)
    out, all_rows = _ageing(session, ctx)
    header, rows = _ageing_table(out, all_rows)
    rows = _cap(rows)
    _audit_download(session, "ageing", len(rows), format=fmt)
    return _file(f"fee-ageing-{out.as_of.isoformat()}", header, rows, fmt)


# --- receipt series (FR-FEE-070) -----------------------------------------------------------------


def _fy(session: Session, financial_year: str | None) -> tuple[str, dt.date, dt.date]:
    today = c.today(session)
    label = financial_year or c.fy(today)
    bounds = fy_bounds(label, c.settings().numbering.financial_year_start_month)
    if bounds is None:
        raise ValidationFailed([c.error("financial_year", "invalid")])
    if bounds[0] > today:
        raise ValidationFailed([c.error("financial_year", "in_future")])
    return label, bounds[0], min(bounds[1], today)


def _missing(numbers: Iterable[int], counter: int) -> list[int]:
    have = set(numbers)
    top = max([counter, *have]) if have or counter else 0
    return [n for n in range(1, top + 1) if n not in have]


def _series(session: Session, label: str) -> SeriesOut:
    numbering = c.settings().numbering
    shown = settings().series_missing_shown
    receipts = prepo.receipt_numbers(session, label)
    counter = prepo.counter_value(session, numbering.receipt.series, label)
    missing = _missing((n for n, _, _ in receipts), counter)
    vouchers = prepo.voucher_numbers(session, label)
    v_counter = prepo.counter_value(session, numbering.refund.series, label)
    v_missing = _missing((n for n, _ in vouchers), v_counter)
    checks = [
        SeriesCheck(
            series="receipt",
            financial_year=label,
            first_number=receipts[0][1] if receipts else None,
            last_number=receipts[-1][1] if receipts else None,
            used=len(receipts),
            cancelled=sum(1 for _, _, status in receipts if status == "cancelled"),
            counter=counter,
            missing_count=len(missing),
            missing=missing[:shown],
        ),
        SeriesCheck(
            series="refund",
            financial_year=label,
            first_number=vouchers[0][1] if vouchers else None,
            last_number=vouchers[-1][1] if vouchers else None,
            used=len(vouchers),
            cancelled=0,
            counter=v_counter,
            missing_count=len(v_missing),
            missing=v_missing[:shown],
        ),
    ]
    return SeriesOut(
        financial_year=label,
        series=checks,
        gap_free=all(x.missing_count == 0 for x in checks),
    )


def series_check(session: Session, ctx: UserContext, financial_year: str | None) -> SeriesOut:
    """FR-FEE-070 (``finance.read``); default the current financial year. 422
    ``financial_year`` (``invalid``, ``in_future``)."""
    _view(session, ctx)
    label, _, _ = _fy(session, financial_year)
    return _series(session, label)


# --- year-end pack (FR-FEE-071) ------------------------------------------------------------------


def year_end_pack(session: Session, ctx: UserContext, financial_year: str) -> Download:
    """Every report of a financial year in one ZIP of CSV files for the auditor and the trust's
    CA (``fee.report``, step-up; audited once as ``fee.report.downloaded`` report
    ``year_end_pack``). The period ends today while the year is running; the DCB covers each
    academic year that starts in the financial year; the ageing is as of today."""
    _download_gate(session, ctx)
    label, first, last = _fy(session, financial_year)
    files: list[tuple[str, list[str], list[list[object]]]] = []
    collection = _collection(session, first, last)
    for part in ("receipts", "heads", "days", "modes", "collectors"):
        header, rows = _collection_table(collection, part)
        files.append((f"collections-{part}.csv", header, _cap(rows)))
    regs = _registers(session, ctx, first, last)
    for kind in ("concessions", "adjustments", "cancellations", "refunds"):
        header, rows = _register_table(regs, kind, include_reasons=False)
        files.append((f"register-{kind}.csv", header, _cap(rows)))
    years = [y for y in tenancy.list_academic_years(session) if first <= y.starts_on <= last]
    for y in sorted(years, key=lambda y: y.starts_on):
        out = _dcb(session, y.id)
        files.append((f"dcb-{y.label}.csv", _DCB_HEADER, _cap(_dcb_rows(out))))
    ageing_out, ageing_rows = _ageing(session, ctx)
    header, rows = _ageing_table(ageing_out, ageing_rows)
    files.append((f"ageing-{ageing_out.as_of.isoformat()}.csv", header, _cap(rows)))
    series = _series(session, label)
    files.append(
        (
            "receipt-series.csv",
            [
                "series",
                "financial_year",
                "first_number",
                "last_number",
                "used",
                "cancelled",
                "counter",
                "missing_count",
                "missing",
            ],
            [
                [
                    x.series,
                    x.financial_year,
                    x.first_number or "",
                    x.last_number or "",
                    x.used,
                    x.cancelled,
                    x.counter,
                    x.missing_count,
                    " ".join(str(n) for n in x.missing),
                ]
                for x in series.series
            ],
        )
    )
    readme = (
        f"SchoolOS fee year-end pack\r\nFinancial year: {label}\r\n"
        f"Period: {first.isoformat()} to {last.isoformat()} (IST dates)\r\n"
        f"Generated on: {c.today(session).isoformat()}\r\n"
        f"Receipts (gross): {_money(collection.out.receipts_total)}\r\n"
        f"Cancelled: {_money(collection.out.cancelled_total)}\r\n"
        f"Refunded: {_money(collection.out.refunded_total)}\r\n"
        f"Net collected: {_money(collection.out.net_total)}\r\n"
        f"Receipt series gap-free: {'yes' if series.gap_free else 'NO, see receipt-series.csv'}\r\n"
        "Cancelled receipts keep their numbers and are listed in register-cancellations.csv.\r\n"
        "Write-offs are adjustments with reason write_off (Written off by management).\r\n"
        "Personal financial data: keep it in the office.\r\n"
    )
    buf = io.BytesIO()
    stamp = (last.year, last.month, last.day, 0, 0, 0)
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("README.txt", date_time=stamp)
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, readme.encode("utf-8"))
        for name, header, rows in files:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, write_csv(header, rows))
    _audit_download(
        session,
        "year_end_pack",
        sum(len(rows) for _, _, rows in files),
        financial_year=label,
        files=len(files) + 1,
    )
    return Download(f"fee-year-end-{label}.zip", "application/zip", buf.getvalue())


__all__ = [
    "ADJUSTMENT_LABELS",
    "Download",
    "ageing",
    "ageing_download",
    "collection_download",
    "collection_print",
    "collection_register",
    "dcb",
    "dcb_download",
    "period",
    "register_download",
    "registers",
    "series_check",
    "year_end_pack",
]
