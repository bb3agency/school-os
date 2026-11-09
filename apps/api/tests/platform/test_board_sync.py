"""Board sync to the control plane and protected exam windows (lead decision D78, ADR-0057;
FR-PLT-014, FR-PLT-020, FR-TEN-020).

The daily usage collector reads each school's board codes (codes only) through
``tenancy.service.tenant_board_codes`` and keeps ``platform.deployments.boards`` current, audited
``deployment.boards_synced``. ``billing.yaml`` ``protected_windows`` are the portal calendar's
board windows (``verified: false`` carried), so a billing suspension inside one needs a
platform owner, and the refusal says the dates are not verified.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session
from app.core.errors import Conflict
from app.dq.calendar import load_calendar
from app.platform import billing, usage
from app.platform.common import config
from app.tenancy import service as tenancy

from .conftest import Api, MakeOperator, Operator, provision_payload

# --- protected windows come from the portal calendar (no database) ----------------------------


def _calendar_windows() -> list[dict[str, Any]]:
    """The windows ADR-0057 derives from app/dq/config/portal_calendar.yaml: every board
    portal event (registration, list of candidates, nominal roll, exam fee), not the
    all-school portals and not the school's administrative deadlines (``school_admin``: SARAS,
    OASIS, the public disclosure; FR-RDY-018), which run for weeks and concern no board portal
    submission of student data."""
    return [
        {
            "key": e.key,
            "name": e.label_en,
            "boards": list(e.boards),
            "start": e.opens_on.isoformat(),
            "end": e.closes_on.isoformat(),
            "verified": e.verified,
        }
        for e in load_calendar().events
        if e.boards and e.portal in ("bseap", "cbse", "cisce")
    ]


def _configured_windows() -> list[dict[str, Any]]:
    return [
        {**w, "start": str(w["start"]), "end": str(w["end"])}
        for w in config()["billing"]["protected_windows"]
    ]


def test_ADR_0057_protected_windows_stay_equal_to_the_portal_calendar() -> None:
    """billing.yaml carries a copy (the control plane never imports app.dq, ADR-0020); a
    calendar release that changes a board window fails here until billing.yaml says the same."""
    windows = _configured_windows()
    assert windows, "protected_windows is seeded from the calendar"
    assert windows == _calendar_windows()
    assert all(w["verified"] is False for w in windows)  # carried, never upgraded silently


def test_FR_PLT_014_protected_window_applies_by_board_and_date() -> None:
    loc = next(w for w in _configured_windows() if w["key"] == "cbse_loc_2027")
    start, end = dt.date.fromisoformat(loc["start"]), dt.date.fromisoformat(loc["end"])
    window = billing.protected_window(["CBSE"], start)
    assert window is not None
    assert window.key in {"cbse_registration_2027", "cbse_loc_2027"}
    assert window.verified is False
    assert billing.protected_window(["CBSE", "BSEAP"], end) is not None
    assert billing.protected_window(["BSEAP"], start) is None  # another board's window
    assert billing.protected_window([], start) is None  # no boards known: nothing matches
    assert billing.protected_window(["CBSE"], dt.date(2026, 12, 15)) is None  # outside every one
    nominal = billing.protected_window(["BSEAP"], dt.date(2027, 2, 3))
    assert nominal is not None
    assert nominal.key == "bseap_ssc_2027_nominal_roll"
    assert billing.in_protected_window(["BSEAP"], dt.date(2027, 2, 3)) == nominal.name


def test_FR_PLT_014_window_without_boards_still_applies_to_every_school(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        config()["billing"],
        "protected_windows",
        [{"name": "All boards", "boards": [], "start": "2030-03-01", "end": "2030-03-10"}],
    )
    window = billing.protected_window(["SSC"], dt.date(2030, 3, 5))
    assert window is not None
    assert window.verified is True  # a hand-entered window without the flag counts as checked


# --- board sync in the daily collector (database) ---------------------------------------------


def _set_tenant_boards(admin_engine: Engine, tenant_id: uuid.UUID, boards: list[str]) -> None:
    """The school changes its boards (FR-TEN-020); as data setup, written directly."""
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.tenants SET boards = :b WHERE id = :t"),
            {"b": boards, "t": tenant_id},
        )


def _deployment_boards(tenant_id: uuid.UUID) -> list[str]:
    with platform_session() as s:
        return list(
            s.execute(
                text("SELECT boards FROM platform.deployments WHERE tenant_id = :t"),
                {"t": tenant_id},
            ).scalar_one()
        )


def _sync_events(tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    with platform_session() as s:
        rows = s.execute(
            text(
                "SELECT actor_type, resource_type, summary FROM platform.audit_events "
                "WHERE subject_tenant_id = :t AND action = 'deployment.boards_synced' "
                "ORDER BY seq"
            ),
            {"t": tenant_id},
        ).all()
    return [{"actor_type": r[0], "resource_type": r[1], "summary": r[2]} for r in rows]


@pytest.mark.db
def test_ADR_0057_tenant_board_codes_returns_codes_only(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    res = api.call("POST", "/tenants", owner, json=provision_payload(make_plan()))
    tid = uuid.UUID(res.json()["tenant_id"])
    assert tenancy.tenant_board_codes(tid) == ["SSC"]


@pytest.mark.db
def test_ADR_0057_collector_syncs_board_changes_and_audits_codes_only(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    res = api.call("POST", "/tenants", owner, json=provision_payload(make_plan()))
    tid = uuid.UUID(res.json()["tenant_id"])
    assert api.call("POST", f"/tenants/{tid}/activate", owner).status_code == 200
    assert usage.sync_boards(tid) is False  # unchanged since provisioning: no write, no audit
    assert _sync_events(tid) == []

    _set_tenant_boards(admin_engine, tid, ["CBSE", "BSEAP"])
    assert usage.sync_boards(tid) is True
    assert sorted(_deployment_boards(tid)) == ["BSEAP", "CBSE"]
    events = _sync_events(tid)
    assert len(events) == 1
    assert events[0]["actor_type"] == "system"
    assert events[0]["resource_type"] == "deployment"
    assert events[0]["summary"] == {"boards": ["BSEAP", "CBSE"], "previous": ["SSC"]}

    assert usage.sync_boards(tid) is False  # idempotent
    # Order is not a change: the same set in another order writes nothing.
    _set_tenant_boards(admin_engine, tid, ["BSEAP", "CBSE"])
    assert usage.sync_boards(tid) is False
    assert len(_sync_events(tid)) == 1

    # The daily collector runs the sync for every active or suspended school.
    _set_tenant_boards(admin_engine, tid, ["CISCE"])
    usage.collect_daily(dt.date(2026, 11, 1))
    assert _deployment_boards(tid) == ["CISCE"]
    assert _sync_events(tid)[-1]["summary"] == {"boards": ["CISCE"], "previous": ["BSEAP", "CBSE"]}


@pytest.mark.db
def test_FR_PLT_014_synced_boards_drive_the_exam_window_and_its_unverified_flag(
    api: Api,
    owner: Operator,
    make_operator: MakeOperator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
) -> None:
    """A school that moved to CBSE is protected during the CBSE window once the collector has
    synced its boards; the refusal and the owner's override say the dates are not verified."""
    from .test_billing import _issue_overdue, _school

    billing_admin = make_operator("billing_admin")
    school = _school(api, owner, make_plan())
    sub, tid = uuid.UUID(school["subscription_id"]), uuid.UUID(school["tenant_id"])
    _issue_overdue(owner, school["subscription_id"], dt.date(2026, 8, 3))
    billing.mark_past_due(today=dt.date(2026, 9, 1))
    day = dt.date(2026, 10, 15)  # inside the CBSE registration and LOC windows (expected)

    # Boards still SSC on the control plane: the CBSE window does not apply.
    assert billing.protected_window(_deployment_boards(tid), day) is None

    _set_tenant_boards(admin_engine, tid, ["CBSE"])
    assert usage.sync_boards(tid) is True
    with pytest.raises(Conflict) as refused:
        billing.suspend_subscription(
            billing_admin.actor,
            sub,
            "Unpaid",
            actor_is_owner=False,
            exam_window_override=True,
            today=day,
        )
    assert refused.value.code == "exam_window"
    assert "not verified" in str(refused.value.detail)
    out = billing.suspend_subscription(
        owner.actor, sub, "Unpaid", actor_is_owner=True, exam_window_override=True, today=day
    )
    assert out.status == "suspended"
    with platform_session() as s:
        summary: Any = s.execute(
            text(
                "SELECT summary FROM platform.audit_events WHERE resource_id = :s "
                "AND action = 'subscription.suspended' ORDER BY seq DESC LIMIT 1"
            ),
            {"s": sub},
        ).scalar_one()
    assert summary["exam_window_override"] is True
    assert summary["exam_window_verified"] is False
    assert summary["exam_window"].startswith("cbse_")
