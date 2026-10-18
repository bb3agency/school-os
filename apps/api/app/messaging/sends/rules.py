"""Sending rules as pure functions (R2-3G; FR-MSG-011..016; ADR-0043 decision 10; owner
decision D25). No database or web: quiet hours, the daily cap, staleness, the cost estimate and
the parent-facing formats are tested as tables.

Times are the school's IST wall clock (``Asia/Kolkata``); stored datetimes are UTC-aware.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection
from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from zoneinfo import ZoneInfo

IST: Final = ZoneInfo("Asia/Kolkata")
_PAISE: Final = Decimal("0.01")


def ist_now(now: dt.datetime) -> dt.datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(IST)


def ist_today(now: dt.datetime) -> dt.date:
    return ist_now(now).date()


def ist_midnight(now: dt.datetime) -> dt.datetime:
    """The start of ``now``'s IST day (as an aware datetime)."""
    return dt.datetime.combine(ist_today(now), dt.time(0), tzinfo=IST)


def closes_at(
    purpose: str, quiet_start: dt.time, early_close: dt.time, early_purposes: Collection[str]
) -> dt.time:
    """When the day's sending window closes for ``purpose``: the school's quiet-hours start,
    or the earlier close for absence alerts and receipts (FR-MSG-011)."""
    if purpose in early_purposes and early_close < quiet_start:
        return early_close
    return quiet_start


def next_open(
    now: dt.datetime,
    *,
    purpose: str,
    quiet_start: dt.time,
    quiet_end: dt.time,
    early_close: dt.time,
    early_purposes: Collection[str],
) -> dt.datetime | None:
    """``None`` when ``purpose`` may be sent at ``now``; otherwise the IST moment the window
    opens again (quiet hours 21:00-07:00 by default, absence alerts and receipts until 20:00)."""
    local = ist_now(now)
    t = local.time()
    close = closes_at(purpose, quiet_start, early_close, early_purposes)
    today_open = dt.datetime.combine(local.date(), quiet_end, tzinfo=IST)
    if close > quiet_end:  # the usual case: quiet from the evening until the morning
        if t >= close:
            return today_open + dt.timedelta(days=1)
        if t < quiet_end:
            return today_open
        return None
    if close == quiet_end:  # no quiet hours
        return None
    # A quiet window inside one day (for example 01:00-06:00).
    if close <= t < quiet_end:
        return today_open
    return None


def next_morning(now: dt.datetime, quiet_end: dt.time) -> dt.datetime:
    """Tomorrow's first sending time (the daily cap is reached for today)."""
    return dt.datetime.combine(ist_today(now) + dt.timedelta(days=1), quiet_end, tzinfo=IST)


def cap_reached(sent_today: int, cap: int | None) -> bool:
    """The school's daily cap (owner decision D25: Meta's limit minus 10% unless set)."""
    return cap is not None and sent_today >= cap


def too_late(
    purpose: str, queued_at: dt.datetime, now: dt.datetime, *, max_queue_hours: int
) -> bool:
    """A queued message that would arrive too late is skipped (``too_late``): an absence alert
    after its own IST day, anything else after ``max_queue_hours``."""
    if purpose == "absence_alert":
        return ist_today(queued_at) != ist_today(now)
    return now - queued_at > dt.timedelta(hours=max_queue_hours)


def estimate_cost(count: int, rate_inr: Decimal, gst_percent: Decimal) -> Decimal:
    """Displayed Meta cost: messages x the configured utility rate x (1 + GST), in rupees with
    two decimals (FR-MSG-012; owner decision D27; the school pays Meta directly)."""
    if count <= 0:
        return Decimal("0.00")
    total = Decimal(count) * rate_inr * (Decimal(1) + gst_percent / Decimal(100))
    return total.quantize(_PAISE, rounding=ROUND_HALF_UP)


def job_due(now: dt.datetime, at: dt.time) -> bool:
    """Whether a daily job set for ``at`` (IST) may run at ``now`` (from that time on)."""
    return ist_now(now).time() >= at


def parent_date(day: dt.date) -> str:
    """Dates in parent messages: ``14/10/2026`` (Indian day/month/year)."""
    return day.strftime("%d/%m/%Y")


def parent_amount(amount: Decimal) -> str:
    """Rupee amounts in parent messages with Indian grouping: ``1,25,000.00`` (the template adds
    the ₹ sign)."""
    value = amount.quantize(_PAISE, rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    whole, _, fraction = f"{abs(value):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}{whole}.{fraction}"


__all__ = [
    "IST",
    "cap_reached",
    "closes_at",
    "estimate_cost",
    "ist_midnight",
    "ist_now",
    "ist_today",
    "job_due",
    "next_morning",
    "next_open",
    "parent_amount",
    "parent_date",
    "too_late",
]
