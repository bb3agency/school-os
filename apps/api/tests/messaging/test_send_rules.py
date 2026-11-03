"""Sending rules as tables (R2-3G; FR-MSG-011, FR-MSG-012, FR-MSG-016; owner decisions D25,
D27). Pure: no database."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.messaging.config import load_config
from app.messaging.sends import rules

IST = ZoneInfo("Asia/Kolkata")
DAY = dt.date(2026, 10, 19)
EARLY = frozenset({"absence_alert", "fee_receipt"})


def _at(h: int, m: int = 0, *, days: int = 0) -> dt.datetime:
    return dt.datetime.combine(DAY + dt.timedelta(days=days), dt.time(h, m), tzinfo=IST)


def _open(now: dt.datetime, purpose: str = "school_notice") -> dt.datetime | None:
    return rules.next_open(
        now,
        purpose=purpose,
        quiet_start=dt.time(21, 0),
        quiet_end=dt.time(7, 0),
        early_close=dt.time(20, 0),
        early_purposes=EARLY,
    )


@pytest.mark.parametrize(
    ("now", "purpose", "expected"),
    [
        (_at(12), "school_notice", None),
        (_at(7), "school_notice", None),
        (_at(6, 59), "school_notice", _at(7)),
        (_at(21), "school_notice", _at(7, days=1)),
        (_at(20, 30), "school_notice", None),
        (_at(20, 30), "absence_alert", _at(7, days=1)),
        (_at(20, 30), "fee_receipt", _at(7, days=1)),
        (_at(19, 59), "fee_receipt", None),
        (_at(2), "fee_reminder", _at(7)),
    ],
)
def test_FR_MSG_011_quiet_hours(now: dt.datetime, purpose: str, expected: object) -> None:
    assert _open(now.astimezone(dt.UTC), purpose) == expected


def test_FR_MSG_011_quiet_window_inside_one_day_and_none() -> None:
    def at(now: dt.datetime, start: int, end: int) -> dt.datetime | None:
        return rules.next_open(
            now,
            purpose="school_notice",
            quiet_start=dt.time(start),
            quiet_end=dt.time(end),
            early_close=dt.time(20),
            early_purposes=EARLY,
        )

    assert at(_at(3), 1, 6) == _at(6)
    assert at(_at(7), 1, 6) is None
    assert at(_at(23), 7, 7) is None


def test_FR_MSG_011_early_close_holds_when_quiet_hours_start_after_midnight_or_are_off() -> None:
    """Absence alerts and receipts close at 20:00 IST whatever the school's quiet hours: a
    school whose quiet hours start after midnight (00:30-07:00) or that has none (07:00-07:00)
    must not get receipts or absence alerts at 22:00 or 00:15."""

    def at(now: dt.datetime, start: dt.time, end: dt.time, purpose: str) -> dt.datetime | None:
        return rules.next_open(
            now.astimezone(dt.UTC),
            purpose=purpose,
            quiet_start=start,
            quiet_end=end,
            early_close=dt.time(20),
            early_purposes=EARLY,
        )

    late, seven = dt.time(0, 30), dt.time(7)
    for purpose in ("fee_receipt", "absence_alert"):
        assert at(_at(22), late, seven, purpose) == _at(7, days=1)
        assert at(_at(0, 15), late, seven, purpose) == _at(7)
        assert at(_at(3), late, seven, purpose) == _at(7)
        assert at(_at(12), late, seven, purpose) is None
        assert at(_at(22), seven, seven, purpose) == _at(0, days=1)
        assert at(_at(12), seven, seven, purpose) is None
    # The school's own quiet hours still govern the other purposes.
    assert at(_at(22), late, seven, "school_notice") is None
    assert at(_at(22), seven, seven, "school_notice") is None


def test_FR_MSG_011_cap_and_staleness() -> None:
    assert rules.cap_reached(225, 225)
    assert not rules.cap_reached(224, 225)
    assert not rules.cap_reached(10_000, None)
    assert rules.next_morning(_at(15), dt.time(7)) == _at(7, days=1)
    assert rules.too_late("absence_alert", _at(11), _at(7, 30, days=1), max_queue_hours=48)
    assert not rules.too_late("absence_alert", _at(11), _at(19), max_queue_hours=48)
    assert not rules.too_late("school_notice", _at(11), _at(10, days=2), max_queue_hours=48)
    assert rules.too_late("school_notice", _at(11), _at(12, days=2), max_queue_hours=48)


def test_FR_MSG_012_cost_estimate_from_config() -> None:
    cfg = load_config().cost
    # The PRD example: 412 messages, about 56 rupees incl. GST.
    assert rules.estimate_cost(412, cfg.utility_rate_inr, cfg.gst_percent) == Decimal("55.91")
    assert rules.estimate_cost(0, cfg.utility_rate_inr, cfg.gst_percent) == Decimal("0.00")


@pytest.mark.parametrize(
    ("amount", "text"),
    [
        ("0", "0.00"),
        ("999.5", "999.50"),
        ("2500", "2,500.00"),
        ("125000", "1,25,000.00"),
        ("12345678.9", "1,23,45,678.90"),
    ],
)
def test_FR_MSG_014_indian_amounts(amount: str, text: str) -> None:
    assert rules.parent_amount(Decimal(amount)) == text


def test_FR_MSG_016_backoff_within_an_hour() -> None:
    cfg = load_config().sending
    assert cfg.backoff(1) == dt.timedelta(minutes=5)
    assert cfg.backoff(2) == dt.timedelta(minutes=20)
    assert sum(cfg.retry_backoff_minutes) <= 60
    assert cfg.max_attempts == 3


def test_parent_date_and_naive_refused() -> None:
    assert rules.parent_date(DAY) == "19/10/2026"
    with pytest.raises(ValueError, match="aware"):
        rules.ist_now(dt.datetime(2026, 10, 19, 12, 0))  # noqa: DTZ001 - the point of the test
