"""Portal calendar file, windows, applicability and lead days (FR-RDY-001..003, FR-RDY-006,
FR-RDY-008; ADR-0053; owner decision D3, lead decision D45). Pure: no database."""

from __future__ import annotations

import copy
import datetime as dt
import uuid
from importlib import resources
from typing import Any

import pytest
import yaml

from app.dq import calendar as cal
from app.dq.profiles import load_profiles
from app.tenancy.schemas import SUPPORTED_BOARDS

D = dt.date


def _raw() -> dict[str, Any]:
    text = resources.files("app.dq").joinpath("config/portal_calendar.yaml").read_text("utf-8")
    raw: dict[str, Any] = yaml.safe_load(text)
    return raw


def _event(**over: Any) -> cal.PortalEvent:
    base: dict[str, Any] = {
        "key": "synthetic_event_2027",
        "year": "2026-27",
        "portal": "cbse",
        "label_en": "Synthetic window",
        "label_te": "Synthetic window",
        "profile_keys": (),
        "boards": ("CBSE",),
        "classes": ("IX", "XI"),
        "opens_on": D(2026, 9, 1),
        "closes_on": D(2026, 10, 31),
        "lead_days": (30, 14, 7, 1),
        "source": ("https://example.test/circular",),
        "verified": False,
    }
    return cal.PortalEvent.model_validate({**base, **over})


# --- the shipped file (FR-RDY-001) -------------------------------------------------------------


def test_FR_RDY_001_calendar_file_loads_with_both_years_and_every_portal() -> None:
    calendar = cal.load_calendar()
    years = {e.year for e in calendar.events}
    assert years == {"2026-27", "2027-28"}
    for year in years:
        portals = {e.portal for e in calendar.for_year(year)}
        assert portals == {"udise_plus", "bseap", "cbse", "cisce", "apaar"}, year
    keys = {e.key for e in calendar.events}
    for expected in (
        "udise_plus_2026_freeze",
        "bseap_ssc_2027_nominal_roll",
        "cbse_registration_2027",
        "cbse_loc_2027",
        "cisce_registration_2027",
        "apaar_2026_generation_drive",
    ):
        assert expected in keys
    assert calendar.default_lead_days == (30, 14, 7, 1)


def test_D3_every_shipped_date_is_unverified_and_sourced() -> None:
    for event in cal.load_calendar().events:
        assert event.verified is False, event.key
        assert event.source, event.key
        assert all(s.startswith("https://") for s in event.source)
        assert event.opens_approximate or event.closes_approximate, event.key
        assert event.closes_on >= event.opens_on


def test_FR_RDY_001_linked_profiles_exist_and_belong_to_the_event_board() -> None:
    profiles = load_profiles()
    for event in cal.load_calendar().events:
        for key in event.profile_keys:
            profile = profiles[key]
            if profile.board is not None:
                assert profile.board in event.boards, (event.key, key)


def test_FR_RDY_002_calendar_boards_are_the_boards_a_school_can_declare() -> None:
    assert set(cal.BOARDS) == set(SUPPORTED_BOARDS)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r["years"]["2026-27"][0].update(closes_on=D(2026, 1, 1)), "before opens_on"),
        (lambda r: r["years"]["2026-27"][0].update(profile_keys=["no-such"]), "unknown profiles"),
        (lambda r: r["years"]["2026-27"][0].update(boards=["ICSE"]), "unknown boards"),
        (lambda r: r["years"]["2026-27"][0].update(lead_days=[1, 7]), "descending"),
        (lambda r: r["years"]["2026-27"][0].update(lead_days=[400]), "between"),
        (lambda r: r["years"]["2026-27"][0].update(source=["http://x"]), "https"),
        (lambda r: r["years"]["2026-27"][0].update(portal="mars"), "portal"),
        (
            lambda r: r["years"]["2026-27"].append(dict(r["years"]["2026-27"][0])),
            "unique",
        ),
        (lambda r: r["years"].update({"2026-28": []}), "academic year"),
        (lambda r: r.update(portals={"udise_plus": {"en": "U", "te": "U"}}), "portals"),
        (lambda r: r.update(default_lead_days=[]), "default_lead_days"),
    ],
)
def test_FR_RDY_001_a_broken_calendar_file_is_refused(change: Any, message: str) -> None:
    raw = copy.deepcopy(_raw())
    change(raw)
    with pytest.raises(cal.CalendarConfigError, match=message):
        cal.parse_calendar(raw)


def test_FR_RDY_001_dates_must_belong_to_their_academic_year() -> None:
    raw = copy.deepcopy(_raw())
    raw["years"]["2026-27"][0].update(opens_on=D(2030, 1, 1), closes_on=D(2030, 2, 1))
    with pytest.raises(cal.CalendarConfigError, match="academic year"):
        cal.parse_calendar(raw)


