"""School calendar rules, pure (FT-06; lead decision D85; FR-CAL-004, FR-CAL-006, FR-CAL-013,
FR-CAL-014; invariant 13). No database."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from app.academics import sheets
from app.academics.school_calendar import Calendar, load_calendar_config, overlapping
from app.core.spreadsheet import Cell

IX = uuid.uuid4()
X = uuid.uuid4()
ONE = uuid.UUID(int=1)


@dataclass(frozen=True)
class E:
    kind: str
    starts_on: dt.date
    ends_on: dt.date
    class_id: uuid.UUID | None = None
    name: str = "Synthetic holiday"
    id: uuid.UUID = ONE


def _cal(
    *entries: E, weekly: tuple[int, ...] = (6,), saturdays: tuple[int, ...] = (2,)
) -> Calendar:
    return Calendar.build(dt.date(2026, 6, 1), dt.date(2027, 3, 31), weekly, saturdays, entries)


def test_FR_CAL_004_pattern_sundays_and_second_saturdays_are_off() -> None:
    cal = _cal()
    # November 2026: Sun 1, Sat 7 (1st), Sat 14 (2nd), Sat 21 (3rd).
    assert cal.day(dt.date(2026, 11, 1), None).kind == "weekly_off"
    assert cal.day(dt.date(2026, 11, 7), None).working
    assert cal.day(dt.date(2026, 11, 14), None).kind == "saturday_off"
    assert cal.day(dt.date(2026, 11, 21), None).working
    assert cal.day(dt.date(2026, 11, 9), None).kind == "working"
    assert cal.day(dt.date(2027, 4, 1), None).kind == "outside_year"
    # 30 days, 5 Sundays, 1 second Saturday.
    assert len(cal.working_days(dt.date(2026, 11, 1), dt.date(2026, 11, 30), None)) == 24


def test_FR_CAL_004_entries_override_the_pattern_and_class_entries_win() -> None:
    cal = _cal(
        E("vacation", dt.date(2026, 10, 11), dt.date(2026, 10, 21), name="Dasara vacation"),
        E("working_day", dt.date(2026, 11, 1), dt.date(2026, 11, 1), name="Compensatory day"),
        E("working_day", dt.date(2026, 10, 15), dt.date(2026, 10, 15), class_id=X),
        E("holiday", dt.date(2026, 11, 10), dt.date(2026, 11, 10), class_id=IX),
    )
    vacation = cal.day(dt.date(2026, 10, 12), None)
    assert (vacation.working, vacation.kind, vacation.name) == (
        False,
        "vacation",
        "Dasara vacation",
    )
    assert cal.day(dt.date(2026, 11, 1), None).kind == "working_day"
    # Class X has a special class during the vacation; IX does not.
    assert cal.day(dt.date(2026, 10, 15), X).working
    assert not cal.day(dt.date(2026, 10, 15), IX).working
    # Class IX's exam-day holiday applies to IX only.
    assert not cal.day(dt.date(2026, 11, 10), IX).working
    assert cal.day(dt.date(2026, 11, 10), X).working
    assert cal.day(dt.date(2026, 11, 10), None).working


def test_FR_CAL_006_overlap_is_within_one_scope_only() -> None:
    school = E("holiday", dt.date(2026, 11, 10), dt.date(2026, 11, 12))
    own = E("holiday", dt.date(2026, 11, 12), dt.date(2026, 11, 12), class_id=IX)
    entries = [school, own]
    assert overlapping(entries, None, dt.date(2026, 11, 12), dt.date(2026, 11, 20)) == [school]
    assert overlapping(entries, IX, dt.date(2026, 11, 11), dt.date(2026, 11, 11)) == []
    assert overlapping(entries, X, dt.date(2026, 11, 12), dt.date(2026, 11, 12)) == []


def test_FR_CAL_013_suggestions_are_unverified_config_within_limits() -> None:
    cfg = load_calendar_config()
    assert cfg.suggestions.verified is False
    assert cfg.default_pattern.verified is False
    keys = [i.key for g in cfg.suggestions.sets for i in g.items]
    assert len(keys) == len(set(keys))
    for group in cfg.suggestions.sets:
        for item in group.items:
            assert item.starts_on <= item.ends_on
            assert (item.ends_on - item.starts_on).days < cfg.limits.max_entry_days
            assert len(item.name) <= cfg.limits.max_name_chars
    assert cfg.suggestion("republic_day_2027") is not None
    assert cfg.suggestion("not_a_key") is None


def test_FR_CAL_007_sheet_cells_on_a_day_off_are_issues_not_entries() -> None:
    sid = uuid.uuid4()
    roster = sheets.Roster.build([(sid, "SYN-1", "1")])
    rows = [
        (1, [Cell("Adm No"), Cell("2026-11-09"), Cell("2026-11-10")]),
        (2, [Cell("SYN-1"), Cell("P"), Cell("A")]),
    ]
    out = sheets.read_attendance(
        iter(rows),
        roster,
        first=dt.date(2026, 6, 1),
        last=dt.date(2027, 3, 31),
        today=dt.date(2026, 11, 20),
        closed={dt.date(2026, 11, 10)},
    )
    assert [(e.on_date, e.status) for e in out.entries] == [(dt.date(2026, 11, 9), "present")]
    assert [i.code for i in out.issues] == ["holiday"]
