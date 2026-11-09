"""Compliance catalogue and date rules (FC-01; FR-COMP-001, FR-COMP-002, FR-COMP-010..012).
Pure: no database."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
import yaml

from app.compliance import config as cfg
from app.disclosure import formats

DAY = dt.date(2026, 11, 9)
LEADS = (90, 60, 30, 7, 0)


def _due(valid_until: dt.date | None, **kw: Any) -> cfg.DueReminder | None:
    return cfg.due_reminder(
        valid_until,
        DAY,
        lead_days=LEADS,
        expired_every_days=30,
        last_lead=kw.get("last_lead"),
        last_on=kw.get("last_on"),
    )


def test_FR_COMP_001_catalogue_loads_unverified_with_sources() -> None:
    cat = cfg.catalogue()
    assert cat.verified is False
    assert "fire_noc" in cat.kinds
    assert cat.kinds["fire_noc"].typical_validity_months == 60
    assert cat.disclosure == "warn"  # lead decision D85
    assert cat.reminders.lead_days == (90, 60, 30, 7, 0)


def test_FR_COMP_002_kinds_follow_the_school_boards() -> None:
    cat = cfg.catalogue()
    ap = cat.kinds_for(["BSEAP"])
    cbse = cat.kinds_for(["CBSE"])
    assert "state_recognition" in ap
    assert "cbse_affiliation" not in ap
    assert "cbse_affiliation" in cbse
    assert "state_recognition" not in cbse
    assert "fire_noc" in ap
    assert "fire_noc" in cbse
    assert "other" not in ap


def test_FR_COMP_020_disclosure_items_exist_in_appendix_ix() -> None:
    items = {i.key: i for i in formats.current().items()}
    for key, kind in cfg.catalogue().kinds.items():
        for item in kind.disclosure_items:
            assert item in items, (key, item)
            assert items[item].takes_documents, (key, item)
    assert cfg.catalogue().kinds_for_item("fire_safety") == ("fire_noc",)


def test_FR_COMP_001_bad_catalogue_is_refused() -> None:
    raw = yaml.safe_load(
        (cfg.resources.files("app.compliance") / "catalogue.yaml").read_text("utf-8")
    )
    raw["kinds"].pop("other")
    with pytest.raises(ValueError, match="other"):
        cfg.parse(raw)
    raw = yaml.safe_load(
        (cfg.resources.files("app.compliance") / "catalogue.yaml").read_text("utf-8")
    )
    raw["kinds"]["fire_noc"]["boards"] = ["ICSE"]
    with pytest.raises(ValueError, match="boards"):
        cfg.parse(raw)
    raw["kinds"]["fire_noc"]["boards"] = []
    raw["reminders"]["lead_days"] = [30, 7]
    with pytest.raises(ValueError, match="lead day 0"):
        cfg.parse(raw)


@pytest.mark.parametrize(
    ("offset", "expected"),
    [(None, "no_expiry"), (-1, "expired"), (0, "expiring"), (90, "expiring"), (91, "valid")],
)
def test_FR_COMP_010_state(offset: int | None, expected: str) -> None:
    until = None if offset is None else DAY + dt.timedelta(days=offset)
    assert cfg.state(until, DAY, expiring_days=90) == expected


def test_FR_COMP_011_reminder_lead_days() -> None:
    assert _due(None) is None
    assert _due(DAY + dt.timedelta(days=91)) is None
    first = _due(DAY + dt.timedelta(days=90))
    assert first == cfg.DueReminder(lead=90, days=90, expired=False)
    # Already sent at 90: nothing until 60.
    assert _due(DAY + dt.timedelta(days=61), last_lead=90, last_on=DAY) is None
    assert _due(DAY + dt.timedelta(days=60), last_lead=90, last_on=DAY) == cfg.DueReminder(
        lead=60, days=60, expired=False
    )
    # A missed run sends only the latest step.
    assert _due(DAY + dt.timedelta(days=5)) == cfg.DueReminder(lead=7, days=5, expired=False)
    assert _due(DAY, last_lead=7, last_on=DAY) == cfg.DueReminder(lead=0, days=0, expired=False)


def test_FR_COMP_012_expired_reminder_then_every_30_days() -> None:
    past = DAY - dt.timedelta(days=3)
    assert _due(past, last_lead=0, last_on=DAY) == cfg.DueReminder(
        lead=cfg.EXPIRED_LEAD, days=3, expired=True
    )
    assert _due(past, last_lead=cfg.EXPIRED_LEAD, last_on=DAY - dt.timedelta(days=29)) is None
    again = _due(past, last_lead=cfg.EXPIRED_LEAD, last_on=DAY - dt.timedelta(days=30))
    assert again is not None
    assert again.expired
