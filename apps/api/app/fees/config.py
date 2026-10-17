"""Fee configuration (``config.yaml``; ADR-0042; FR-FEE-001, FR-FEE-010, FR-FEE-011, FR-FEE-014;
invariant 13).

Pure: reads and validates the versioned file next to this module; no database. The database
CHECKs of migration ``0058_fees_core`` are the second line of defence.
"""

from __future__ import annotations

import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_PATH: Final = Path(__file__).with_name("config.yaml")
NUMBER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,39}$")  # receipts_number_shape

PaperSize = Literal["A5", "A4"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SeriesSpec(_Frozen):
    series: str = Field(pattern=r"^(RCT|RFD)$")
    format: str = Field(min_length=3, max_length=60)

    @model_validator(mode="after")
    def _formats(self) -> SeriesSpec:
        sample = self.render("2026-27", 999999)
        if not NUMBER_RE.match(sample):
            raise ValueError(f"number format gives an invalid number: {sample!r}")
        return self

    def render(self, financial_year: str, number: int) -> str:
        return self.format.format(series=self.series, fy=financial_year, no=number)


class Numbering(_Frozen):
    receipt: SeriesSpec
    refund: SeriesSpec
    financial_year_start_month: int = Field(ge=1, le=12)

    @model_validator(mode="after")
    def _distinct(self) -> Numbering:
        if self.receipt.series != "RCT" or self.refund.series != "RFD":
            raise ValueError("receipts use series RCT and refunds RFD (fin.receipt_counters)")
        return self


class ReceiptSpec(_Frozen):
    default_paper_size: PaperSize
    default_gst_note: str = Field(min_length=1, max_length=200)
    template_version: str = Field(pattern=r"^v[0-9]{1,3}$")
    document_acl_roles: tuple[str, ...] = Field(min_length=1)
    download_url_ttl_s: int = Field(ge=30, le=300)

    @field_validator("document_acl_roles")
    @classmethod
    def _no_teachers(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if {"class_teacher", "teacher"} & set(v):
            raise ValueError("teachers never see fee data (FR-FEE-024)")
        return v


class Limits(_Frozen):
    max_amount: Decimal = Field(gt=0)
    max_heads: int = Field(ge=1, le=200)
    max_heads_per_plan: int = Field(ge=1, le=100)
    max_instalments: int = Field(ge=1, le=24)
    max_plan_students: int = Field(ge=1, le=20000)
    max_concession_targets: int = Field(ge=1, le=300)
    reason_min_length: int = Field(ge=10, le=10)
    reason_max_length: int = Field(ge=1000, le=1000)
    counter_matches: int = Field(ge=1, le=20)
    list_max: int = Field(ge=1, le=200)
    dues_max_rows: int = Field(ge=1, le=50000)

    @field_validator("max_amount")
    @classmethod
    def _two_places(cls, v: Decimal) -> Decimal:
        if v != v.quantize(Decimal("0.01")) or v >= Decimal("1000000000000"):
            raise ValueError("max_amount fits numeric(14,2)")
        return v


class Modes(_Frozen):
    utr_pattern: str
    bank_reference_pattern: str
    cheque_number_pattern: str
    bank_name_max_length: int = Field(ge=10, le=200)

    @model_validator(mode="after")
    def _compile(self) -> Modes:
        for pattern in (self.utr_pattern, self.bank_reference_pattern, self.cheque_number_pattern):
            re.compile(pattern)
        if self.utr_pattern != "^[0-9]{12}$":
            raise ValueError("the UTR pattern is pinned by receipts_utr_shape")
        return self


class LateFees(_Frozen):
    max_catch_up_days: int = Field(ge=1, le=1000)


class DayClose(_Frozen):
    max_days_back: int = Field(ge=0, le=366)


class Retention(_Frozen):
    financial_years_kept: int = Field(ge=8, le=20)


class FeesConfig(_Frozen):
    version: int = Field(ge=1)
    flag: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,63}$")
    numbering: Numbering
    receipt: ReceiptSpec
    limits: Limits
    modes: Modes
    late_fees: LateFees
    day_close: DayClose
    retention: Retention


@lru_cache(maxsize=1)
def load_config() -> FeesConfig:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return FeesConfig.model_validate(raw)


__all__ = ["CONFIG_PATH", "FeesConfig", "PaperSize", "SeriesSpec", "load_config"]
