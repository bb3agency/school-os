"""FT-14 records completeness (lead decision D86; FR-ATT-016..FR-ATT-025, FR-MRK-016..FR-MRK-025).

A fresh synthetic school (``support.insights_school``: a1, a2 in 9A whose class teacher is
recorded; c1 in 9C without one; x1 in 10A). Counts per section only, the class teacher named to
the principal and the exam coordinator, reminders off by default.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.academics import completeness
from app.academics.completeness_schemas import CompletenessReminderIn
from app.academics.config import CompletenessRules, load_config
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, ValidationFailed

pytestmark = pytest.mark.db

S = sys.modules["sos_test_insights_support"]


@pytest.fixture(scope="module")
def cs(world: Any, admin_engine: Engine) -> Any:
    """A school of its own, with today's attendance for a1 only and an exam with marks."""
    s = S.insights_school(admin_engine)
    today = S.academics.today_ist()
    S.record(s, "section_9a", {s.ids["a1"]: ["present"]}, [today])
    s.ids["exam"] = S.exam(s, "Unit test 1", today)
    S.marks(
        s,
        "section_9a",
        s.ids["exam"],
        {
            s.ids["a1"]: [("Maths", 30, 50), ("Science", None, 50)],
            s.ids["a2"]: [("maths", 41, 50)],
        },
    )
    return s


@pytest.fixture
def reminders_on(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_config().model_copy(
        update={"completeness": CompletenessRules(reminders_enabled=True)}
    )
    monkeypatch.setattr(completeness, "load_config", lambda: cfg)


def _row(body: dict[str, Any], section_id: uuid.UUID) -> dict[str, Any]:
    return next(r for r in body["rows"] if r["section_id"] == str(section_id))


def _events(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT resource_id, summary FROM audit.events "
                    "WHERE tenant_id = :t AND action = :a ORDER BY seq"
                ),
                {"t": tenant_id, "a": action},
            )
        ]


def test_FR_ATT_016_attendance_counts_per_section_with_the_class_teacher(cs: Any, api: Any) -> None:
    got = api.call(cs.people["principal"], "GET", "/api/v1/completeness/attendance")
    assert got.status_code == 200, got.text
    body = got.json()
    a = _row(body, cs.ids["section_9a"])
    assert (a["students"], a["marked"], a["status"]) == (2, 1, "partial")
    assert a["class_teacher"] == {
        "membership_id": str(cs.people["ct"].membership_id),
        "display_name": "Synthetic Teacher Anitha",
    }
    c = _row(body, cs.ids["section_9c"])
    assert (c["students"], c["marked"], c["status"], c["class_teacher"]) == (
        1,
        0,
        "not_taken",
        None,
    )
    assert body["partial"] >= 1
    assert body["not_taken"] >= 2
    assert body["sections"] == len(body["rows"])
    assert body["reminders_enabled"] is False
    # Counts only: no student ids, names or statuses anywhere in the answer.
    for sid in (cs.ids["a1"], cs.ids["a2"], cs.ids["c1"]):
        assert str(sid) not in got.text
    assert "Synthetica" not in got.text


def test_FR_ATT_017_complete_section_and_date_checks(cs: Any, api: Any) -> None:
    today = S.academics.today_ist()
    S.record(cs, "section_9c", {cs.ids["c1"]: ["absent"]}, [today])
    with tenant_session(cs.tenant_id, cs.people["principal"].user_id) as db:
        out = completeness.attendance_completeness(db, S.principal_ctx(cs))
    row = next(r for r in out.rows if r.section_id == cs.ids["section_9c"])
    assert (row.students, row.marked, row.status) == (1, 1, "complete")
    future = api.call(
        cs.people["principal"],
        "GET",
        "/api/v1/completeness/attendance",
        params={"date": (today + dt.timedelta(days=1)).isoformat()},
    )
    assert future.status_code == 422
    assert future.json()["errors"][0]["code"] == "future_date"
    old = api.call(
        cs.people["principal"],
        "GET",
        "/api/v1/completeness/attendance",
        params={"date": "2020-01-01"},
    )
    assert old.status_code == 422
    assert old.json()["errors"][0]["code"] == "date_out_of_range"


def test_FR_MRK_016_marks_grid_per_section_and_subject(cs: Any, api: Any) -> None:
    got = api.call(cs.people["exam"], "GET", f"/api/v1/completeness/exams/{cs.ids['exam']}")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["subjects"] == ["Maths", "Science"]  # "maths" is the same subject
    a = _row(body, cs.ids["section_9a"])
    assert a["students"] == 2
    assert a["status"] == "partial"
    assert a["subjects"] == [
        {"subject": "Maths", "entered": 2, "status": "complete"},
        {"subject": "Science", "entered": 1, "status": "partial"},  # absent counts as entered
    ]
    c = _row(body, cs.ids["section_9c"])
    assert c["status"] == "not_started"
    assert [cell["status"] for cell in c["subjects"]] == ["missing", "missing"]
    assert body["not_started"] >= 2
    assert body["locked"] is False
    assert str(cs.ids["a1"]) not in got.text


