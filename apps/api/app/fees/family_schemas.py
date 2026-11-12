"""IO models of the family counter (FM-07; FR-FEE-101..112)."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import Field

from app.fees.arrears_schemas import YearDue
from app.fees.schemas import (
    AllocationIn,
    CounterMode,
    ModeDetailsIn,
    Money,
    PositiveMoney,
    ReceiptOut,
    _In,
    _Out,
)


class FamilyMember(_Out):
    """One child of the family at the counter: the chosen student first, then each sibling the
    clerk may see (a student sharing a guardian with the chosen one, FR-FEE-101). Display
    fields come from ``students.summaries`` under the clerk's own scope (inv. 3)."""

    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None
    status: str
    selected: bool = Field(description="True for the student the clerk searched for.")
    linked_by: list[str] = Field(
        description="How the shared guardian is related to this child (``father``, ``mother``, "
        "``guardian``...); empty for the chosen student."
    )
    accounts: list[YearDue] = Field(description="Open fee accounts, earlier years first.")
    collectable: Money


class FamilyCounterOut(_Out):
    """The family of one student at the counter (FR-FEE-101/102)."""

    student_id: uuid.UUID
    as_of: dt.date
    members: list[FamilyMember]
    total_collectable: Money
    max_accounts: int = Field(description="Accounts one family payment may cover.")
    modes: list[str] = Field(description="Modes a family payment may use (never UPI).")


class FamilyPaymentPart(_In):
    """The amount taken on one account. ``allocations`` default to oldest due first; when given
    they follow the rules of a counter receipt (FR-FEE-010)."""

    account_id: uuid.UUID
    amount: PositiveMoney
    allocations: list[AllocationIn] | None = Field(default=None, max_length=300)


class FamilyPaymentCreate(_In):
    """One payment by a family split across siblings' accounts (FR-FEE-103): one receipt per
    account, in one transaction, with the same mode details."""

    mode: CounterMode
    mode_details: ModeDetailsIn = Field(default_factory=ModeDetailsIn)
    parts: list[FamilyPaymentPart] = Field(min_length=2, max_length=50)


class FamilyPaymentOut(_Out):
    """The receipts of one family payment, in the order of the parts (consecutive numbers)."""

    receipts: list[ReceiptOut]
    amount: Money
    mode: str
