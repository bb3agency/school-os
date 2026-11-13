"""IO models of the fee structure notice and the year-on-year change summary (FM-10;
FR-FEE-113..125). Plans, heads and amounts only: no student, account or payment data."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.fees.schemas import Money, _Out

NoticePlanStatus = Literal["draft", "active"]
HeadChangeKind = Literal["added", "removed", "changed", "unchanged"]


class FeeStructureNoticeHead(_Out):
    head_id: uuid.UUID
    code: str
    name: str
    annual_amount: Money


class FeeStructureNoticeInstalment(_Out):
    instalment_no: int
    label: str
    due_on: dt.date
    amount: Money


class FeeStructureNoticeClass(_Out):
    """One class's general fee plan (no category) for the year: the active plan, else the
    newest draft (marked DRAFT on the notice)."""

    class_id: uuid.UUID
    class_label: str
    plan_id: uuid.UUID
    plan_status: NoticePlanStatus
    heads: list[FeeStructureNoticeHead]
    annual_total: Money
    instalments: list[FeeStructureNoticeInstalment]
    late_fee_text: str


class FeeStructureNoticeOut(_Out):
    academic_year_id: uuid.UUID
    academic_year_label: str
    title: str
    disclaimer: str
    classes: list[FeeStructureNoticeClass]
    classes_without_plan: list[str] = Field(
        description="Labels of the school's classes with no general plan for the year."
    )


class FeeStructureHeadChange(_Out):
    """One head of one class against the previous year. ``percent`` is null when the head is
    new (nothing to compare with) or was not charged."""

    head_id: uuid.UUID
    code: str
    name: str
    change: HeadChangeKind
    previous_amount: Money | None
    current_amount: Money | None
    difference: Money
    percent: Decimal | None
    above_threshold: bool


class FeeStructureClassChange(_Out):
    class_id: uuid.UUID
    class_label: str
    plan_status: NoticePlanStatus | None = Field(
        description="Status of this year's plan; null when the class has no plan this year."
    )
    has_previous: bool
    previous_total: Money
    current_total: Money
    difference: Money
    percent: Decimal | None
    above_threshold: bool
    heads: list[FeeStructureHeadChange]


class FeeStructureChangesOut(_Out):
    """This year's general plans against the previous academic year's active plans
    (FR-FEE-118..122)."""

    academic_year_id: uuid.UUID
    academic_year_label: str
    previous_year_id: uuid.UUID | None
    previous_year_label: str | None
    warn_percent: Decimal
    warn_percent_verified: bool
    classes: list[FeeStructureClassChange]
    classes_above_threshold: int
