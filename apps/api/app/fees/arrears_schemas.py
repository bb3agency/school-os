"""IO models for earlier-year dues and the left-with-dues report (FM-01; FR-FEE-031..045)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from app.fees.schemas import Money, _Out


class YearDue(_Out):
    """One open fee account of a student at the counter, oldest year first (FR-FEE-036)."""

    account_id: uuid.UUID
    academic_year_id: uuid.UUID
    academic_year_label: str | None
    earlier_year: bool
    balance: Money
    overdue: Money
    collectable: Money
    oldest_due_on: dt.date | None


class SplitPart(_Out):
    """How much of the amount to take on one account (oldest year first, FR-FEE-037): the
    counter issues one receipt per part, each allocated oldest due first within its account."""

    account_id: uuid.UUID
    amount: Money


class StudentYearsOut(_Out):
    """The student's open fee accounts, earlier years first, and (when an amount was given) the
    oldest-first split across them."""

    student_id: uuid.UUID
    as_of: dt.date
    years: list[YearDue]
    earlier_years_due: Money
    total_collectable: Money
    split: list[SplitPart] | None


class LeftWithDuesRow(_Out):
    """A student who left (withdrawn, TC issued or graduated) with an open balance (FR-FEE-038).
    Never printed on a TC and never blocking one (FR-FEE-030, D16)."""

    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    status: Literal["left", "graduated"]
    accounts: int
    balance: Money
    oldest_due_on: dt.date | None
    last_payment_on: dt.date | None
    written_off: Money


class LeftWithDuesOut(_Out):
    as_of: dt.date
    rows: list[LeftWithDuesRow]
    students: int
    total_balance: Money
    written_off_total: Money
