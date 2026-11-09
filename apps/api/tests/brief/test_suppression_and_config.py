"""Weekly brief: small-cell suppression, rendering and configuration (FA-05; FR-BRF-009,
FR-BRF-014). Pure: no database."""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

from app.authz.context import Scopes, UserContext
from app.brief import service
from app.brief.config import load_config
from app.brief.suppression import suppress_group, suppress_one
from app.knowledge.config.tools import load_tools_config


def _ctx(*permissions: str) -> UserContext:
    return UserContext(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        roles=frozenset({"principal"}),
        permissions=frozenset({"brief.read", *permissions}),
        scopes=Scopes(school=True),
        mfa=True,
        auth_time=None,
    )


ALL = tuple(p for perms in service.SECTION_PERMISSIONS.values() for p in perms)


def _counts(**over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "v": 1,
        "as_of": "2026-11-09",
        "readiness": [
            {
                "profile_key": "bseap_ssc",
                "label_en": "BSEAP SSC",
                "students": 160,
                "ready": 142,
                "needs_parent": 15,
                "needs_school": 3,
                "blocked": 0,
            }
        ],
        "findings": {"blockers": 7, "warnings": 12, "students_with_blockers": 3},
        "changes_waiting": 2,
        "certificates_issued": 1,
        "deadlines": [
            {
                "event_key": "bseap_ssc_2027_nominal_roll",
                "portal": "bseap",
                "label_en": "SSC nominal roll",
                "closes_on": "2026-11-20",
                "days_left": 11,
                "confirmed": False,
            }
        ],
        "fees": {
            "receipts": 4,
            "collected": "12500.00",
            "balance": "90000.00",
            "overdue": "15000.00",
            "students_owing": 2,
        },
        "ask": {"answers": 40, "included": 300},
    }
    out.update(over)
    return out


def test_FR_BRF_009_small_cells_are_hidden_with_secondary_suppression() -> None:
    assert suppress_one(0, 5) == 0
    assert suppress_one(4, 5) is None
    assert suppress_one(5, 5) == 5
    # One small cell: the next smallest non-zero is hidden too, so the total cannot reveal it.
    assert suppress_group({"a": 142, "b": 15, "c": 3, "d": 0}, 5) == {
        "a": 142,
        "b": None,
        "c": None,
        "d": 0,
    }
    # Two small cells hide each other.
    assert suppress_group({"a": 150, "b": 2, "c": 3}, 5) == {"a": 150, "b": None, "c": None}


def test_FR_BRF_009_minimum_matches_the_ask_count_tools() -> None:
    tools = load_tools_config()
    assert load_config().small_cell_min == min(
        t.small_cell_min for t in tools.tools.values() if t.small_cell_min is not None
    )


def test_FR_BRF_003_render_shows_change_and_hides_small_cells() -> None:
    before = {
        "week_start": dt.date(2026, 10, 26),
        "counts": _counts(
            readiness=[
                {
                    "profile_key": "bseap_ssc",
                    "label_en": "BSEAP SSC",
                    "students": 160,
                    "ready": 124,
                    "needs_parent": 30,
                    "needs_school": 6,
                    "blocked": 0,
                }
            ]
        ),
    }
    out = service.render(
        _ctx(*ALL),
        _counts(),
        week_start=dt.date(2026, 11, 2),
        row=None,
        previous=before,  # type: ignore[arg-type]
    )
    (line,) = out.readiness or []
    assert (line.students, line.ready, line.ready_change) == (160, 142, 18)
    assert line.needs_school is None  # 3 < 5
    assert line.needs_parent is None  # secondary suppression
    assert line.blocked == 0
    assert out.findings is not None
    assert out.findings.students_with_blockers is None
    assert out.findings.blockers == 7  # work counts are not hidden
    assert out.fees is not None
    assert out.fees.students_owing is None
    assert out.week_end == dt.date(2026, 11, 8)
    assert out.stored is False
    assert out.id is None


def test_FR_BRF_002_sections_follow_the_readers_other_permissions() -> None:
    out = service.render(
        _ctx("dq.findings.read"),
        _counts(),
        week_start=dt.date(2026, 11, 2),
        row=None,
        previous=None,
    )
    assert out.findings is not None
    assert out.readiness is None
    assert out.deadlines is None
    assert out.fees is None
    assert out.ask is None
    assert out.changes_waiting is None
    assert out.certificates_issued is None


def test_FR_BRF_001_counts_hold_no_personal_keys() -> None:
    """The stored JSON's keys are fixed counts and codes; nothing names a student or person."""
    flat = repr(_counts())
    for word in ("name", "student_id", "phone", "dob", "aadhaar", "flag"):
        assert word not in flat.replace("students_with_blockers", "").replace("students_owing", "")


def test_FR_BRF_014_config_is_versioned_and_email_is_opt_in() -> None:
    cfg = load_config()
    assert cfg.version == 1
    assert cfg.delivery.email_default is False
    assert cfg.delivery.in_app_default is True
    assert cfg.deadline_days == 14
    assert dataclasses.is_dataclass(service.system_context(uuid.uuid4()))
    assert service.system_context(uuid.uuid4()).permissions.isdisjoint(
        {"insights.read", "insights.flag.read", "brief.read"}
    )


def test_FR_BRF_010_weeks_start_on_monday() -> None:
    assert service.week_start_of(dt.date(2026, 11, 9)) == dt.date(2026, 11, 9)
    assert service.week_start_of(dt.date(2026, 11, 15)) == dt.date(2026, 11, 9)
