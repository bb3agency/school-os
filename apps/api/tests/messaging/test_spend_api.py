"""Messages and estimated Meta cost over the API (FP-11; FR-MSG-032, FR-MSG-033; ADR-0043
amendment of 2026-11-09).

Synthetic data only: message rows of school A dated 11 IST months back (no other test sends
in that month), priced as Meta's status webhooks would have priced them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, text

from app.messaging import runtime
from app.messaging.spend import month_starts

pytestmark = pytest.mark.db
IST = ZoneInfo("Asia/Kolkata")
URL = "/api/v1/messaging/spend"


def _month() -> dt.date:
    return month_starts(dt.datetime.now(IST).date(), 12)[-1]


def _label(day: dt.date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


@pytest.fixture(scope="module")
def priced(world: Any, admin_engine: Engine) -> dt.date:
    """School A: 6 charged notices, 2 free, 1 not priced yet, 1 failed; 3 charged join
    confirmations; 1 STOP confirmation Meta reported free."""
    month = _month()
    sent_at = dt.datetime.combine(month + dt.timedelta(days=9), dt.time(10, 0), tzinfo=IST)
    source = uuid.uuid4()
    row = tuple[str, str, str | None, bool | None, str]
    notice: row = ("school_notice", "template", None, None, "sent")
    rows: list[row] = (
        [("school_notice", "template", "utility", True, "delivered")] * 6
        + [("school_notice", "template", "utility", False, "read")] * 2
        + [notice]
        + [("school_notice", "template", None, None, "failed")]
        + [("optin_confirm", "template", "utility", True, "delivered")] * 3
        + [("optin_stopped", "text", "service", False, "delivered")]
    )
    with admin_engine.begin() as c:
        c.execute(
            text("DELETE FROM msg.messages WHERE tenant_id = :t AND source_type = 'spend_test'"),
            {"t": world.a.tenant_id},
        )
        for n, (purpose, kind, category, billable, status) in enumerate(rows):
            template = purpose if kind == "template" else None
            c.execute(
                text(
                    "INSERT INTO msg.messages (id, tenant_id, purpose, kind, template_key, "
                    "template_version, language, source_type, source_id, dedupe_key, status, "
                    "sent_at, billable, pricing_category, failed_at) VALUES (:id, :t, :p, :k, "
                    ":tk, :tv, 'en', 'spend_test', :s, :d, :st, :at, :b, :c, "
                    "CASE WHEN :st = 'failed' THEN CAST(:at AS timestamptz) END)"
                ),
                {
                    "id": uuid.uuid4(),
                    "t": world.a.tenant_id,
                    "p": purpose,
                    "k": kind,
                    "tk": template,
                    "tv": 2 if template else None,
                    "s": source,
                    "d": f"spend{n}",
                    "st": status,
                    "at": sent_at,
                    "b": billable,
                    "c": category,
                },
            )
    return month


def _get(api: Any, who: Any, **params: Any) -> Any:
    return api.call(who, "GET", URL, params=params or None)


@pytest.mark.parametrize("role", ["owner", "principal", "office_admin", "accountant"])
def test_FR_MSG_032_school_wide_readers_see_the_monthly_estimate(
    world: Any, api: Any, priced: dt.date, role: str
) -> None:
    res = _get(api, world.person(role), months=12)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["months"]) == 12
    month = body["months"][-1]
    assert month["month"] == _label(priced)
    lines = {(x["pricing_category"], x["purpose"]): x for x in month["lines"]}
    notices = lines[("utility", "school_notice")]
    assert (notices["sent"], notices["billable"], notices["free"], notices["pending"]) == (
        10,
        6,
        3,
        1,
    )
    assert notices["rate"] == "utility_out_of_window"
    confirms = lines[("utility", "optin_confirm")]
    assert (confirms["billable"], confirms["rate"]) == (3, "utility_in_window")
    stopped = lines[("service", "optin_stopped")]
    assert (stopped["free"], stopped["estimated_cost_inr"]) == (1, "0.00")
    assert month["sent"] == 14
    rates = {r["rate"]: r for r in body["rates"]}
    gst = 1 + float(body["gst_percent"]) / 100
    expected = round(7 * float(rates["utility_out_of_window"]["inr"]) * gst, 2) + round(
        3 * float(rates["utility_in_window"]["inr"]) * gst, 2
    )
    assert float(month["estimated_cost_inr"]) == pytest.approx(expected)
    assert body["rates_checked_on"]
    assert body["next_rate_change"] > body["rates_checked_on"]


def test_FR_MSG_033_no_recipient_data_in_the_summary(world: Any, api: Any, priced: dt.date) -> None:
    raw = _get(api, world.person("owner"), months=12).text.lower()
    for word in ("phone", "guardian", "student", "last4", "provider_message_id"):
        assert word not in raw


def test_FR_MSG_032_default_window_and_bounds(world: Any, api: Any, priced: dt.date) -> None:
    body = _get(api, world.person("owner")).json()
    assert len(body["months"]) == 6
    assert _label(priced) not in {m["month"] for m in body["months"]}
    assert _get(api, world.person("owner"), months=0).status_code == 422
    assert _get(api, world.person("owner"), months=14).status_code == 422


@pytest.mark.parametrize("role", ["teacher", "class_teacher"])
def test_FR_MSG_032_without_school_wide_message_read_is_refused(
    world: Any, api: Any, role: str
) -> None:
    # The teacher has no message.read; the class teacher's grant is section-scoped.
    assert _get(api, world.person(role)).status_code == 403


def test_FR_MSG_032_another_school_sees_only_its_own_messages(
    world: Any, api: Any, priced: dt.date
) -> None:
    res = _get(api, world.b.people["owner"], months=12)
    assert res.status_code == 200, res.text
    month = res.json()["months"][-1]
    assert month["month"] == _label(priced)
    assert (month["sent"], month["lines"]) == (0, [])


def test_FR_MSG_024_spend_answers_module_unavailable_when_messaging_is_off(
    world: Any, api: Any
) -> None:
    saved = runtime.current_patch()
    runtime.patch_for_tests({**(saved or {}), "messaging_enabled": False})
    try:
        res = _get(api, world.person("owner"))
        assert res.status_code == 404
        assert res.json()["code"] == "module_unavailable"
    finally:
        runtime.patch_for_tests(saved)
