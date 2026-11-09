"""Tuition fee certificate configuration (``tax_certificate.yaml``; FM-06; FR-FEE-046..060;
invariant 13).

Pure: reads and validates the versioned file next to this module; no database. The legal wording
is marked ``verified: false`` until a chartered accountant has reviewed it.
"""

from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.fees.money import financial_year

CONFIG_PATH: Final = Path(__file__).with_name("tax_certificate.yaml")
NUMBER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,39}$")
HEAD_KINDS: Final = frozenset({"tuition", "admission", "exam", "books", "transport", "other"})


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Numbering(_Frozen):
    series: str = Field(pattern=r"^FTC$")  # receipt_counters_series_check (0083)
    format: str = Field(min_length=3, max_length=60)

    @model_validator(mode="after")
    def _formats(self) -> Numbering:
        sample = self.render("2026-27", 99999)
        if not NUMBER_RE.match(sample):
            raise ValueError(f"number format gives an invalid number: {sample!r}")
        return self

    def render(self, financial_year: str, number: int) -> str:
        return self.format.format(series=self.series, fy=financial_year, no=number)


class Counting(_Frozen):
    default_on_for_kinds: tuple[str, ...]

    @model_validator(mode="after")
    def _kinds(self) -> Counting:
        if set(self.default_on_for_kinds) - HEAD_KINDS:
            raise ValueError("unknown head kind")
        return self


class Limits(_Frozen):
    max_bulk_students: int = Field(ge=1, le=2000)
    max_receipts: int = Field(ge=12, le=500)
    max_years_back: int = Field(ge=1, le=20)


class PrintSpec(_Frozen):
    template_version: str = Field(pattern=r"^v[0-9]{1,3}$")
    title: str = Field(min_length=1, max_length=80)
    signatory_label: str = Field(min_length=1, max_length=60)


class ParentLink(_Frozen):
    visible_from_month: int = Field(ge=1, le=12)
    visible_from_day: int = Field(ge=1, le=28)
    visible_until_month: int = Field(ge=1, le=12)
    visible_until_day: int = Field(ge=1, le=30)


class Legal(_Frozen):
    verified: bool
    source: str = Field(min_length=1)
    section_reference: str = Field(min_length=1, max_length=300)
    certifies: str = Field(min_length=1, max_length=1000)
    disclaimer: str = Field(min_length=1, max_length=1000)


class TaxCertificateConfig(_Frozen):
    version: int = Field(ge=1)
    numbering: Numbering
    counting: Counting
    limits: Limits
    print: PrintSpec
    parent_link: ParentLink
    legal: Legal


@lru_cache(maxsize=1)
def load_config() -> TaxCertificateConfig:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return TaxCertificateConfig.model_validate(raw)


FY_RE: Final = re.compile(r"^(20[0-9]{2})-([0-9]{2})$")


def fy_bounds(financial_year: str, start_month: int) -> tuple[dt.date, dt.date] | None:
    """First and last day of ``YYYY-YY`` (``None`` when the label is malformed)."""
    m = FY_RE.match(financial_year)
    if m is None:
        return None
    first_year = int(m.group(1))
    if int(m.group(2)) != (first_year + 1) % 100:
        return None
    start = dt.date(first_year, start_month, 1)
    return start, dt.date(first_year + 1, start_month, 1) - dt.timedelta(days=1)


def _on_or_after(day: dt.date, month: int, dom: int) -> dt.date:
    """The first (month, day) on or after ``day``."""
    found = dt.date(day.year, month, dom)
    return found if found >= day else dt.date(day.year + 1, month, dom)


def parent_link_year(today: dt.date, start_month: int) -> str | None:
    """The financial year whose "Tax certificate" button the parent pages show on ``today``,
    or ``None``: the current year from its ``visible_from`` day (1 January, its last
    quarter), else the previous year until its ``visible_until`` day (30 September after it
    ended), else none."""
    spec = load_config().parent_link
    current = financial_year(today, start_month)
    bounds = fy_bounds(current, start_month)
    if bounds is None:  # pragma: no cover - financial_year() gives a valid label
        return None
    start, _ = bounds
    if today >= _on_or_after(start, spec.visible_from_month, spec.visible_from_day):
        return current
    previous_end = start - dt.timedelta(days=1)
    if today <= _on_or_after(previous_end, spec.visible_until_month, spec.visible_until_day):
        return financial_year(previous_end, start_month)
    return None


__all__ = [
    "CONFIG_PATH",
    "TaxCertificateConfig",
    "fy_bounds",
    "load_config",
    "parent_link_year",
]
