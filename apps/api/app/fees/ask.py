"""Fee figures for Ask the school (FR-KB-044, US-2112; docs/06 §7, §13.4).

``get_fee_dues`` reads the SchoolOS fee ledger through these two functions while fees are on
for the school (FR-FEE-001); Tally is then not consulted. Both require ``finance.read`` held
school-wide (FR-FEE-024: fee permissions are never section-scoped) and answer 404
``module_hidden`` while fees are off. Amounts and display fields only; nothing is written, so
nothing is audited here (the Ask route audits the question, invariant 7).

- :func:`student_fees`: ONE student the caller may read (``students.summaries``, 404 otherwise):
  the current year's account balance, the overdue amount SchoolOS collects, what is due per
  instalment, the oldest overdue date and the last payment date. A student without an account
  for the year comes back with ``has_account`` false (no figure, never a guess).
- :func:`school_fees`: numbers per class and for the school (no names): students owing,
  balance and overdue over the current year's accounts with a balance above zero.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from app.core.errors import NotFound
from app.fees import _common as c
from app.fees import ledger
from app.fees import repository as repo
from app.fees.money import ZERO
from app.fees.schemas import DueLine
from app.students import service as students
from app.tenancy import service as tenancy

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext


@dataclass(frozen=True, slots=True)
class AskInstalment:
    label: str
    due_on: dt.date | None
    due: Decimal
    overdue: bool


@dataclass(frozen=True, slots=True)
class AskStudentFees:
    """What ``get_fee_dues`` may tell the model about ONE student from the fee ledger."""

    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None
    year_label: str | None
    has_account: bool
    balance: Decimal
    overdue: Decimal
    oldest_due_on: dt.date | None
    last_payment_on: dt.date | None
    instalments: tuple[AskInstalment, ...]
    as_of: dt.date


@dataclass(frozen=True, slots=True)
class AskClassFees:
    class_name: str
    students_owing: int
    balance: Decimal
    overdue: Decimal


@dataclass(frozen=True, slots=True)
class AskSchoolFees:
    """Numbers only (no names): the school view of ``get_fee_dues`` from the fee ledger."""

    year_label: str | None
    students_owing: int
    balance: Decimal
    overdue: Decimal
    classes: tuple[AskClassFees, ...]
    as_of: dt.date


def _guard(session: Session, ctx: UserContext) -> None:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ)


def student_fees(session: Session, ctx: UserContext, student_id: uuid.UUID) -> AskStudentFees:
    """ONE student's current-year fee position (404 outside the caller's student scope)."""
    _guard(session, ctx)
    summary = students.summaries(session, ctx, [student_id]).get(student_id)
    if summary is None:
        raise NotFound("Student not found")
    today = c.today(session)
    year = tenancy.get_current_academic_year(session)
    accounts = repo.accounts_for_year(session, year.id, [student_id]) if year else []
    instalments: list[AskInstalment] = []
    balance = overdue = ZERO
    oldest: dt.date | None = None
    last: dt.date | None = None
    if accounts:
        account = accounts[0]
        dues = ledger.dues_for(session, account, today)
        per: dict[int, list[DueLine]] = {}
        for _, line in dues.lines:
            if line.collectable:
                per.setdefault(line.instalment_no, []).append(line)
        for no in sorted(per):
            lines = per[no]
            due = sum((x.due for x in lines), ZERO)
            if due > 0:
                instalments.append(
                    AskInstalment(
                        label=lines[0].label,
                        due_on=lines[0].due_on,
                        due=due,
                        overdue=any(x.overdue for x in lines),
                    )
                )
        balance, overdue, oldest = dues.balance, dues.overdue, dues.oldest_due_on
        last = repo.last_payment_dates(session, [account.id]).get(account.id)
    return AskStudentFees(
        student_id=student_id,
        display_name=summary.display_name,
        admission_no=summary.admission_no,
        class_section=summary.class_section,
        year_label=year.label if year else None,
        has_account=bool(accounts),
        balance=balance,
        overdue=overdue,
        oldest_due_on=oldest,
        last_payment_on=last,
        instalments=tuple(instalments),
        as_of=today,
    )


def school_fees(session: Session, ctx: UserContext) -> AskSchoolFees:
    """School and per-class totals over the current year's accounts with a balance above zero."""
    _guard(session, ctx)
    today = c.today(session)
    year = tenancy.get_current_academic_year(session)
    if year is None:
        return AskSchoolFees(None, 0, ZERO, ZERO, (), today)
    accounts = repo.accounts_for_year(session, year.id)
    dues = ledger.dues_of(session, accounts, today)
    owing = [a for a in accounts if dues[a.id].balance > 0]
    sections = {s.id: s.class_id for s in tenancy.list_sections(session, academic_year_id=year.id)}
    classes = {k.id: k for k in tenancy.list_classes(session)}
    people = students.summaries(session, ctx, {a.student_id for a in owing})
    per: dict[uuid.UUID | None, list[uuid.UUID]] = {}
    for a in owing:
        person = people.get(a.student_id)
        section = person.section_id if person is not None else None
        per.setdefault(sections.get(section) if section else None, []).append(a.id)

    def order(class_id: uuid.UUID | None) -> tuple[int, str]:
        k = classes.get(class_id) if class_id else None
        return (k.sort_order, k.display_en) if k else (10**6, "")

    rows = []
    for class_id in sorted(per, key=order):
        k = classes.get(class_id) if class_id else None
        ids = per[class_id]
        rows.append(
            AskClassFees(
                class_name=k.display_en if k else "No current class",
                students_owing=len(ids),
                balance=sum((dues[i].balance for i in ids), ZERO),
                overdue=sum((dues[i].overdue for i in ids), ZERO),
            )
        )
    return AskSchoolFees(
        year_label=year.label,
        students_owing=len(owing),
        balance=sum((dues[a.id].balance for a in owing), ZERO),
        overdue=sum((dues[a.id].overdue for a in owing), ZERO),
        classes=tuple(rows),
        as_of=today,
    )


__all__ = [
    "AskClassFees",
    "AskInstalment",
    "AskSchoolFees",
    "AskStudentFees",
    "school_fees",
    "student_fees",
]
