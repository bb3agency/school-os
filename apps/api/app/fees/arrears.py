"""Earlier-year dues (arrears), the counter's view across years and "left with dues" (FM-01;
FR-FEE-031..045; ADR-0042 follow-up note D84; internal: other modules use ``app.fees.service``).

Fee accounts are per academic year and stay open until settled, so the dues of 2026-27 are
still owed in June 2027. This module reads every open account of the current and earlier years:

- :func:`years_in_view` is the current year and every earlier one (a year starting later than
  the current one is never "due").
- :func:`dues_accounts` feeds the dues list, CSV and tiles (``reports.py``).
- :func:`student_years` is the counter's view of one student, earlier years first, with the
  oldest-first split of an amount across accounts **only when the clerk asks for it**
  (``split_amount``); each part is then an ordinary receipt on its own account (FR-FEE-010).
- :func:`left_with_dues` lists students who left (status ``left`` or ``graduated``: withdrawn
  or TC issued) with an open balance. It is a follow-up list for management, never printed on a
  TC and never blocking one (FR-FEE-030, D16).
- :func:`earlier_years_due` and :func:`live_student_ids` serve the counter search, the parent
  dues page and the automatic reminders (which never include earlier years or students who left).

Fee permissions are school-wide only (FR-FEE-024); student names come from
``students.summaries`` under the caller's own scope (inv. 3), so a row outside it shows no name.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import NotFound, ValidationFailed
from app.fees import _common as c
from app.fees import arrears_repository as arepo
from app.fees import ledger
from app.fees import repository as repo
from app.fees.arrears_schemas import (
    LeftWithDuesOut,
    LeftWithDuesRow,
    SplitPart,
    StudentYearsOut,
    YearDue,
)
from app.fees.models import StudentFeeAccount
from app.fees.money import ZERO, total
from app.students import service as students
from app.tenancy import service as tenancy

WRITE_OFF_LABEL = "Written off by management"


@dataclass(frozen=True, slots=True)
class Years:
    current_id: uuid.UUID
    labels: dict[uuid.UUID, str]
    starts: dict[uuid.UUID, dt.date]

    def earlier(self, year_id: uuid.UUID) -> bool:
        return year_id != self.current_id

    def order(self, year_id: uuid.UUID) -> dt.date:
        return self.starts.get(year_id, dt.date.min)


def years_in_view(session: Session) -> Years | None:
    """The current academic year and every earlier one; ``None`` without a current year."""
    current = tenancy.get_current_academic_year(session)
    if current is None:
        return None
    kept = [y for y in tenancy.list_academic_years(session) if y.starts_on <= current.starts_on]
    return Years(
        current_id=current.id,
        labels={y.id: y.label for y in kept},
        starts={y.id: y.starts_on for y in kept},
    )


def accounts_of(
    session: Session, years: Years, student_ids: Collection[uuid.UUID] | None = None
) -> list[StudentFeeAccount]:
    return arepo.accounts_in_years(
        session, list(years.labels), current_year_id=years.current_id, student_ids=student_ids
    )


def dues_accounts(
    session: Session,
    ctx: UserContext,
    years: Years,
    *,
    section_id: uuid.UUID | None,
    class_id: uuid.UUID | None,
    arrears_only: bool,
) -> list[StudentFeeAccount]:
    """The accounts of the dues list (FR-FEE-031/032): the filter's students (their current
    class and section) or the whole school, every open account of the current and earlier
    years; ``arrears_only`` keeps the earlier years."""
    if section_id is None and class_id is None:
        accounts = accounts_of(session, years)
    else:
        ids = students.list_students_in_scope(
            session,
            ctx,
            section_ids=[section_id] if section_id else None,
            class_ids=[class_id] if class_id else None,
        )
        accounts = accounts_of(session, years, ids)
    if arrears_only:
        accounts = [a for a in accounts if years.earlier(a.academic_year_id)]
    return accounts


def earlier_years_due(
    session: Session, years: Years, student_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """Collectable dues of each student's open earlier-year accounts (FR-FEE-036/039)."""
    accounts = [
        a for a in accounts_of(session, years, student_ids) if years.earlier(a.academic_year_id)
    ]
    out: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    for dues in ledger.dues_of(session, accounts, c.today(session)).values():
        out[dues.account.student_id] += dues.collectable
    return dict(out)


def live_student_ids(session: Session, student_ids: Collection[uuid.UUID]) -> set[uuid.UUID]:
    """Students still at the school (``active`` or ``provisional``); automatic reminders skip
    the others (FR-FEE-034)."""
    statuses = students.record_statuses(session, student_ids)
    return {sid for sid, status in statuses.items() if status in students.LIVE_STATUSES}


# --- the counter (FR-FEE-036/037) ---------------------------------------------------------------


def split_oldest_first(
    parts: Sequence[tuple[uuid.UUID, Decimal]], amount: Decimal
) -> list[SplitPart]:
    """Take ``amount`` from each account's collectable dues in the given (oldest first) order.
    The caller checked ``amount`` against the total."""
    left = amount
    out: list[SplitPart] = []
    for account_id, collectable in parts:
        if left <= 0:
            break
        take = min(left, collectable)
        if take > 0:
            out.append(SplitPart(account_id=account_id, amount=take))
            left -= take
    return out


