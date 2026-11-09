"""School calendar: working days and holidays (FT-06; lead decision D85; FR-CAL-001..FR-CAL-020;
invariants 1, 2, 7, 13). Synthetic school from ``tests/insights/support.py``, built for this
module so the shared school keeps no calendar (its attendance tests mark any weekday).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from app.academics import service as academics
from app.academics.schemas import (
    AttendanceEntryIn,
    AttendanceWrite,
    CalendarEntryIn,
    CalendarPatternIn,
)
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed

pytestmark = pytest.mark.db


def _support() -> Any:
    import sys

    return sys.modules["sos_test_insights_support"]


S = _support()


@pytest.fixture(scope="module")
def own(world: Any, admin_engine: Engine) -> Any:
    return S.insights_school(admin_engine)


def _admin(school: Any) -> Any:
    return S.ctx(school, "office_admin", school.people["office_admin"])


def _pattern(school: Any, weekly: list[int], saturdays: list[int], **kw: Any) -> Any:
    actor = _admin(school)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        return academics.set_pattern(
            db,
            actor,
            school.ids["year"],
            CalendarPatternIn(weekly_off=weekly, saturdays_off=saturdays),
            **kw,
        )


def _add(school: Any, **body: Any) -> Any:
    actor = _admin(school)
    body.setdefault("name", "Synthetic holiday")
    with tenant_session(school.tenant_id, actor.user_id) as db:
        return academics.add_entry(db, actor, school.ids["year"], CalendarEntryIn(**body))


def _remove(school: Any, entry_id: uuid.UUID) -> None:
    actor = _admin(school)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        academics.remove_entry(db, actor, school.ids["year"], entry_id)


def _mark(school: Any, section: str, student: str, day: dt.date, status: str = "present") -> Any:
    actor = S.principal_ctx(school)
    body = AttendanceWrite(
        entries=[AttendanceEntryIn(student_id=school.ids[student], on_date=day, status=status)]
    )
    with tenant_session(school.tenant_id, actor.user_id) as db:
        return academics.record_attendance(db, actor, school.ids[section], body)


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


def _weekday(back: int) -> dt.date:
    """A Monday-Friday date ``back`` weekdays before today (inside the 2026-27 year)."""
    day: dt.date = S.school_days(back + 1)[0]
    return day


@pytest.fixture(scope="module")
def set_up(own: Any) -> Any:
    """Before set-up nothing is refused; then the pattern is saved (Sunday off only)."""
    sunday = _weekday(10)
    while sunday.weekday() != 6:
        sunday -= dt.timedelta(days=1)
    assert _mark(own, "section_9a", "a1", sunday).written == 1  # FR-CAL-007: old rule
    out = _pattern(own, [6], [], expected_version=0)
    return out, sunday


def test_FR_CAL_001_pattern_set_up_change_and_etag(
    own: Any, set_up: Any, admin_engine: Engine
) -> None:
    out, _ = set_up
    assert (out.set_up, out.weekly_off, out.saturdays_off, out.version) == (True, [6], [], 1)
    assert (out.working_days or 0) > 200
    with pytest.raises(PreconditionFailed):
        _pattern(own, [6], [2], expected_version=0)
    again = _pattern(own, [6], [], expected_version=1)
    assert again.version == 1  # unchanged pattern: nothing written
    events = _events(admin_engine, own.tenant_id, "calendar.set_up")
    assert [e["summary"]["weekly_off"] for e in events] == [[6]]


def test_FR_CAL_007_holiday_refuses_attendance_unless_a_working_day(own: Any, set_up: Any) -> None:
    _, sunday = set_up
    with pytest.raises(ValidationFailed) as err:
        _mark(own, "section_9a", "a1", sunday - dt.timedelta(days=7))
    assert [e["code"] for e in err.value.errors] == [academics.HOLIDAY_CODE]
    day = _weekday(6)
    entry = _add(own, kind="holiday", starts_on=day, ends_on=day)
    with pytest.raises(ValidationFailed) as err:
        _mark(own, "section_9a", "a2", day)
    assert [e["code"] for e in err.value.errors] == ["holiday"]
    # A class-IX extra working day inside the school holiday (an exam day) lets IX mark it.
    extra = _add(own, kind="working_day", starts_on=day, ends_on=day, class_id=own.ids["class_ix"])
    assert _mark(own, "section_9a", "a2", day).written == 1
    with pytest.raises(ValidationFailed):
        _mark(own, "section_10a", "x1", day)
    _remove(own, extra.id)
    _remove(own, entry.id)
    assert _mark(own, "section_10a", "x1", day).written == 1


def test_FR_CAL_008_holiday_over_marks_reports_them_and_audits(
    own: Any, set_up: Any, admin_engine: Engine
) -> None:
    day = _weekday(4)
    _mark(own, "section_9c", "c1", day)
    added = _add(own, kind="holiday", starts_on=day, ends_on=day, name="Synthetic rain holiday")
    assert added.attendance_marks >= 1
    events = _events(admin_engine, own.tenant_id, "calendar.entry_added")
    assert events[-1]["resource_id"] == own.ids["year"]
    assert events[-1]["summary"]["entry_id"] == str(added.id)
    assert "name" not in events[-1]["summary"]
    _remove(own, added.id)
    assert _events(admin_engine, own.tenant_id, "calendar.entry_removed")[-1]["summary"][
        "entry_id"
    ] == str(added.id)


def test_FR_CAL_006_entries_validate_and_never_overlap(own: Any, set_up: Any) -> None:
    first = dt.date(2026, 12, 21)
    entry = _add(own, kind="vacation", starts_on=first, ends_on=dt.date(2026, 12, 31))
    with pytest.raises(Conflict) as conflict:
        _add(own, kind="holiday", starts_on=dt.date(2026, 12, 25), ends_on=dt.date(2026, 12, 25))
    assert conflict.value.code == "calendar_overlap"
    with pytest.raises(ValidationFailed) as err:
        _add(own, kind="holiday", starts_on=dt.date(2027, 5, 1), ends_on=dt.date(2027, 5, 1))
    assert [e["code"] for e in err.value.errors] == ["date_out_of_range"]
    with pytest.raises(ValidationFailed) as err:
        _add(own, kind="holiday", starts_on=dt.date(2027, 1, 5), ends_on=dt.date(2027, 1, 4))
    assert [e["code"] for e in err.value.errors] == ["dates_reversed"]
    with pytest.raises(ValidationFailed) as err:
        _add(own, kind="vacation", starts_on=dt.date(2026, 6, 1), ends_on=dt.date(2026, 9, 30))
    assert [e["code"] for e in err.value.errors] == ["entry_too_long"]
    with pytest.raises(ValidationFailed) as err:
        _add(
            own,
            kind="holiday",
            starts_on=dt.date(2027, 1, 4),
            ends_on=dt.date(2027, 1, 4),
            class_id=uuid.uuid4(),
        )
    assert [e["code"] for e in err.value.errors] == ["unknown_class"]
    _remove(own, entry.id)
    with pytest.raises(NotFound):
        _remove(own, entry.id)


def test_FR_CAL_014_suggestions_are_adopted_once(own: Any, set_up: Any) -> None:
    actor = _admin(own)
    with tenant_session(own.tenant_id, actor.user_id) as db:
        before = academics.suggestions(db, actor, own.ids["year"])
    assert before.verified is False
    item = next(i for i in before.items if i.code == "republic_day_2027")
    body = {
        "kind": item.kind,
        "name": item.name,
        "starts_on": item.starts_on,
        "ends_on": item.ends_on,
        "suggestion": item.code,
    }
    added = _add(own, **body)
    with tenant_session(own.tenant_id, actor.user_id) as db:
        after = academics.suggestions(db, actor, own.ids["year"])
    assert item.code not in {i.code for i in after.items}
    with pytest.raises(Conflict) as conflict:
        _add(own, **body)
    assert conflict.value.code == "suggestion_already_added"
    with pytest.raises(ValidationFailed) as err:
        _add(own, **{**body, "suggestion": "no_such_suggestion"})
    assert [e["code"] for e in err.value.errors] == ["unknown_suggestion"]
    _remove(own, added.id)


def test_FR_CAL_009_month_view_and_day_view(own: Any, set_up: Any) -> None:
    actor = S.ct_ctx(own)
    with tenant_session(own.tenant_id, actor.user_id) as db:
        month = academics.calendar_month(db, actor, own.ids["year"], "2026-11")
        day = academics.attendance_day(db, actor, own.ids["section_9a"], dt.date(2026, 11, 1))
    assert month.set_up
    assert len(month.days) == 30
    assert month.working_days == 25  # 30 days, 5 Sundays (no Saturdays off in this pattern)
    assert day.calendar is not None
    assert day.calendar.kind == "weekly_off"


def test_FR_CAL_011_register_shows_working_days_and_not_marked(own: Any, set_up: Any) -> None:
    month = dt.date(2026, 7, 1)
    actor = S.principal_ctx(own)
    with tenant_session(own.tenant_id, actor.user_id) as db:
        out = academics.attendance_month(db, actor, own.ids["section_10a"], month.strftime("%Y-%m"))
    assert out.calendar_set
    assert len(out.working_days) == 27  # July 2026: 31 days, 4 Sundays
    x1 = next(s for s in out.students if s.student.student_id == own.ids["x1"])
    assert x1.counts["not_marked"] == 27 - len(x1.days)


def test_FR_CAL_012_attendance_summary_counts_working_days(own: Any, set_up: Any) -> None:
    actor = S.principal_ctx(own)
    with tenant_session(own.tenant_id, actor.user_id) as db:
        out = academics.attendance_summary(
            db, [own.ids["a1"], own.ids["c1"]], own.ids["year"], until=dt.date(2026, 6, 30)
        )
    a1 = out[own.ids["a1"]]
    assert a1.working_days == 26  # June 2026: 30 days, 4 Sundays
    assert a1.not_marked == 26 - (a1.present + a1.late + a1.leave + a1.absent)
    assert (a1.first_day, a1.last_day) == (dt.date(2026, 6, 1), dt.date(2026, 6, 30))
    with tenant_session(own.tenant_id, actor.user_id) as db:
        full = academics.attendance_summary(db, [own.ids["a1"]], own.ids["year"])
    # The Sunday marked before set-up is kept out of the counts and reported.
    assert full[own.ids["a1"]].marks_on_days_off >= 1


def test_FR_CAL_012_summary_without_a_calendar_counts_marks_only(own: Any) -> None:
    actor = S.principal_ctx(own)
    with tenant_session(own.tenant_id, actor.user_id) as db:
        out = academics.attendance_summary(db, [own.ids["a1"]], own.ids["old_year"])
    a1 = out[own.ids["a1"]]
    assert (a1.working_days, a1.not_marked, a1.marks_on_days_off) == (None, None, 0)


def test_invariant_1_calendar_tables_force_rls_and_app_cannot_update_entries(
    own: Any, set_up: Any, admin_engine: Engine, app_engine: Engine
) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname IN ('school_calendars', 'calendar_entries') ORDER BY relname"
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("calendar_entries", True, True),
        ("school_calendars", True, True),
    ]
    with pytest.raises(ProgrammingError), app_engine.begin() as c:
        c.execute(text("UPDATE sis.calendar_entries SET name = 'x' WHERE false"))


def test_invariant_1_another_schools_year_is_not_found(own: Any, world: Any) -> None:
    actor = _admin(own)
    with tenant_session(own.tenant_id, actor.user_id) as db, pytest.raises(NotFound):
        academics.get_calendar(db, actor, world.a.ids["year"])


# --- API: allowed 200, disallowed 403, other school 404 (CLAUDE.md §8) -------------------------


def test_FR_CAL_015_api_guards(own: Any, set_up: Any, api: Any, world: Any) -> None:
    year = own.ids["year"]
    path = f"/api/v1/academic-years/{year}/calendar"
    got = api.call(own.people["ct"], "GET", path)
    assert got.status_code == 200, got.text
    assert got.headers["ETag"] == 'W/"1"'
    month = api.call(own.people["ct"], "GET", f"{path}/month", params={"month": "2026-11"})
    assert month.status_code == 200, month.text
    denied = api.call(own.people["ct"], "PUT", path, json={"weekly_off": [6], "saturdays_off": []})
    assert denied.status_code == 403
    assert api.call(own.people["ct"], "GET", f"{path}/suggestions").status_code == 403
    body = {
        "kind": "holiday",
        "name": "Synthetic",
        "starts_on": "2027-02-01",
        "ends_on": "2027-02-01",
    }
    assert api.call(own.people["ct"], "POST", f"{path}/entries", json=body).status_code == 403
    stale = api.call(
        own.people["office_admin"],
        "PUT",
        path,
        json={"weekly_off": [6], "saturdays_off": [2]},
        headers={"If-Match": 'W/"9"'},
    )
    assert stale.status_code == 412
    created = api.call(own.people["office_admin"], "POST", f"{path}/entries", json=body)
    assert created.status_code == 201, created.text
    entry_id = created.json()["id"]
    other = f"/api/v1/academic-years/{world.a.ids['year']}/calendar"
    assert api.call(own.people["office_admin"], "GET", other).status_code == 404
    assert (
        api.call(own.people["office_admin"], "DELETE", f"{other}/entries/{entry_id}").status_code
        == 404
    )
    gone = api.call(own.people["office_admin"], "DELETE", f"{path}/entries/{entry_id}")
    assert gone.status_code == 204
