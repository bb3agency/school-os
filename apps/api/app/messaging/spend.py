"""Messages and estimated Meta cost per month (FP-11; FR-MSG-029..033; ADR-0043 amendment of
2026-11-09; owner decisions D7, D27).

Pure module (no database or web): the repository hands over counts grouped by IST month, kind,
purpose, Meta's pricing category and billable flag (stored from status webhooks, FR-MSG-016) and
the automatic replies per month; this module prices them with the configured rates.

- A message Meta reported ``billable`` is charged at the rate of its category: utility at the
  in-window rate for purposes sent only as a reply to the parent (``in_window_purposes``) and the
  out-of-window rate otherwise; service above the free tier; marketing (never sent; kept so a
  re-categorised message is still estimated). Other categories have no rate (counted, ₹0).
- A message Meta has not priced yet (sent, no pricing) is counted as ``pending`` and estimated as
  charged; a failed one or one Meta reported free costs nothing.
- Automatic replies are not stored as messages (FR-MSG-018); they are service messages, charged
  only for the part of the month's service messages above ``service_free_per_month``.

Counts and rupees only: no recipient, phone number or text reaches this module.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from app.messaging.config import CostConfig, RateKey
from app.messaging.schemas import (
    MessagingSpendOut,
    SpendLineOut,
    SpendMonthOut,
    SpendRateOut,
)

_PAISE: Final = Decimal("0.01")
AUTO_REPLY: Final = "auto_reply"
MAX_MONTHS: Final = 13


@dataclass(frozen=True)
class SpendRow:
    """One group of stored messages (``month`` is the first day of the IST month)."""

    month: dt.date
    kind: str
    purpose: str
    pricing_category: str | None
    billable: bool | None
    failed: bool
    count: int


@dataclass
class _Line:
    category: str
    rate_key: RateKey | None
    purpose: str
    sent: int = 0
    billable: int = 0
    free: int = 0
    pending: int = 0


def month_starts(today: dt.date, months: int) -> list[dt.date]:
    """The first days of the last ``months`` IST months, newest first (``today``'s month too)."""
    if not 1 <= months <= MAX_MONTHS:
        raise ValueError(f"months must be 1..{MAX_MONTHS}")
    year, month = today.year, today.month
    out: list[dt.date] = []
    for _ in range(months):
        out.append(dt.date(year, month, 1))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return out


def rate_key(category: str, purpose: str, cfg: CostConfig) -> RateKey | None:
    if category == "utility":
        return "utility_in_window" if purpose in cfg.in_window_purposes else "utility_out_of_window"
    if category == "service":
        return "service_above_free_tier"
    if category == "marketing":
        return "marketing"
    return None


def _category(row: SpendRow) -> str:
    if row.pricing_category:
        return row.pricing_category
    return "utility" if row.kind == "template" else "service"


def _rupees(count: int, key: RateKey | None, cfg: CostConfig) -> Decimal:
    if count <= 0 or key is None:
        return Decimal("0")
    return Decimal(count) * cfg.rates[key].inr * (Decimal(1) + cfg.gst_percent / Decimal(100))


def _fmt(value: Decimal) -> str:
    return str(value.quantize(_PAISE, rounding=ROUND_HALF_UP))


def _month(
    start: dt.date, rows: Iterable[SpendRow], auto_replies: int, cfg: CostConfig
) -> SpendMonthOut:
    lines: dict[tuple[str, str], _Line] = {}
    for row in rows:
        category = _category(row)
        line = lines.setdefault(
            (category, row.purpose),
            _Line(category, rate_key(category, row.purpose, cfg), row.purpose),
        )
        line.sent += row.count
        if row.billable is True:
            line.billable += row.count
        elif row.billable is False or row.failed:
            line.free += row.count
        else:
            line.pending += row.count
    if auto_replies > 0:
        # Free up to the monthly free tier, counting the stored service messages first.
        service_sent = sum(v.sent for v in lines.values() if v.category == "service")
        free_left = max(0, cfg.service_free_per_month - service_sent)
        charged = max(0, auto_replies - free_left)
        lines[("service", AUTO_REPLY)] = _Line(
            "service",
            "service_above_free_tier",
            AUTO_REPLY,
            sent=auto_replies,
            billable=charged,
            free=auto_replies - charged,
        )
    out_lines: list[SpendLineOut] = []
    total = Decimal("0")
    for (category, purpose), line in sorted(lines.items()):
        # Lines are rounded to paise first, so the month adds up to what the card shows.
        cost = _rupees(line.billable + line.pending, line.rate_key, cfg).quantize(
            _PAISE, rounding=ROUND_HALF_UP
        )
        total += cost
        out_lines.append(
            SpendLineOut(
                pricing_category=category,
                rate_key=line.rate_key,
                purpose=purpose,
                sent=line.sent,
                billable=line.billable,
                free=line.free,
                pending=line.pending,
                estimated_cost_inr=_fmt(cost),
            )
        )
    return SpendMonthOut(
        month=f"{start.year:04d}-{start.month:02d}",
        sent=sum(v.sent for v in out_lines),
        billable=sum(v.billable for v in out_lines),
        pending=sum(v.pending for v in out_lines),
        estimated_cost_inr=_fmt(total),
        lines=out_lines,
    )


def summarise(
    *,
    today: dt.date,
    months: int,
    rows: Iterable[SpendRow],
    auto_replies: Mapping[dt.date, int],
    cfg: CostConfig,
    auto_reply_days: int,
) -> MessagingSpendOut:
    """The spend view for the last ``months`` IST months up to ``today`` (FR-MSG-032)."""
    starts = month_starts(today, months)
    by_month: dict[dt.date, list[SpendRow]] = {start: [] for start in starts}
    for row in rows:
        if row.month in by_month:
            by_month[row.month].append(row)
    return MessagingSpendOut(
        months=[_month(s, by_month[s], auto_replies.get(s, 0), cfg) for s in starts],
        gst_percent=str(cfg.gst_percent),
        rates=[
            SpendRateOut(key=key, inr=str(rate.inr), verified=rate.verified, used=rate.used)
            for key, rate in cfg.rates.items()
        ],
        rates_checked_on=cfg.rates_checked_on,
        next_rate_change=cfg.next_rate_change(cfg.rates_checked_on),
        rates_recheck_due=cfg.rates_recheck_due(today),
        service_free_per_month=cfg.service_free_per_month,
        auto_reply_days=auto_reply_days,
    )


__all__ = ["AUTO_REPLY", "MAX_MONTHS", "SpendRow", "month_starts", "rate_key", "summarise"]