def test_FR_RDY_001_year_key_of_a_school_year() -> None:
    assert cal.year_key(D(2026, 6, 1)) == "2026-27"
    assert cal.year_key(D(2099, 4, 1)) == "2099-00"


# --- applicability (FR-RDY-002) ----------------------------------------------------------------


def test_FR_RDY_002_portal_events_apply_to_every_school() -> None:
    udise = _event(portal="udise_plus", boards=(), classes=())
    assert cal.applies(udise, boards=["BSEAP"], class_boards={}, class_codes={"I"})
    assert cal.applies(udise, boards=[], class_boards={}, class_codes=set())


def test_FR_RDY_002_board_events_follow_the_school_boards() -> None:
    cbse = _event()
    assert cal.applies(cbse, boards=["CBSE"], class_boards={}, class_codes={"IX"})
    assert not cal.applies(cbse, boards=["BSEAP"], class_boards={}, class_codes={"IX"})
    # No board declared yet: every board's events are shown (FR-TEN-020).
    assert cal.applies(cbse, boards=[], class_boards={}, class_codes={"IX"})


def test_FR_RDY_002_classes_must_exist_and_follow_the_event_board() -> None:
    cbse = _event(classes=("XI",))
    assert not cal.applies(cbse, boards=["CBSE"], class_boards={}, class_codes={"IX", "X"})
    assert cal.applies(cbse, boards=["CBSE"], class_boards={}, class_codes={"XI"})
    # Two boards: class XI follows CISCE here, so the CBSE event does not apply (FR-TEN-021).
    both = ["CBSE", "CISCE"]
    assert not cal.applies(cbse, boards=both, class_boards={"XI": "CISCE"}, class_codes={"XI"})
    assert cal.applies(cbse, boards=both, class_boards={"XI": "CBSE"}, class_codes={"XI"})
    # A class missing from the map with two boards: not ruled out.
    assert cal.applies(cbse, boards=both, class_boards={}, class_codes={"XI"})
    # A school with no classes yet is not ruled out by class.
    assert cal.applies(cbse, boards=["CBSE"], class_boards={}, class_codes=set())


# --- states (FR-RDY-003) -----------------------------------------------------------------------


def test_FR_RDY_003_states_of_expected_and_confirmed_windows() -> None:
    expected = cal.Window(D(2026, 9, 1), D(2026, 10, 31), confirmed=False)
    confirmed = cal.Window(D(2026, 9, 1), D(2026, 10, 31), confirmed=True)
    assert cal.state(expected, D(2026, 8, 1)) == "expected"
    # Never "open now" while nobody confirmed the dates.
    assert cal.state(expected, D(2026, 9, 15)) == "expected"
    assert cal.state(expected, D(2026, 11, 1)) == "closed"
    assert cal.state(confirmed, D(2026, 8, 1)) == "confirmed"
    assert cal.state(confirmed, D(2026, 9, 1)) == "open"
    assert cal.state(confirmed, D(2026, 10, 31)) == "open"
    assert cal.state(confirmed, D(2026, 11, 1)) == "closed"
    assert cal.days_left(confirmed, D(2026, 10, 31)) == 0
    assert cal.days_left(confirmed, D(2026, 10, 17)) == 14


# --- lead days (FR-RDY-006, FR-RDY-008) --------------------------------------------------------


LEADS = (30, 14, 7, 1)


@pytest.mark.parametrize(
    ("left", "sent", "expected"),
    [
        (45, set(), None),  # before the first lead day
        (30, set(), 30),
        (29, {30}, None),  # rerun / next morning: nothing new until 14
        (14, {30}, 14),
        (13, set(), 14),  # a missed morning still sends that step, once
        (13, {14}, None),
        (7, {30, 14}, 7),
        (1, {30, 14, 7}, 1),
        (0, {30, 14, 7}, 1),  # closing day, the 1-day step was missed
        (0, {30, 14, 7, 1}, None),
        (-1, set(), None),  # closed
    ],
)
def test_FR_RDY_008_one_reminder_per_lead_day(left: int, sent: set[int], expected: Any) -> None:
    assert cal.due_lead_day(LEADS, left, sent) == expected


# --- the school's own events (FR-RDY-005) ------------------------------------------------------


def test_FR_RDY_005_own_event_keys_are_readable_and_never_personal() -> None:
    salt = uuid.UUID("00000000-0000-0000-0000-0000000abcde")
    key = cal.own_event_key("Mandal UDISE+ review meeting!", salt=salt)
    assert key == "school:mandal-udise-review-meeting-0abcde"
    assert cal.is_own_key(key)
    telugu = cal.own_event_key("మండల సమావేశం", salt=salt)
    assert telugu == "school:event-0abcde"
    long = cal.own_event_key("x" * 120, salt=salt)
    import re

    assert re.match(cal.ANY_KEY_PATTERN, long)
    assert re.match(cal.ANY_KEY_PATTERN, key)
