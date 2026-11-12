"""Safety check kinds and policy review rules (FC-09, FC-10; FR-COMP-046, FR-COMP-047,
FR-COMP-050, FR-COMP-061). Pure: no database."""

from __future__ import annotations

import datetime as dt
from importlib import resources
from typing import Any

import pytest
import yaml

from app.compliance import safety_config as cfg

DAY = dt.date(2026, 11, 12)


def _raw(name: str) -> Any:
    return yaml.safe_load(resources.files("app.compliance").joinpath(name).read_text("utf-8"))


def test_FR_COMP_046_checks_catalogue_loads_unverified() -> None:
    cat = cfg.checks()
    assert cat.verified is False
    assert cat.kinds["fire_drill"].drill is True
    assert cat.kinds["fire_extinguisher_refill"].every_months == 12
    assert cat.kinds["other"].every_months is None
    assert all(k.note for k in cat.kinds.values())


def test_FR_COMP_047_kinds_follow_the_school_boards() -> None:
    cat = cfg.checks()
    assert "cctv_check" in cat.kinds_for(["CBSE"])
    assert "cctv_check" not in cat.kinds_for(["BSEAP"])
    assert "other" not in cat.kinds_for(["CBSE"])


def test_FR_COMP_046_bad_catalogues_are_refused() -> None:
    raw = _raw("checks.yaml")
    del raw["kinds"]["other"]
    with pytest.raises(ValueError, match="other"):
        cfg.parse_checks(raw)
    raw = _raw("checks.yaml")
    raw["kinds"]["fire_drill"]["boards"] = ["ICSE"]
    with pytest.raises(ValueError, match="unknown boards"):
        cfg.parse_checks(raw)
    raw = _raw("checks.yaml")
    raw["reminders"]["lead_days"] = [7]
    with pytest.raises(ValueError, match="lead day 0"):
        cfg.parse_checks(raw)


def test_FR_COMP_061_policy_rules_load_unverified() -> None:
    rules = cfg.policies()
    assert rules.verified is False
    assert rules.review_months == 12
    assert rules.reminders.lead_days == (30, 7, 0)


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (dt.date(2026, 1, 31), 1, dt.date(2026, 2, 28)),
        (dt.date(2027, 11, 30), 3, dt.date(2028, 2, 29)),
        (dt.date(2026, 11, 12), 12, dt.date(2027, 11, 12)),
        (dt.date(2026, 12, 15), 1, dt.date(2027, 1, 15)),
    ],
)
def test_FR_COMP_050_add_months(day: dt.date, months: int, expected: dt.date) -> None:
    assert cfg.add_months(day, months) == expected


def test_FR_COMP_050_check_state() -> None:
    cat = cfg.checks()
    drill = cat.kinds["fire_drill"]  # every 4 months
    soon = cat.due_soon_days
    assert cfg.check_state(drill, None, DAY, due_soon_days=soon) == "never"
    assert cfg.check_state(drill, DAY, DAY, due_soon_days=soon) == "ok"
    assert cfg.check_state(drill, dt.date(2026, 7, 20), DAY, due_soon_days=soon) == "due_soon"
    assert cfg.check_state(drill, dt.date(2026, 7, 12), DAY, due_soon_days=soon) == "due_soon"
    assert cfg.check_state(drill, dt.date(2026, 8, 1), DAY, due_soon_days=soon) == "ok"
    assert cfg.check_state(drill, dt.date(2026, 7, 11), DAY, due_soon_days=soon) == "overdue"
    other = cat.kinds["other"]
    assert cfg.check_state(other, DAY, DAY, due_soon_days=soon) == "logged"
    assert cfg.next_due(drill, dt.date(2026, 7, 11)) == dt.date(2026, 11, 11)


def test_FR_COMP_061_review_state() -> None:
    assert cfg.review_state(DAY, DAY, due_soon_days=30) == "due_soon"
    assert cfg.review_state(DAY - dt.timedelta(days=1), DAY, due_soon_days=30) == "overdue"
    assert cfg.review_state(DAY + dt.timedelta(days=31), DAY, due_soon_days=30) == "ok"