def test_FR_MRK_017_class_filter_and_unknown_class(cs: Any, api: Any) -> None:
    url = f"/api/v1/completeness/exams/{cs.ids['exam']}"
    one = api.call(cs.people["principal"], "GET", url, params={"class_id": str(cs.ids["class_x"])})
    assert one.status_code == 200, one.text
    assert one.json()["subjects"] == []  # nothing entered for class X yet
    assert all(r["class_id"] == str(cs.ids["class_x"]) for r in one.json()["rows"])
    bad = api.call(cs.people["principal"], "GET", url, params={"class_id": str(uuid.uuid4())})
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["code"] == "unknown_class"


@pytest.mark.parametrize("role", ["owner", "ct", "office_admin", "teacher"])
def test_FR_ATT_020_other_roles_are_refused(cs: Any, api: Any, role: str) -> None:
    for path in ("/api/v1/completeness/attendance", f"/api/v1/completeness/exams/{cs.ids['exam']}"):
        assert api.call(cs.people[role], "GET", path).status_code == 403, (role, path)


def test_FR_MRK_020_exam_of_another_school_is_404(cs: Any, api: Any, world: Any) -> None:
    other = world.a.people["principal"]
    got = api.call(other, "GET", f"/api/v1/completeness/exams/{cs.ids['exam']}")
    assert got.status_code == 404


def test_FR_ATT_022_reminders_are_off_by_default(cs: Any, api: Any) -> None:
    got = api.call(
        cs.people["principal"],
        "POST",
        "/api/v1/completeness/reminders",
        json={"section_id": str(cs.ids["section_9a"]), "kind": "attendance"},
    )
    assert got.status_code == 409
    assert got.json()["code"] == "reminders_off"
    refused = api.call(
        cs.people["exam"],
        "POST",
        "/api/v1/completeness/reminders",
        json={"section_id": str(cs.ids["section_9a"]), "kind": "attendance"},
    )
    assert refused.status_code == 403


def test_FR_ATT_023_reminder_once_per_day_audited(
    cs: Any, admin_engine: Engine, reminders_on: None
) -> None:
    who = S.principal_ctx(cs)
    body = CompletenessReminderIn(section_id=cs.ids["section_9a"], kind="attendance")
    with tenant_session(cs.tenant_id, who.user_id) as db:
        assert completeness.send_completeness_reminder(db, who, body).sent == 1
    with tenant_session(cs.tenant_id, who.user_id) as db:
        assert completeness.send_completeness_reminder(db, who, body).sent == 0
    sent = S.notifications(admin_engine, cs.tenant_id, "academics.completeness_reminder")
    assert [n["recipient_membership_id"] for n in sent] == [cs.people["ct"].membership_id]
    assert sent[0]["params"] == {"kind": "attendance", "section_id": str(cs.ids["section_9a"])}
    events = _events(admin_engine, cs.tenant_id, "completeness.reminder_sent")
    assert [e["summary"]["sent"] for e in events] == [1, 0]
    assert events[0]["resource_id"] == cs.ids["section_9a"]


def test_FR_MRK_022_marks_reminder_checks(cs: Any, reminders_on: None) -> None:
    who = S.principal_ctx(cs)
    with tenant_session(cs.tenant_id, who.user_id) as db:
        with pytest.raises(ValidationFailed) as err:
            completeness.send_completeness_reminder(
                db, who, CompletenessReminderIn(section_id=cs.ids["section_9a"], kind="marks")
            )
        assert err.value.errors[0]["code"] == "exam_required"
        with pytest.raises(Conflict) as no_teacher:
            completeness.send_completeness_reminder(
                db,
                who,
                CompletenessReminderIn(
                    section_id=cs.ids["section_9c"], kind="marks", exam_id=cs.ids["exam"]
                ),
            )
        assert no_teacher.value.code == "no_class_teacher"
        with pytest.raises(NotFound):
            completeness.send_completeness_reminder(
                db,
                who,
                CompletenessReminderIn(
                    section_id=uuid.uuid4(), kind="marks", exam_id=cs.ids["exam"]
                ),
            )
        out = completeness.send_completeness_reminder(
            db,
            who,
            CompletenessReminderIn(
                section_id=cs.ids["section_9a"], kind="marks", exam_id=cs.ids["exam"]
            ),
        )
    assert out.sent == 1
