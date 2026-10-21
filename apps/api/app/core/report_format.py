"""Formatting helpers for data-principal access reports (R3-1A; FR-DPDP-009, PRV-031).

Every subject-report contributor (``app/core/subjects.py``) returns already-formatted English
strings. These helpers keep the formats the same across modules: dates DD/MM/YYYY in IST,
money ``₹1,234.00`` (Indian grouping), counts with thousands separators, and the truncation
note for sections longer than :data:`app.core.subjects.MAX_SECTION_ROWS`.

Pure: no database, no feature-module imports (``core`` is the bottom layer).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from app.core.subjects import MAX_SECTION_ROWS

IST: Final = ZoneInfo("Asia/Kolkata")
_CENT: Final = Decimal("0.01")
EMPTY: Final = "—"
AADHAAR_HELD: Final = "Held; shown at the office"


def fmt_date(value: dt.date | dt.datetime | None) -> str:
    """DD/MM/YYYY; a datetime is first converted to IST (naive datetimes are taken as UTC)."""
    if value is None:
        return EMPTY
    if isinstance(value, dt.datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
        return aware.astimezone(IST).strftime("%d/%m/%Y")
    return value.strftime("%d/%m/%Y")


def fmt_datetime(value: dt.datetime | None) -> str:
    """DD/MM/YYYY HH:MM in IST."""
    if value is None:
        return EMPTY
    aware = value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
    return aware.astimezone(IST).strftime("%d/%m/%Y %H:%M")


def fmt_money(value: Decimal | int | None) -> str:
    """``₹1,23,456.00`` (Indian grouping, two decimals)."""
    if value is None:
        return EMPTY
    dec = Decimal(value).quantize(_CENT)
    sign = "-" if dec < 0 else ""
    whole, frac = f"{abs(dec):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}₹{whole}.{frac}"


def fmt_count(value: int) -> str:
    return f"{value:,}"


def fmt_text(value: object) -> str:
    """A plain value as text; ``None`` or empty becomes an em dash."""
    if value is None:
        return EMPTY
    text = str(value).strip()
    return text or EMPTY


def fmt_bool(value: bool | None) -> str:
    if value is None:
        return EMPTY
    return "Yes" if value else "No"


def humanise(key: str | None) -> str:
    """``fee_receipt`` -> ``Fee receipt`` (sentence case for stored codes)."""
    if not key:
        return EMPTY
    text = key.replace("_", " ").replace(".", " ").strip()
    return text[:1].upper() + text[1:]


def cap_rows[T](rows: Sequence[T], total: int | None = None) -> tuple[list[T], str | None]:
    """Keep at most ``MAX_SECTION_ROWS`` rows (callers pass them newest first) and return the
    truncation note when rows were dropped. ``total`` is the full count when the caller fetched
    only ``MAX_SECTION_ROWS + 1`` rows or counted separately."""
    full = total if total is not None else len(rows)
    kept = list(rows[:MAX_SECTION_ROWS])
    if full > len(kept):
        return kept, f"Showing the latest {fmt_count(len(kept))} of {fmt_count(full)} entries."
    return kept, None


__all__ = [
    "AADHAAR_HELD",
    "EMPTY",
    "IST",
    "cap_rows",
    "fmt_bool",
    "fmt_count",
    "fmt_date",
    "fmt_datetime",
    "fmt_money",
    "fmt_text",
    "humanise",
]