def student_years(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    *,
    split_amount: Decimal | None,
) -> StudentYearsOut:
    """The student's open accounts of the current and earlier years, **earlier years first**
    (``fee.collect`` or ``finance.read``, school-wide). With ``split_amount`` (the clerk ticked
    "oldest first across years"): the parts per account, oldest year first; 422
    ``split_amount`` ``above_outstanding`` above what is collectable, ``not_positive`` at or
    below zero. Nothing is written: each part is issued as its own receipt."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    if student_id not in students.summaries(session, ctx, [student_id]):
        raise NotFound("Student not found")
    today = c.today(session)
    years = years_in_view(session)
    if years is None:
        accounts: list[StudentFeeAccount] = []
        years = Years(current_id=uuid.UUID(int=0), labels={}, starts={})
    else:
        accounts = accounts_of(session, years, [student_id])
    dues = ledger.dues_of(session, accounts, today)
    ordered = sorted(accounts, key=lambda a: (years.order(a.academic_year_id), str(a.id)))
    rows = [
        YearDue(
            account_id=a.id,
            academic_year_id=a.academic_year_id,
            academic_year_label=years.labels.get(a.academic_year_id),
            earlier_year=years.earlier(a.academic_year_id),
            balance=dues[a.id].balance,
            overdue=dues[a.id].overdue,
            collectable=dues[a.id].collectable,
            oldest_due_on=dues[a.id].oldest_due_on,
        )
        for a in ordered
        if a.status == "open"
    ]
    collectable = total(r.collectable for r in rows)
    split = None
    if split_amount is not None:
        if split_amount <= 0:
            raise ValidationFailed([c.error("split_amount", "not_positive")])
        if split_amount > collectable:
            raise ValidationFailed([c.error("split_amount", "above_outstanding")])
        split = split_oldest_first([(r.account_id, r.collectable) for r in rows], split_amount)
    return StudentYearsOut(
        student_id=student_id,
        as_of=today,
        years=rows,
        earlier_years_due=total(r.collectable for r in rows if r.earlier_year),
        total_collectable=collectable,
        split=split,
    )


# --- left with dues (FR-FEE-038) ----------------------------------------------------------------


def left_with_dues_report(session: Session, ctx: UserContext) -> LeftWithDuesOut:
    """The report without its permission check (the caller checked it)."""
    today = c.today(session)
    years = years_in_view(session)
    if years is None:
        return LeftWithDuesOut(
            as_of=today, rows=[], students=0, total_balance=ZERO, written_off_total=ZERO
        )
    accounts = [a for a in accounts_of(session, years) if a.status == "open"]
    statuses = students.record_statuses(session, {a.student_id for a in accounts})
    left = [a for a in accounts if statuses.get(a.student_id) in ("left", "graduated")]
    dues = ledger.dues_of(session, left, today)
    owing = [a for a in left if dues[a.id].balance > 0]
    per: dict[uuid.UUID, list[StudentFeeAccount]] = defaultdict(list)
    for a in owing:
        per[a.student_id].append(a)
    people = students.summaries(session, ctx, set(per))
    last = repo.last_payment_dates(session, [a.id for a in owing])
    written = arepo.write_off_sums(session, [a.id for a in owing])
    rows: list[LeftWithDuesRow] = []
    for student_id, mine in per.items():
        s = people.get(student_id)
        dates = [d for a in mine if (d := dues[a.id].oldest_due_on) is not None]
        paid = [d for a in mine if (d := last.get(a.id)) is not None]
        status = statuses[student_id]
        rows.append(
            LeftWithDuesRow(
                student_id=student_id,
                display_name=s.display_name if s else None,
                admission_no=s.admission_no if s else None,
                status="graduated" if status == "graduated" else "left",
                accounts=len(mine),
                balance=total(dues[a.id].balance for a in mine),
                oldest_due_on=min(dates) if dates else None,
                last_payment_on=max(paid) if paid else None,
                written_off=total(written.get(a.id, ZERO) for a in mine),
            )
        )
    rows.sort(key=lambda r: (r.oldest_due_on or dt.date.max, -r.balance, r.display_name or ""))
    limit = c.settings().limits.dues_max_rows
    return LeftWithDuesOut(
        as_of=today,
        rows=rows[:limit],
        students=len(rows),
        total_balance=total(r.balance for r in rows),
        written_off_total=total(r.written_off for r in rows),
    )


def left_with_dues(session: Session, ctx: UserContext) -> LeftWithDuesOut:
    """Students who left (withdrawn, TC issued or graduated) with an open balance, for
    management follow-up (``finance.read`` school-wide). Information only (FR-FEE-030)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ)
    return left_with_dues_report(session, ctx)


__all__ = [
    "WRITE_OFF_LABEL",
    "Years",
    "accounts_of",
    "dues_accounts",
    "earlier_years_due",
    "left_with_dues",
    "left_with_dues_report",
    "live_student_ids",
    "split_oldest_first",
    "student_years",
    "years_in_view",
]
