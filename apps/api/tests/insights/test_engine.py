"""Early-warning rules and ABC indicators (FR-EW-001..003; FR-MRK-005; 08 §4 PRV-005). Pure: the
engine sees only dates, statuses, percentages and counts; it never sees names or note text.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from importlib import resources

import pytest
import yaml

from app.insights import engine
from app.insights.config import RULE_KEYS, InsightsConfig, effective, load_config

TODAY = dt.date(2026, 9, 29)


def _marks(*statuses: str, end: dt.date = TODAY) -> list[engine.Mark]:
    """One mark per school day ending at ``end`` (weekends skipped), oldest first."""
    out: list[engine.Mark] = []
    day = end
    for status in reversed(statuses):
        while day.weekday() >= 5:
            day -= dt.timedelta(days=1)
        out.append(engine.Mark(day, status))
        day -= dt.timedelta(days=1)
    return list(reversed(out))


def _facts(
    marks: Sequence[engine.Mark] = (),
    results: Sequence[engine.Result] = (),
    concerns: Sequence[dt.date] = (),
) -> engine.Facts:
    return engine.Facts(marks=tuple(marks), results=tuple(results), concern_dates=tuple(concerns))


def _rules(
    facts: engine.Facts, overrides: dict[str, object] | None = None
) -> dict[str, engine.Finding]:
    found = engine.findings(facts, effective(overrides or {}), TODAY)
    return {f.rule: f for f in found}


def test_FR_EW_002_three_consecutive_absences_raise_the_AP_follow_up() -> None:
    marks = _marks("present", "absent", "absent", "absent")
    found = _rules(_facts(marks))
    streak = found["attendance_streak"]
    assert streak.indicator == "attendance"
    assert streak.evidence["days"] == 3
    assert streak.evidence["from"] == marks[1].on_date.isoformat()
    assert streak.evidence["to"] == marks[-1].on_date.isoformat()
    assert streak.basis == f"run:{marks[1].on_date.isoformat()}"


@pytest.mark.parametrize(
    "statuses",
    [
        ("absent", "absent"),
        ("absent", "absent", "present"),
        ("absent", "leave", "absent", "absent"),
        ("absent", "absent", "late"),
    ],
)
def test_FR_EW_002_leave_late_and_present_end_the_run(statuses: tuple[str, ...]) -> None:
    assert "attendance_streak" not in _rules(_facts(_marks(*statuses)))


def test_FR_EW_003_the_same_run_keeps_its_basis_as_it_grows() -> None:
    """Day 3 of a run raises the flag; day 4 of the SAME run has the same basis (the first
    absence), so the unique basis key stops a second flag (FR-EW-003)."""
    four = _marks("present", "absent", "absent", "absent", "absent")
    day3 = _rules(_facts(four[:-1]))
    day4 = _rules(_facts(four))
    assert day3["attendance_streak"].basis == day4["attendance_streak"].basis
    assert day4["attendance_streak"].basis == f"run:{four[1].on_date.isoformat()}"
    assert day4["attendance_streak"].evidence["days"] == 4


def test_FR_EW_014_school_threshold_applies_within_bounds() -> None:
    marks = _marks("present", "absent", "absent", "absent")
    assert "attendance_streak" not in _rules(_facts(marks), {"attendance_streak": {"threshold": 4}})
    # Out-of-bounds values stored earlier are clamped (max 5), never trusted.
    settings = effective({"attendance_streak": {"threshold": 99, "enabled": False}})
    assert settings["attendance_streak"].threshold == 5
    # The AP follow-up rule cannot be switched off.
    assert settings["attendance_streak"].enabled is True
    assert effective({"course_low": {"enabled": False}})["course_low"].enabled is False


def test_FR_EW_002_low_attendance_rate_over_the_last_school_days() -> None:
    marks = _marks(*(["present"] * 6 + ["absent"] * 4))
    found = _rules(_facts(marks))
    rate = found["attendance_rate"]
    assert rate.evidence["rate"] == 60
    assert rate.evidence["days"] == 10
    assert rate.basis == "month:2026-09"
    # Fewer than min_days marked: no judgement.
    assert "attendance_rate" not in _rules(_facts(_marks("absent", "absent", "present")))
    # Leave days are left out of the count.
    leave = _marks(*(["present"] * 8 + ["leave"] * 6 + ["absent"] * 2))
    assert "attendance_rate" not in _rules(_facts(leave))


def test_FR_EW_002_course_low_and_decline() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    results = [
        engine.Result(first, dt.date(2026, 7, 15), 62.0),
        engine.Result(second, dt.date(2026, 9, 10), 31.5),
    ]
    found = _rules(_facts(results=results))
    assert found["course_low"].evidence["percent"] == 31.5
    assert found["course_low"].basis == f"exam:{second}"
    decline = found["course_decline"]
    assert decline.evidence["drop"] == 30.5
    assert decline.evidence["previous_exam_id"] == str(first)
    assert decline.basis == f"exam:{second}"
    # An exam without marks (all papers absent) is not a result.
    only_absent = [*results, engine.Result(uuid.uuid4(), dt.date(2026, 9, 20), None)]
    assert _rules(_facts(results=only_absent))["course_low"].basis == f"exam:{second}"
    ok = [engine.Result(first, dt.date(2026, 7, 15), 70.0), engine.Result(second, TODAY, 66.0)]
    assert not {"course_low", "course_decline"} & set(_rules(_facts(results=ok)))


def test_FR_EW_002_repeated_concern_notes() -> None:
    recent = [TODAY - dt.timedelta(days=d) for d in (1, 5, 20)]
    found = _rules(_facts(concerns=recent))
    assert found["behaviour_concerns"].evidence["concerns"] == 3
    old = [TODAY - dt.timedelta(days=d) for d in (1, 5, 40)]
    assert "behaviour_concerns" not in _rules(_facts(concerns=old))


def test_FR_EW_001_indicators_explain_the_numbers() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    facts = _facts(
        _marks("present", "late", "absent", "absent"),
        [engine.Result(first, dt.date(2026, 7, 1), 50.0), engine.Result(second, TODAY, 44.0)],
        [TODAY],
    )
    ind = engine.indicators(facts, effective({}), TODAY)
    assert ind.attendance.days == 4
    assert ind.attendance.streak == 2
    assert ind.attendance.rate == 50.0
    assert ind.course.percent == 44.0
    assert ind.course.change == -6.0
    assert ind.behaviour.concerns == 1
    assert not ind.attendance.concern
    assert not ind.course.concern
    assert not ind.behaviour.concern


def test_FR_EW_002_disabled_rules_raise_nothing() -> None:
    results = [engine.Result(uuid.uuid4(), TODAY, 10.0)]
    assert "course_low" not in _rules(_facts(results=results), {"course_low": {"enabled": False}})


def test_FR_EW_002_evidence_holds_numbers_codes_and_dates_only() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    facts = _facts(
        _marks(*(["absent"] * 12)),
        [engine.Result(first, dt.date(2026, 7, 1), 80.0), engine.Result(second, TODAY, 20.0)],
        [TODAY, TODAY, TODAY],
    )
    for finding in engine.findings(facts, effective({}), TODAY):
        for key, value in finding.evidence.items():
            assert isinstance(value, int | float | str), key
            assert "name" not in key


def test_rules_yaml_is_complete_and_bounded() -> None:
    cfg = load_config()
    assert set(cfg.rules) == set(RULE_KEYS)
    assert cfg.flags.due_days == 7  # the M5 exit metric counts 7 days
    assert cfg.rules["attendance_streak"].threshold.default == 3  # AP follow-up rule
    assert cfg.rules["attendance_streak"].can_disable is False
    raw = yaml.safe_load(resources.files("app.insights").joinpath("rules.yaml").read_text())
    raw["rules"].pop("course_low")
    with pytest.raises(ValueError, match="every rule"):
        InsightsConfig.model_validate(raw)
