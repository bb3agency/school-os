"""Cheque and bank transfer matching configuration and the pure extraction of a credit's cheque
number and transfer reference (``bank_matching.yaml``; FM-08; FR-FEE-126, FR-FEE-127;
invariant 13).

Pure: no database, no logging. :func:`bank_facts` runs inside the statement parse on the raw
narration (before masking), so a reference the masking would hide is still read; what it returns
is only a 6-digit cheque number or a transfer reference shaped like the bank's, never an account
number (all-digit tokens other than 12 digits are refused) and never the narration itself.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_PATH: Final = Path(__file__).with_name("bank_matching.yaml")
_SPLIT: Final = re.compile(r"[/\-\s:|]+")
_TOKEN_SHAPE: Final = re.compile(r"^[A-Za-z0-9]+$")
_IMPS_DIGITS: Final = 12


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ChequeSpec(_Frozen):
    verified: bool
    source: str = Field(min_length=10)
    reference: str
    narration_marker: str
    narration_group: str
    credit_days_before_receipt: int = Field(ge=0, le=7)
    credit_days_after_receipt: int = Field(ge=0, le=60)

    @model_validator(mode="after")
    def _compile(self) -> ChequeSpec:
        if re.compile(self.reference).groups != 1 or re.compile(self.narration_group).groups != 1:
            raise ValueError("cheque patterns capture the 6 digits as group 1")
        re.compile(self.narration_marker)
        return self


class TransferSpec(_Frozen):
    verified: bool
    source: str = Field(min_length=10)
    narration_marker: str
    token_min_length: int = Field(ge=6, le=35)
    token_max_length: int = Field(ge=6, le=35)
    credit_days_before_receipt: int = Field(ge=0, le=30)
    credit_days_after_receipt: int = Field(ge=0, le=30)

    @model_validator(mode="after")
    def _lengths(self) -> TransferSpec:
        re.compile(self.narration_marker)
        if self.token_min_length > self.token_max_length:
            raise ValueError("token_min_length <= token_max_length")
        return self


class BankMatchingRules(_Frozen):
    cheque_exact: bool
    transfer_exact: bool


class ChequesListSpec(_Frozen):
    max_rows: int = Field(ge=10, le=2000)
    deposit_batch_max: int = Field(ge=1, le=500)
    deposit_due_days: int = Field(ge=0, le=30)


class BankMatchingConfig(_Frozen):
    version: int = Field(ge=1)
    cheques: ChequeSpec
    transfers: TransferSpec
    matching: BankMatchingRules
    cheques_list: ChequesListSpec


@lru_cache(maxsize=1)
def load_bank_matching_config() -> BankMatchingConfig:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return BankMatchingConfig.model_validate(raw)


def cheque_number(
    narration: str | None, reference: str | None, cfg: BankMatchingConfig
) -> str | None:
    """The 6-digit cheque number of a (non-UPI) credit: the reference column when it holds 6
    digits (zero-padded allowed) and the credit looks like a cheque or the column is exactly 6
    digits; else the number after a cheque marker in the narration."""
    spec = cfg.cheques
    text = narration or ""
    marked = bool(re.search(spec.narration_marker, text))
    value = (reference or "").strip()
    if value:
        found = re.match(spec.reference, value)
        if found and (marked or len(value) == len(found.group(1))):
            return found.group(1)
    if marked:
        found = re.search(spec.narration_group, text)
        if found:
            return found.group(1)
    return None


def transfer_reference(narration: str | None, cfg: BankMatchingConfig) -> str | None:
    """The NEFT/RTGS/IMPS reference of a credit's narration (upper case), or ``None``."""
    spec = cfg.transfers
    text = narration or ""
    marker = re.search(spec.narration_marker, text)
    if marker is None:
        return None
    for token in _SPLIT.split(text[marker.end() :]):
        if not (spec.token_min_length <= len(token) <= spec.token_max_length):
            continue
        if not _TOKEN_SHAPE.match(token):
            continue
        has_letter = any(ch.isalpha() for ch in token)
        digits = sum(ch.isdigit() for ch in token)
        if (has_letter and digits >= 6) or (token.isdigit() and len(token) == _IMPS_DIGITS):
            return token.upper()
    return None


def bank_facts(
    narration: str | None, reference: str | None, *, is_upi: bool, cfg: BankMatchingConfig
) -> tuple[str | None, str | None]:
    """``(cheque_no, transfer_ref)`` of one credit (``None, None`` for a UPI credit)."""
    if is_upi:
        return None, None
    transfer = transfer_reference(narration, cfg)
    cheque = None if transfer else cheque_number(narration, reference, cfg)
    return cheque, transfer


__all__ = [
    "BankMatchingConfig",
    "bank_facts",
    "cheque_number",
    "load_bank_matching_config",
    "transfer_reference",
]
