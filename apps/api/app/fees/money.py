"""Money and dates for fees (FR-FEE-028, FR-FEE-022; ADR-0042 P2).

Pure: no database, no web. Amounts are :class:`decimal.Decimal` rupees with exactly two places
(``numeric(14,2)`` in the database). Nothing here ever uses a float, and nothing converts to paise:
that happens only inside the payment provider package with its own exact check (FR-FEE-028).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final
from zoneinfo import ZoneInfo

IST: Final = ZoneInfo("Asia/Kolkata")
CENT: Final = Decimal("0.01")
ZERO: Final = Decimal("0.00")
HUNDRED: Final = Decimal("100")


class AmountError(ValueError):
    """Not an exact rupee amount with at most two decimals."""


def amount(value: object) -> Decimal:
    """An exact amount: a Decimal, int or numeric string with at most two decimals (never a float,
    never rounded: ``12.345`` is refused)."""
    if isinstance(value, bool | float):
        raise AmountError("amounts are exact decimals, never floats")
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise AmountError("not a number") from exc
    if not dec.is_finite():
        raise AmountError("not a finite number")
    quantized = dec.quantize(CENT)
    if quantized != dec:
        raise AmountError("at most two decimals")
    return quantized


def total(values: Iterable[Decimal]) -> Decimal:
    return sum(values, ZERO).quantize(CENT)


def percent_of(base: Decimal, percent: Decimal) -> Decimal:
    """``percent`` % of ``base``, rounded half up to the paisa (a concession line)."""
    return (base * percent / HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP)


def spread(amount_: Decimal, room: Sequence[Decimal]) -> list[Decimal]:
    """Spread ``amount_`` over buckets in order, never above each bucket's ``room`` (oldest first).
    Raises :class:`AmountError` when the room is not enough."""
    left = amount_
    out: list[Decimal] = []
    for space in room:
        take = min(left, max(space, ZERO))
        out.append(take)
        left -= take
    if left > ZERO:
        raise AmountError("more than the room available")
    return out


# --- display (receipts print rupees in Indian grouping) ------------------------------------------


def group_indian(value: Decimal) -> str:
    """``125000.5`` -> ``1,25,000.50`` (lakh grouping)."""
    sign = "-" if value < 0 else ""
    text = f"{abs(value).quantize(CENT):.2f}"
    whole, frac = text.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}{whole}.{frac}"


def inr(value: Decimal) -> str:
    return f"₹{group_indian(value)}"


_ONES: Final = (
    "",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
)
_TENS: Final = (
    "",
    "",
    "Twenty",
    "Thirty",
    "Forty",
    "Fifty",
    "Sixty",
    "Seventy",
    "Eighty",
    "Ninety",
)


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return f"{_TENS[tens]} {_ONES[ones]}".strip()


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        parts.append(_below_hundred(rest))
    return " ".join(parts)


def _words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts: list[str] = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    if crore:
        parts.append(f"{_words(crore)} Crore")
    if lakh:
        parts.append(f"{_below_hundred(lakh)} Lakh")
    if thousand:
        parts.append(f"{_below_hundred(thousand)} Thousand")
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def in_words(value: Decimal) -> str:
    """``1250.50`` -> ``Rupees One Thousand Two Hundred Fifty and Fifty Paise Only``."""
    quantized = abs(value).quantize(CENT)
    rupees = int(quantized)
    paise = int((quantized - rupees) * 100)
    text = f"Rupees {_words(rupees)}"
    if paise:
        text += f" and {_below_hundred(paise)} Paise"
    return text + " Only"


# --- dates ---------------------------------------------------------------------------------------


def ist_date(at: dt.datetime) -> dt.date:
    return at.astimezone(IST).date()


def financial_year(day: dt.date, start_month: int = 4) -> str:
    """``2026-10-17`` -> ``2026-27`` (India: April to March)."""
    first = day.year if day.month >= start_month else day.year - 1
    return f"{first}-{(first + 1) % 100:02d}"


__all__ = [
    "CENT",
    "IST",
    "ZERO",
    "AmountError",
    "amount",
    "financial_year",
    "group_indian",
    "in_words",
    "inr",
    "ist_date",
    "percent_of",
    "spread",
    "total",
]
