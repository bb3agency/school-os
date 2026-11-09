"""Follow-up and UDISE+ to-do rules (FR-UDI-021, FR-UDI-024, FR-UDI-033): the configuration
file and the pure state machine. No database."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.dq import followup_config as fc

D = dt.date
TODAY = D(2026, 11, 9)


def test_FR_UDI_021_config_file_is_valid_and_complete() -> None:
    cfg = fc.load_followup_config()
    assert cfg.followups.overdue_after_days == 14
    assert "promised_by" in cfg.followups.owners["parent_aadhaar"].events
    assert "promised_by" not in cfg.followups.owners["school_udise"].events
    assert cfg.channels_for("parent_contacted")
    assert cfg.channels_for("slip_given") == ()
    # UDISE+ steps come from secondary sources (owner decision D3).
    assert cfg.movements.verified is False
    assert cfg.movements.source


def _raw() -> dict[str, Any]:
    from importlib import resources

    text = resources.files("app.dq").joinpath("config/udise_followup.yaml").read_text("utf-8")
    raw: dict[str, Any] = yaml.safe_load(text)
    return raw


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r["followups"]["owners"].pop("school_udise"),
        lambda r: r["followups"]["events"].pop("slip_given"),
        lambda r: r["followups"]["owners"]["school_udise"]["events"].append("promised_by"),
        lambda r: r["followups"].update(overdue_after_days=0),
        lambda r: r["movements"]["kinds"].pop("progression"),
        lambda r: r["movements"].update(source=[]),
    ],
)
def test_FR_UDI_021_broken_config_is_refused(change: Any) -> None:
    raw = _raw()
    change(raw)
    with pytest.raises(ValidationError):
        fc.parse_followup_config(raw)


def S(event: str, on: dt.date, promised: dt.date | None = None) -> fc.Step:
    return fc.Step(event, on, promised)


def test_FR_UDI_024_states() -> None:
    assert fc.followup_state([], TODAY, 14).state == "not_started"
    waiting = fc.followup_state([S("slip_given", D(2026, 11, 1))], TODAY, 14)
    assert (waiting.state, waiting.last_event, waiting.steps) == ("waiting", "slip_given", 1)
    stale = fc.followup_state([S("slip_given", D(2026, 10, 20))], TODAY, 14)
    assert (stale.state, stale.overdue_reason) == ("overdue", "no_contact")
    # Exactly N days is not yet overdue.
    edge = fc.followup_state([S("slip_given", TODAY - dt.timedelta(days=14))], TODAY, 14)
    assert edge.state == "waiting"
    promised = [S("slip_given", D(2026, 11, 1)), S("promised_by", D(2026, 11, 2), D(2026, 11, 8))]
    late = fc.followup_state(promised, TODAY, 14)
    assert (late.state, late.overdue_reason, late.promised_on) == (
        "overdue",
        "promise_passed",
        D(2026, 11, 8),
    )
    future = [S("promised_by", D(2026, 11, 2), D(2026, 11, 20))]
    assert fc.followup_state(future, TODAY, 14).state == "waiting"
    reported = [*promised, S("update_reported", D(2026, 11, 9))]
    assert fc.followup_state(reported, TODAY, 14).state == "update_reported"
    # A contact after the reported update (still different): the promise no longer counts.
    again = [*reported, S("parent_contacted", D(2026, 11, 9))]
    out = fc.followup_state(again, TODAY, 14)
    assert (out.state, out.promised_on) == ("waiting", None)


def test_FR_UDI_033_movement_overdue() -> None:
    assert fc.movement_overdue(D(2026, 10, 1), TODAY, 14, open_=True)
    assert not fc.movement_overdue(D(2026, 10, 1), TODAY, 14, open_=False)
    assert not fc.movement_overdue(TODAY - dt.timedelta(days=14), TODAY, 14, open_=True)
