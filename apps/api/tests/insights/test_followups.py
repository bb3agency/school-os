"""FT-13 early-warning follow-ups (lead decision D86; FR-EW-019..FR-EW-030; invariant 14).

Planned reviews on flags (date checks, cleared by the next action and by closing), the daily
review reminder (once per planned date), the ``review_due`` filter and count, and the
school-wide counts for management (numbers only, never names).
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.errors import ValidationFailed
from app.insights import service as insights
from app.insights.schemas import ActionIn, CloseIn

pytestmark = pytest.mark.db

S = sys.modules["sos_test_insights_support"]


@pytest.fixture(scope="module")
def fs(world: Any, admin_engine: Engine) -> Any:
    """A school of its own, so counts and notifications start from zero."""
    return S.insights_school(admin_engine)


def _act(school: Any, flag_id: uuid.UUID, *, c9: bool = False, **kw: Any) -> Any:
    who = S.ct_ctx(school, "ct9c", "section_9c") if c9 else S.ct_ctx(school)
    with tenant_session(school.tenant_id, who.user_id) as db:
        return insights.add_action(db, who, flag_id, ActionIn(kind="called_parent", **kw))


def _summary_events(admin: Engine, tenant_id: uuid.UUID, flag_id: uuid.UUID) -> list[Any]:
    with admin.connect() as c:
        return [
            r.summary
            for r in c.execute(
                text(
                    "SELECT summary FROM audit.events WHERE tenant_id = :t AND resource_id = :f "
                    "AND action = 'insights.flag_action_added' ORDER BY seq"
                ),
                {"t": tenant_id, "f": flag_id},
            )
        ]


def test_FR_EW_019_an_action_plans_the_next_review(fs: Any, admin_engine: Engine) -> None:
    flag = S.manual_flag(fs, "a1")
    today = insights.today_ist()
    review = today + dt.timedelta(days=7)
    out = _act(fs, flag.id, review_on=review)
    assert out.review_on == review
    assert out.review_due is False
    assert out.actions[-1].review_on == review
    assert S.flags_of(admin_engine, fs.tenant_id, fs.ids["a1"])[-1]["review_on"] == review
    assert _summary_events(admin_engine, fs.tenant_id, flag.id)[-1]["review_on"] == (
        review.isoformat()
    )
    # The next action without a date means the review was held: nothing is planned now.
    again = _act(fs, flag.id)
    assert again.review_on is None
    assert [a.review_on for a in again.actions if a.kind == "called_parent"] == [review, None]


@pytest.mark.parametrize(
    ("days", "code"),
    [(0, "review_not_after_today"), (-3, "review_not_after_today"), (61, "review_too_far")],
)
def test_FR_EW_020_review_date_checks(fs: Any, days: int, code: str) -> None:
    flag = S.manual_flag(fs, "c1")
    with pytest.raises(ValidationFailed) as err:
        _act(fs, flag.id, c9=True, review_on=insights.today_ist() + dt.timedelta(days=days))
    assert {e["code"] for e in err.value.errors} == {code}
    who = S.principal_ctx(fs)
    with tenant_session(fs.tenant_id, who.user_id) as db:
        insights.close_flag(
            db,
            who,
            flag.id,
            CloseIn(reason="raised_in_error"),
            insights.get_flag(db, who, flag.id).version,
        )


def test_FR_EW_021_review_reminder_once_per_planned_date(fs: Any, admin_engine: Engine) -> None:
    flag = S.manual_flag(fs, "a2")
    review = insights.today_ist() + dt.timedelta(days=3)
    _act(fs, flag.id, review_on=review)
    with tenant_session(fs.tenant_id) as db:
        insights.send_reminders(db, today=review - dt.timedelta(days=1))
    assert S.notifications(admin_engine, fs.tenant_id, "insights.flag_review_due") == []
    for _ in range(2):
        with tenant_session(fs.tenant_id) as db:
            insights.send_reminders(db, today=review)
    sent = S.notifications(admin_engine, fs.tenant_id, "insights.flag_review_due")
    assert [n["recipient_membership_id"] for n in sent] == [fs.people["ct"].membership_id]
    assert sent[0]["params"] == {"flag_id": str(flag.id)}
    assert sent[0]["resource_id"] == flag.id


def test_FR_EW_022_closing_clears_the_review(fs: Any) -> None:
    flag = S.manual_flag(fs, "x1")
    who = S.principal_ctx(fs)
    with tenant_session(fs.tenant_id, who.user_id) as db:
        planned = insights.add_action(
            db,
            who,
            flag.id,
            ActionIn(kind="met_parent", review_on=insights.today_ist() + dt.timedelta(days=5)),
        )
    assert planned.review_on is not None
    with tenant_session(fs.tenant_id, who.user_id) as db:
        version = insights.get_flag(db, who, flag.id).version
        out = insights.close_flag(db, who, flag.id, CloseIn(reason="improved"), version)
    assert out.review_on is None
    assert out.status == "closed"


def test_FR_EW_023_review_due_filter_and_summary(fs: Any, api: Any, admin_engine: Engine) -> None:
    flag = S.manual_flag(fs, "c1")
    _act(fs, flag.id, c9=True, review_on=insights.today_ist() + dt.timedelta(days=2))
    with admin_engine.begin() as c:  # the planned day has come (two days on)
        c.execute(
            text(
                "UPDATE sis.insight_flags SET review_on = raised_on "
                "WHERE tenant_id = :t AND id = :f"
            ),
            {"t": fs.tenant_id, "f": flag.id},
        )
    principal = fs.people["principal"]
    got = api.call(
        principal, "GET", "/api/v1/insights/flags", params={"view": "all", "due": "review_due"}
    )
    assert got.status_code == 200, got.text
    rows = got.json()["data"]
    assert [r["id"] for r in rows] == [str(flag.id)]
    assert rows[0]["review_due"] is True
    summary = api.call(principal, "GET", "/api/v1/insights/summary").json()
    assert summary["review_due"] == 1


def test_FR_EW_026_management_sees_counts_only(fs: Any, api: Any) -> None:
    got = api.call(fs.people["owner"], "GET", "/api/v1/insights/counts")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["raised"] >= 5
    assert body["open"] >= 2
    assert body["review_due"] == 1
    assert body["closed"] >= 2
    assert body["outcomes"]["improved"] >= 1
    assert body["outcomes"]["raised_in_error"] >= 1
    assert set(body["outcomes"]) >= {"improved", "support_in_place", "student_left"}
    assert body["raised"] == body["actioned_on_time"] + body["actioned_late"] + body["not_actioned"]
    # Numbers only (PRV-004): no student, flag, member or section id, no name.
    for sid in fs.ids.values():
        assert str(sid) not in got.text
    assert "Synthetic" not in got.text
    future = api.call(
        fs.people["owner"],
        "GET",
        "/api/v1/insights/counts",
        params={"since": (insights.today_ist() + dt.timedelta(days=1)).isoformat()},
    )
    assert future.status_code == 422


@pytest.mark.parametrize("role", ["principal", "ct", "office_admin", "exam", "teacher"])
def test_FR_EW_027_counts_are_for_management_only(fs: Any, api: Any, role: str) -> None:
    assert api.call(fs.people[role], "GET", "/api/v1/insights/counts").status_code == 403


def test_FR_EW_028_owner_still_sees_no_flag(fs: Any, api: Any) -> None:
    """The counts permission opens no restricted insight (08 PRV-004, invariant 14)."""
    owner = fs.people["owner"]
    assert api.call(owner, "GET", "/api/v1/insights/flags").status_code == 403
    assert api.call(owner, "GET", "/api/v1/insights/summary").status_code == 403
