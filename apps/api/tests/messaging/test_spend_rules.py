"""Meta pricing configuration and the monthly spend estimate as tables (FP-11; FR-MSG-029..033;
ADR-0043 amendment of 2026-11-09). Pure: no database."""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from importlib import resources
from typing import Any

import pytest
from pydantic import ValidationError

from app.messaging import catalogue, tasks
from app.messaging.config import RATE_KEYS, CostConfig, load_config
from app.messaging.sends import repository as sends_repo
from app.messaging.spend import AUTO_REPLY, SpendRow, month_starts, rate_key, summarise

NOV = dt.date(2026, 11, 1)
OCT = dt.date(2026, 10, 1)


def _cost(**changes: Any) -> CostConfig:
    base = load_config().cost.model_dump()
    base.update(changes)
    return CostConfig.model_validate(base)


def _row(
    purpose: str = "school_notice",
    *,
    month: dt.date = NOV,
    kind: str = "template",
    category: str | None = "utility",
    billable: bool | None = True,
    failed: bool = False,
    count: int = 1,
) -> SpendRow:
    return SpendRow(month, kind, purpose, category, billable, failed, count)


# --- configuration (FR-MSG-029, FR-MSG-030, FR-MSG-031) ------------------------------------------


def test_FR_MSG_029_every_rate_is_configured_with_its_source_and_verification() -> None:
    cost = load_config().cost
    assert set(cost.rates) == set(RATE_KEYS)
    for rate in cost.rates.values():
        assert rate.inr > 0
        assert len(rate.source) >= 10
    # Secondary sources only (research notes): nothing claims to be checked against Meta yet.
    assert not any(r.verified for r in cost.rates.values())
    # Marketing is never sent (BR-18); kept only to price a message Meta re-categorised.
    assert cost.rates["marketing"].used is False
    assert all(cost.rates[k].used for k in RATE_KEYS if k != "marketing")
    # The previews keep using the business-initiated utility rate.
    assert cost.utility_rate_inr == cost.rates["utility_out_of_window"].inr
    assert cost.in_window_purposes == frozenset({"optin_confirm"})
    assert cost.service_free_per_month == 1000


def test_FR_MSG_029_a_missing_rate_or_a_bad_month_is_refused() -> None:
    rates = dict(load_config().cost.model_dump()["rates"])
    rates.pop("service_above_free_tier")
    with pytest.raises(ValidationError):
        _cost(rates=rates)
    with pytest.raises(ValidationError):
        _cost(rate_change_months=(1, 13))
    with pytest.raises(ValidationError):
        _cost(rate_change_months=(4, 4))


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        (dt.date(2026, 11, 9), dt.date(2027, 1, 1)),
        (dt.date(2026, 12, 31), dt.date(2027, 1, 1)),
        (dt.date(2027, 1, 1), dt.date(2027, 4, 1)),
        (dt.date(2027, 3, 31), dt.date(2027, 4, 1)),
        (dt.date(2027, 6, 15), dt.date(2027, 7, 1)),
        (dt.date(2027, 9, 30), dt.date(2027, 10, 1)),
    ],
)
def test_FR_MSG_031_next_rate_change_is_the_next_quarter_day(
    after: dt.date, expected: dt.date
) -> None:
    assert load_config().cost.next_rate_change(after) == expected


def test_FR_MSG_031_recheck_is_due_from_the_first_quarter_day_after_the_check() -> None:
    cost = _cost(rates_checked_on=dt.date(2026, 11, 9))
    assert cost.rates_recheck_due(dt.date(2026, 12, 31)) is False
    assert cost.rates_recheck_due(dt.date(2027, 1, 1)) is True
    assert cost.rates_recheck_due(dt.date(2027, 2, 1)) is True


