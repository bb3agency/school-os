"""Pydantic v2 IO models for cheque status and the deposit slip (docs/09 "Fees"; FM-08;
FR-FEE-131..140). Inputs forbid unknown fields. Money is :class:`decimal.Decimal` rupees.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import Field

from app.fees.schemas import CancellationOut, Money, Note, _In, _Out

ChequeStatus = Literal["received", "deposited", "cleared", "returned"]


class ChequeOut(_Out):
    """A cheque receipt and where its cheque is: ``received`` (at the school), ``deposited``
    (taken to the bank), ``cleared`` (seen in a bank statement and confirmed by a person) or
    ``returned`` (the bank returned it; a ``cheque_dishonoured`` cancellation request exists)."""

    receipt_id: uuid.UUID
    receipt_number: str
    receipt_date: dt.date
    receipt_status: str
    amount: Money
    cheque_number: str
    bank_name: str
    cheque_date: dt.date | None
    student_name: str
    class_section: str
    cheque_status: ChequeStatus
    deposited_on: dt.date | None
    cleared_on: dt.date | None
    cleared_from_statement: bool
    returned_on: dt.date | None
    cancellation_id: uuid.UUID | None
    deposit_overdue: bool


class ChequeListOut(_Out):
    as_of: dt.date
    date_from: dt.date
    date_to: dt.date
    deposit_due_days: int
    items: list[ChequeOut]
    counts: dict[str, int]
    truncated: bool


class ChequeDepositIn(_In):
    """Mark cheques as taken to the bank (FR-FEE-132). ``deposited_on``: today or earlier, not
    before the receipt date."""

    receipt_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    deposited_on: dt.date


class ChequeSkipped(_Out):
    receipt_id: uuid.UUID
    code: str


class ChequeDepositOut(_Out):
    deposited: int
    already_deposited: int
    skipped: list[ChequeSkipped]


class ChequeReturnIn(_In):
    """The bank returned the cheque (FR-FEE-134): a ``cheque_dishonoured`` cancellation request is
    raised for the receipt (a different holder of ``fee.receipt.cancel`` decides)."""

    returned_on: dt.date
    reason: Note


class ChequeReturnOut(_Out):
    cheque: ChequeOut
    cancellation: CancellationOut


__all__ = [
    "ChequeDepositIn",
    "ChequeDepositOut",
    "ChequeListOut",
    "ChequeOut",
    "ChequeReturnIn",
    "ChequeReturnOut",
    "ChequeSkipped",
    "ChequeStatus",
]
