"""UPI and bank statement configuration (``upi.yaml``; R2-2E; ADR-0042 decision 9; FR-FEE-015..017;
invariant 13).

Pure: reads and validates the versioned file next to this module; no database. The CHECKs of
migration ``0060_fees_upi`` are the second line of defence (the VPA shape and the 1-30 day window
are pinned to them here).
"""

from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_PATH: Final = Path(__file__).with_name("upi.yaml")
VPA_SHAPE: Final = "^[A-Za-z0-9][A-Za-z0-9._-]{1,63}@[A-Za-z][A-Za-z0-9]{1,63}$"
PRESET_KEY_RE: Final = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def normalise_heading(text: str) -> str:
    """A column heading as presets name it: lower case, every run of characters other than
    letters and digits one space (``"Ref No./Cheque No."`` -> ``"ref no cheque no"``)."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text.casefold()).split())


class UpiSpec(_Frozen):
    vpa_pattern: str
    payee_name_min_length: int = Field(ge=2, le=2)
    payee_name_max_length: int = Field(ge=60, le=60)
    confirm_within_days_default: int = Field(ge=1, le=30)
    qr_error: Literal["l", "m", "q", "h"]

    @field_validator("vpa_pattern")
    @classmethod
    def _pinned(cls, v: str) -> str:
        if v != VPA_SHAPE:
            raise ValueError("the UPI ID pattern is pinned by upi_settings_vpa_shape")
        re.compile(v)
        return v


class StatementSpec(_Frozen):
    max_file_bytes: int = Field(ge=1024, le=10 * 1024 * 1024)
    max_lines: int = Field(ge=1, le=5000)
    max_scanned_rows: int = Field(ge=10, le=20000)
    xlsx_max_uncompressed_bytes: int = Field(ge=1024, le=200 * 1024 * 1024)
    xlsx_max_compression_ratio: int = Field(ge=10, le=1000)
    xlsx_max_members: int = Field(ge=5, le=1000)
    header_search_rows: int = Field(ge=1, le=200)
    max_days_back: int = Field(ge=1, le=1000)
    max_days_ahead: int = Field(ge=0, le=7)
    narration_keep_days: int = Field(ge=1, le=90)
    narration_max_length: int = Field(ge=20, le=1000)
    narration_mask_digits_from: int = Field(ge=6, le=12)

    @model_validator(mode="after")
    def _rows(self) -> StatementSpec:
        if self.max_scanned_rows < self.max_lines:
            raise ValueError("max_scanned_rows must cover max_lines")
        return self


class AmountDate(_Frozen):
    enabled: bool
    days_before: int = Field(ge=0, le=15)
    days_after: int = Field(ge=0, le=15)


class MatchingSpec(_Frozen):
    utr_exact: bool
    amount_date: AmountDate
    rematch_window_days: int = Field(ge=1, le=400)
    max_review_lines: int = Field(ge=1, le=5000)


class ExceptionsSpec(_Frozen):
    notify_hour_utc: int = Field(ge=0, le=23)
    notify_minute_utc: int = Field(ge=0, le=59)
    list_max: int = Field(ge=1, le=2000)


class Preset(_Frozen):
    """One bank's statement layout: which headings hold the date, narration, reference and
    credit (or amount plus a credit/debit marker), and the date formats it uses."""

    key: str
    version: int = Field(ge=1)
    name: str = Field(min_length=3, max_length=80)
    verified: bool
    date_columns: tuple[str, ...] = Field(min_length=1)
    narration_columns: tuple[str, ...] = Field(min_length=1)
    reference_columns: tuple[str, ...] = ()
    credit_columns: tuple[str, ...] = ()
    debit_columns: tuple[str, ...] = ()
    amount_columns: tuple[str, ...] = ()
    type_columns: tuple[str, ...] = ()
    credit_markers: tuple[str, ...] = ()
    date_formats: tuple[str, ...] = Field(min_length=1)

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        if not PRESET_KEY_RE.match(v):
            raise ValueError("preset keys match bank_statement_imports_preset_shape")
        return v

    @field_validator(
        "date_columns",
        "narration_columns",
        "reference_columns",
        "credit_columns",
        "debit_columns",
        "amount_columns",
        "type_columns",
    )
    @classmethod
    def _headings(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for heading in v:
            if normalise_heading(heading) != heading:
                raise ValueError(f"heading {heading!r} is not normalised")
        return v

    @field_validator("date_formats")
    @classmethod
    def _formats(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        sample = dt.date(2026, 10, 18)
        for fmt in v:
            if dt.datetime.strptime(sample.strftime(fmt), fmt).date() != sample:  # noqa: DTZ007
                raise ValueError(f"date format {fmt!r} does not round-trip")
        return v

    @model_validator(mode="after")
    def _amounts(self) -> Preset:
        split = bool(self.credit_columns)
        typed = bool(self.amount_columns and self.type_columns and self.credit_markers)
        if split == typed:
            raise ValueError("a preset has credit columns OR an amount with a credit marker")
        return self

    @property
    def typed_amount(self) -> bool:
        return not self.credit_columns


class UtrSpec(_Frozen):
    upi_marker: str
    narration_group: str
    reference: str
    other_reference: str

    @model_validator(mode="after")
    def _compile(self) -> UtrSpec:
        for pattern in (self.upi_marker, self.narration_group, self.reference):
            if re.compile(pattern).groups > 1:
                raise ValueError("UTR patterns capture at most one group")
        if re.compile(self.narration_group).groups != 1 or re.compile(self.reference).groups != 1:
            raise ValueError("UTR patterns capture the 12 digits as group 1")
        if self.other_reference != "^[A-Za-z0-9]{6,35}$":
            raise ValueError("other references are pinned by bank_statement_lines_ref_shape")
        return self


class UpiConfig(_Frozen):
    version: int = Field(ge=1)
    upi: UpiSpec
    statements: StatementSpec
    matching: MatchingSpec
    exceptions: ExceptionsSpec
    presets: tuple[Preset, ...] = Field(min_length=1)
    utr: UtrSpec

    @model_validator(mode="after")
    def _unique(self) -> UpiConfig:
        keys = [p.key for p in self.presets]
        if len(set(keys)) != len(keys):
            raise ValueError("preset keys are unique")
        if keys[0] != "generic":
            raise ValueError("the first preset is the generic one (the default)")
        return self

    def preset(self, key: str) -> Preset | None:
        return next((p for p in self.presets if p.key == key), None)


@lru_cache(maxsize=1)
def load_upi_config() -> UpiConfig:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return UpiConfig.model_validate(raw)


__all__ = ["CONFIG_PATH", "Preset", "UpiConfig", "load_upi_config", "normalise_heading"]
