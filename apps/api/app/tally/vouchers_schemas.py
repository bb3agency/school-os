"""Pydantic IO for the Tally voucher export and the ledger-vs-Tally check (FM-03;
FR-TALLY-011..030; docs/09 Tally connector).

Ledger names typed by the accountant are NFC-normalised and trimmed and then REFUSED (not cleaned)
when they hold control or invisible format characters, an Aadhaar-like number, or more than
``vouchers.ledger_name_max_length`` characters: a name must match the Tally ledger exactly, so a
silent change would make Tally refuse the import. Money is ``Decimal`` rupees.
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.core.redaction import contains_full_aadhaar
from app.core.textnorm import nfc
from app.tally.config import rules

PartyMode = Literal["student", "receivable"]
ReceiptMode = Literal["cash", "cheque", "bank_transfer", "upi_qr", "razorpay"]
VoucherKind = Literal["receipt", "cancellation", "refund", "demand"]
CheckState = Literal["matches", "differs", "missing_in_tally", "not_in_schoolos"]

_INVISIBLE = frozenset(
    map(chr, (0x200B, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A), 0xFEFF))
)


def _ledger_name(value: str) -> str:
    text = nfc(value).strip()
    if not text:
        raise ValueError("Enter the ledger name exactly as it is in Tally.")
    if len(text) > rules().vouchers.ledger_name_max_length:
        raise ValueError("This ledger name is too long.")
    if any(c in _INVISIBLE or unicodedata.category(c) in {"Cc", "Cs", "Co"} for c in text):
        raise ValueError("Remove the hidden or control characters from this ledger name.")
    if contains_full_aadhaar(text):
        raise ValueError("Don't enter Aadhaar numbers in ledger names.")
    return text


LedgerName = Annotated[str, Field(min_length=1, max_length=255), AfterValidator(_ledger_name)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


# --- mapping (FR-TALLY-011..015) -----------------------------------------------------------------


class VoucherSettingsIn(_In):
    party_mode: PartyMode = Field(
        description="student: each student's linked Tally ledger (else the receivable ledger); "
        "receivable: one receivable ledger for every student"
    )
    receivable_ledger: LedgerName
    mode_ledgers: dict[ReceiptMode, LedgerName] = Field(
        default_factory=dict, max_length=10, description="Cash or bank ledger per receipt mode"
    )
    head_ledgers: dict[uuid.UUID, LedgerName] = Field(
        default_factory=dict,
        max_length=500,
        description="Income ledger per fee head (needed for fee demand)",
    )
    other_income_ledger: LedgerName | None = Field(
        default=None, description="Income ledger for late fees and adjustments without a head"
    )
    include_demand: bool = False
    company: LedgerName | None = Field(
        default=None, description="The Tally company the file is for (optional)"
    )


class HeadMappingOut(_Out):
    head_id: uuid.UUID
    code: str
    name: str
    active: bool
    ledger: str | None


class ModeMappingOut(_Out):
    mode: ReceiptMode
    ledger: str | None


class VoucherSettingsOut(_Out):
    configured: bool = Field(description="False until the mapping is saved once")
    party_mode: PartyMode
    receivable_ledger: str
    modes: list[ModeMappingOut]
    heads: list[HeadMappingOut]
    other_income_ledger: str | None
    include_demand: bool
    company: str | None
    links_available: bool = Field(
        description="Whether the Tally connector is on, so students' linked ledgers can be used"
    )
    format_verified: bool
    version: int
    updated_at: dt.datetime | None


# --- export (FR-TALLY-016..024) ------------------------------------------------------------------


class VoucherExportIn(_In):
    period_from: dt.date
    period_to: dt.date
    include_exported: bool = Field(
        default=False, description="Also include vouchers already in an earlier file (marked)"
    )


class MissingLedger(_Out):
    field: str = Field(description="e.g. mode_ledgers.cash, head_ledgers.<head id>")
    label: str


class KindCount(_Out):
    kind: VoucherKind
    count: int
    new: int


class VoucherPreviewOut(_Out):
    period_from: dt.date
    period_to: dt.date
    vouchers: int
    new_vouchers: int
    already_exported: int
    by_kind: list[KindCount]
    cash_in: Decimal
    cash_out: Decimal
    open_days: list[dt.date] = Field(description="Days with vouchers that are not closed yet")
    missing: list[MissingLedger]
    students_on_receivable: int = Field(
        description="Students posted to the receivable ledger (party mode student, no link)"
    )
    ready: bool


class VoucherExportOut(_Out):
    id: uuid.UUID
    period_from: dt.date
    period_to: dt.date
    include_exported: bool
    include_demand: bool
    party_mode: PartyMode
    vouchers: int
    new_vouchers: int
    cash_total: Decimal
    created_by_name: str | None
    created_at: dt.datetime


# --- ledger-vs-Tally check (FR-TALLY-025..030) ---------------------------------------------------


class CheckRow(_Out):
    ledger_name: str
    state: CheckState
    tally_balance: Decimal | None
    schoolos_balance: Decimal | None
    difference: Decimal
    students: int = Field(description="Students the ledger stands for")


class LedgerCheckOut(_Out):
    party_mode: PartyMode
    as_of: dt.date | None = Field(description="Tally's as-of date of the last snapshot")
    compared: int
    matching: int
    differing: int
    tally_total: Decimal
    schoolos_total: Decimal
    not_exported: int = Field(
        description="Vouchers dated up to the as-of date that are in no file yet (they explain "
        "differences until imported)"
    )
    rows: list[CheckRow] = Field(description="Ledgers that differ, largest difference first")


__all__ = [
    "CheckRow",
    "HeadMappingOut",
    "KindCount",
    "LedgerCheckOut",
    "LedgerName",
    "MissingLedger",
    "ModeMappingOut",
    "PartyMode",
    "VoucherExportIn",
    "VoucherExportOut",
    "VoucherPreviewOut",
    "VoucherSettingsIn",
    "VoucherSettingsOut",
]