def test_FR_MSG_031_the_quarterly_reminder_runs_on_the_quarter_days_and_warns(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = tasks.beat_schedule()["messaging-rates-recheck"]
    assert entry["task"] == "messaging.rates_recheck"
    schedule = entry["schedule"]
    assert schedule.month_of_year == {1, 4, 7, 10}
    assert schedule.day_of_month == {1}
    status = tasks.rates_recheck_status(dt.date(2027, 1, 1))
    assert status["due"] is True
    assert status["next_rate_change"] == "2027-01-01"
    assert status["unverified"] == sorted(RATE_KEYS)
    assert tasks.rates_recheck_status(dt.date(2026, 12, 1))["due"] is False
    seen: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(tasks.log, "warning", lambda event, **kw: seen.append((event, kw)))
    with caplog.at_level(logging.WARNING):
        tasks.rates_recheck.run()
    assert [e for e, _ in seen] == ["messaging.rates_recheck_due"]
    assert set(seen[0][1]) == {"due", "rates_checked_on", "next_rate_change", "unverified"}


def test_FR_MSG_029_no_text_says_in_window_messages_are_free() -> None:
    # The 1 Oct 2026 change: in-window utility templates and service messages above the free
    # tier are charged; the catalogue and the cap no longer describe them as free.
    shipped = resources.files("app.messaging").joinpath("templates.yaml").read_text("utf-8")
    assert "no charge" not in shipped
    assert "service_free_per_month" in shipped
    assert "are free" not in (sends_repo.sent_since.__doc__ or "")
    assert "1 Oct 2026" in (catalogue.__doc__ or "")


# --- the estimate (FR-MSG-032, FR-MSG-033) -------------------------------------------------------


def test_FR_MSG_032_month_starts_go_back_newest_first_across_the_year() -> None:
    assert month_starts(dt.date(2027, 2, 14), 4) == [
        dt.date(2027, 2, 1),
        dt.date(2027, 1, 1),
        dt.date(2026, 12, 1),
        dt.date(2026, 11, 1),
    ]
    with pytest.raises(ValueError, match="months"):
        month_starts(NOV, 0)
    with pytest.raises(ValueError, match="months"):
        month_starts(NOV, 14)


@pytest.mark.parametrize(
    ("category", "purpose", "expected"),
    [
        ("utility", "school_notice", "utility_out_of_window"),
        ("utility", "fee_reminder", "utility_out_of_window"),
        ("utility", "optin_confirm", "utility_in_window"),
        ("service", "optin_stopped", "service_above_free_tier"),
        ("service", AUTO_REPLY, "service_above_free_tier"),
        ("marketing", "school_notice", "marketing"),
        ("authentication", "school_notice", None),
    ],
)
def test_FR_MSG_030_rate_per_category_and_window(
    category: str, purpose: str, expected: str | None
) -> None:
    assert rate_key(category, purpose, load_config().cost) == expected


def test_FR_MSG_032_estimate_by_month_category_and_purpose() -> None:
    cost = _cost(
        rates={
            "utility_out_of_window": {"inr": "0.10", "verified": False, "source": "synthetic rate"},
            "utility_in_window": {"inr": "0.05", "verified": False, "source": "synthetic rate"},
            "service_above_free_tier": {
                "inr": "0.20",
                "verified": False,
                "source": "synthetic rate",
            },
            "marketing": {
                "inr": "1.00",
                "verified": False,
                "used": False,
                "source": "synthetic rate",
            },
        },
        gst_percent=Decimal(18),
        service_free_per_month=2,
    )
    rows = [
        _row("school_notice", count=100),  # charged
        _row("school_notice", billable=False, count=5),  # Meta: free
        _row("school_notice", category=None, billable=None, count=10),  # not priced yet
        _row("school_notice", category=None, billable=None, failed=True, count=3),  # failed
        _row("optin_confirm", count=4),  # in-window utility
        _row("optin_stopped", kind="text", category="service", billable=False, count=1),
        _row("fee_reminder", category="marketing", count=2),  # re-categorised by Meta
        _row("school_notice", category="authentication", count=7),  # no rate
        _row("school_notice", month=OCT, count=50),
        _row("school_notice", month=dt.date(2025, 1, 1), count=999),  # outside the range
    ]
    out = summarise(
        today=dt.date(2026, 11, 20),
        months=3,
        rows=rows,
        auto_replies={NOV: 4},
        cfg=cost,
        auto_reply_days=90,
    )
    assert [m.month for m in out.months] == ["2026-11", "2026-10", "2026-09"]
    nov, oct_, sep = out.months
    lines = {(x.pricing_category, x.purpose): x for x in nov.lines}
    notice = lines[("utility", "school_notice")]
    assert (notice.sent, notice.billable, notice.free, notice.pending) == (118, 100, 8, 10)
    assert notice.rate == "utility_out_of_window"
    assert notice.estimated_cost_inr == "12.98"  # 110 x 0.10 x 1.18
    assert lines[("utility", "optin_confirm")].estimated_cost_inr == "0.24"  # 4 x 0.05 x 1.18
    assert lines[("marketing", "fee_reminder")].estimated_cost_inr == "2.36"
    unpriced = lines[("authentication", "school_notice")]
    assert (unpriced.rate, unpriced.estimated_cost_inr, unpriced.billable) == (None, "0.00", 7)
    stopped = lines[("service", "optin_stopped")]
    assert (stopped.free, stopped.estimated_cost_inr) == (1, "0.00")
    # 1 stored service message + 4 automatic replies against a free tier of 2: 3 charged.
    replies = lines[("service", AUTO_REPLY)]
    assert (replies.sent, replies.billable, replies.free) == (4, 3, 1)
    assert replies.estimated_cost_inr == "0.71"  # 3 x 0.20 x 1.18
    assert nov.sent == 118 + 4 + 1 + 2 + 7 + 4
    assert nov.pending == 10
    assert nov.estimated_cost_inr == "16.29"  # 12.98 + 0.24 + 2.36 + 0.71
    assert (oct_.sent, oct_.estimated_cost_inr) == (50, "5.90")
    assert (sep.sent, sep.lines, sep.estimated_cost_inr) == (0, [], "0.00")
    assert out.gst_percent == "18"
    assert {r.rate for r in out.rates} == set(RATE_KEYS)
    assert out.service_free_per_month == 2
    assert out.auto_reply_days == 90


def test_FR_MSG_033_the_summary_carries_counts_and_rupees_only() -> None:
    out = summarise(
        today=dt.date(2026, 11, 20),
        months=1,
        rows=[_row(count=3)],
        auto_replies={},
        cfg=load_config().cost,
        auto_reply_days=90,
    )
    dumped = out.model_dump(mode="json")
    fields = set(dumped) | set(dumped["months"][0]) | set(dumped["months"][0]["lines"][0])
    assert not {f for f in fields if any(w in f for w in ("phone", "guardian", "name", "text"))}
    assert out.rates_checked_on == load_config().cost.rates_checked_on
    assert out.next_rate_change == dt.date(2027, 1, 1)
    assert out.rates_recheck_due is False
    later = summarise(
        today=dt.date(2027, 1, 2),
        months=1,
        rows=[],
        auto_replies={},
        cfg=load_config().cost,
        auto_reply_days=90,
    )
    assert later.rates_recheck_due is True
