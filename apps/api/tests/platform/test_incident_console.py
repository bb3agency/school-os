"""Incident follow-ups in the operator console (wave F1; lead decision D49; ADR-0050;
FR-DPDP-031, FR-DPDP-032).

1. In-console clock alerts (D49 part 2): holders of ``platform.incidents.read`` see each open
   incident clock that is due soon or overdue on the dashboard and on ``/incidents/clocks``,
   from the same rules as the beat alerts; other operators get no tile (``null``).
2. A dedicated-host school told of a reopen by phone (D49 open item): an operator with
   ``platform.incidents.manage`` (step-up) records it; it counts as the school's pull and is
   audited ``incident.school_reopen_told``. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import platform_session
from app.platform import incident_alerts, incidents

from .conftest import Api, MakeOperator, letters

pytestmark = pytest.mark.db

SUMMARY = "Contact records of one school exposed to a wrong recipient for an hour"
HOUR = dt.timedelta(hours=1)
MINUTE = dt.timedelta(minutes=1)


def _school(mode: str = "shared") -> tuple[uuid.UUID, str]:
    tenant_id, code = uuid.uuid4(), f"t-{letters(12)}"
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.deployments (id, tenant_id, tenant_code, school_name, mode, "
                "tenant_status, status, heartbeat_key_id, heartbeat_key_ciphertext) "
                "VALUES (:i, :t, :c, 'Synthetic Console School', :m, 'active', 'healthy', :k, "
                ":kc)"
            ),
            {
                "i": uuid.uuid4(),
                "t": tenant_id,
                "c": code,
                "m": mode,
                "k": "hb-1" if mode == "dedicated" else None,
                "kc": b"\x01" * 16 if mode == "dedicated" else None,
            },
        )
    return tenant_id, code


def _open(api: Api, op: Any, *, ago: dt.timedelta, cert_in: bool = True) -> str:
    opened = (dt.datetime.now(dt.UTC) - ago).replace(microsecond=0)
    res = api.call(
        "POST",
        "/incidents",
        op,
        json={
            "opened_at": opened.isoformat(),
            "severity": "P2",
            "summary": SUMMARY,
            "cert_in_required": cert_in,
        },
    )
    assert res.status_code == 201, res.text
    iid: str = res.json()["id"]
    return iid


def _with_school(api: Api, first: Any, second: Any, tenant_id: uuid.UUID, iid: str) -> None:
    payload = {"tenant_id": str(tenant_id), "categories": ["contact"], "approx_people": 12}
    assert api.call("POST", f"/incidents/{iid}/schools", first, json=payload).status_code == 200
    assert api.call("POST", f"/incidents/{iid}/confirm", first).status_code == 200
    assert api.call("POST", f"/incidents/{iid}/confirm", second).status_code == 200


def _mine(clocks: list[dict[str, Any]], iid: str) -> list[dict[str, Any]]:
    return [c for c in clocks if c["incident_id"] == iid]


# --- 1. in-console clock alerts (D49 part 2) --------------------------------------------------


def test_D49_FR_DPDP_031_clocks_due_soon_and_overdue_reach_the_console(
    api: Api, make_operator: MakeOperator
) -> None:
    owner = make_operator("platform_owner")
    soon = _open(api, owner, ago=5 * HOUR)  # CERT-In due in about 1 h
    late = _open(api, owner, ago=7 * HOUR)  # CERT-In overdue by about 1 h
    calm = _open(api, owner, ago=MINUTE)  # CERT-In due in about 6 h: no alert yet
    clocks = api.call("GET", "/incidents/clocks", owner)
    assert clocks.status_code == 200, clocks.text
    body = clocks.json()
    (s,) = _mine(body, soon)
    assert (s["clock"], s["stage"], s["severity"], s["tenant_id"]) == (
        "cert_in",
        "due_soon",
        "P2",
        None,
    )
    assert 50 <= s["minutes_left"] <= 60
    (o,) = _mine(body, late)
    assert (o["clock"], o["stage"]) == ("cert_in", "overdue")
    assert -70 <= o["minutes_left"] <= -60
    assert _mine(body, calm) == []
    # Most urgent first.
    order = [c["incident_id"] for c in body if c["incident_id"] in (soon, late)]
    assert order == [late, soon]
    # The dashboard tile carries the same clocks.
    tile = api.call("GET", "/dashboard", owner).json()["incident_clocks"]
    assert _mine(tile, soon) == [s]
    assert _mine(tile, late) == [o]
    # Never the summary text.
    assert "exposed" not in str(body)
    # Recording the CERT-In report ends the clock.
    res = api.call(
        "PATCH",
        f"/incidents/{late}",
        owner,
        json={"cert_in_reported_at": dt.datetime.now(dt.UTC).isoformat()},
        headers=api.if_match(f"/incidents/{late}", owner),
    )
    assert res.status_code == 200, res.text
    assert _mine(api.call("GET", "/incidents/clocks", owner).json(), late) == []


def test_D49_FR_DPDP_031_school_notice_clock_names_the_school_by_tenant_code(
    api: Api, make_operator: MakeOperator
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    iid = _open(api, first, ago=MINUTE, cert_in=False)
    tenant_id, code = _school()
    _with_school(api, first, second, tenant_id, iid)
    with platform_session() as s:
        confirmed: dt.datetime = s.execute(
            text("SELECT confirmed_at FROM platform.incidents WHERE id = :i"), {"i": iid}
        ).scalar_one()
    due = confirmed + 24 * HOUR
    assert [
        c for c in incident_alerts.open_clocks(due - 3 * HOUR) if str(c.incident_id) == iid
    ] == []
    (clock,) = [c for c in incident_alerts.open_clocks(due - HOUR) if str(c.incident_id) == iid]
    assert (clock.clock, clock.stage, clock.tenant_id, clock.tenant_code) == (
        "school_notice",
        "due_soon",
        tenant_id,
        code,
    )
    assert clock.due_at == due
    (over,) = [c for c in incident_alerts.open_clocks(due + HOUR) if str(c.incident_id) == iid]
    assert (over.stage, over.minutes_left) == ("overdue", -60)
    # Reading the console never alerts and never writes the dedupe events.
    with platform_session() as s:
        alerts: int = s.execute(
            text(
                "SELECT count(*) FROM platform.audit_events "
                "WHERE action = 'incident.clock_alert' AND resource_id = :i"
            ),
            {"i": iid},
        ).scalar_one()
    assert alerts == 0
    # The same rules as the beat alerts: both see the same stage.
    sent = [a for a in incident_alerts.check_clocks(due + HOUR) if str(a.incident_id) == iid]
    assert [(a.clock, a.stage, a.tenant_id) for a in sent] == [
        ("school_notice", "overdue", tenant_id)
    ]


def test_D49_FR_DPDP_031_closed_incidents_have_no_clocks(
    api: Api, make_operator: MakeOperator
) -> None:
    op = make_operator("platform_owner")
    iid = _open(api, op, ago=7 * HOUR, cert_in=False)
    res = api.call(
        "PATCH",
        f"/incidents/{iid}",
        op,
        json={"status": "closed"},
        headers=api.if_match(f"/incidents/{iid}", op),
    )
    assert res.status_code == 200, res.text
    assert _mine(api.call("GET", "/incidents/clocks", op).json(), iid) == []


@pytest.mark.parametrize("role", ["platform_engineer", "support_agent", "platform_viewer"])
def test_D49_FR_DPDP_031_incident_readers_get_the_tile(
    api: Api, make_operator: MakeOperator, role: str
) -> None:
    op = make_operator(role)
    assert api.call("GET", "/incidents/clocks", op).status_code == 200
    dash = api.call("GET", "/dashboard", op)
    assert dash.status_code == 200
    assert isinstance(dash.json()["incident_clocks"], list)


def test_D49_FR_DPDP_031_operators_without_incident_read_get_no_tile(
    api: Api, make_operator: MakeOperator
) -> None:
    owner, billing = make_operator("platform_owner"), make_operator("billing_admin")
    _open(api, owner, ago=7 * HOUR)  # an overdue clock exists
    assert api.call("GET", "/incidents/clocks", billing).status_code == 403
    dash = api.call("GET", "/dashboard", billing)
    assert dash.status_code == 200
    assert dash.json()["incident_clocks"] is None
    assert dash.json()["mrr_inr"] is not None, "the rest of the dashboard is unchanged"


# --- 2. a dedicated school told of a reopen by phone (D49 open item) ---------------------------


def _reopened(api: Api, first: Any, second: Any, mode: str) -> tuple[str, uuid.UUID]:
    iid = _open(api, first, ago=HOUR, cert_in=False)
    tenant_id, _ = _school(mode)
    _with_school(api, first, second, tenant_id, iid)
    closing = api.call(
        "PATCH",
        f"/incidents/{iid}",
        first,
        json={"status": "closed", "notified_tenant_ids": [str(tenant_id)]},
        headers=api.if_match(f"/incidents/{iid}", first),
    )
    assert closing.status_code == 200, closing.text
    reason = {"reason": "Another kind of record found exposed after closure"}
    assert api.call("POST", f"/incidents/{iid}/reopen", first, json=reason).status_code == 200
    assert api.call("POST", f"/incidents/{iid}/reopen:confirm", second).status_code == 200
    return iid, tenant_id


def _told(api: Api, op: Any, iid: str, tenant_id: uuid.UUID, **kw: Any) -> Any:
    body = {"tenant_id": str(tenant_id), "reopen_seq": 1, "note": "Principal told by phone"}
    return api.call(
        "POST", f"/incidents/{iid}/reopen:told", op, json=body | kw.pop("body", {}), **kw
    )


def _events(action: str, iid: str) -> list[dict[str, Any]]:
    with platform_session() as s:
        return [
            dict(r._mapping)
            for r in s.execute(
                text(
                    "SELECT summary, actor_id, subject_tenant_id FROM platform.audit_events "
                    "WHERE action = :a AND resource_id = :r ORDER BY seq"
                ),
                {"a": action, "r": iid},
            )
        ]


def test_D49_FR_DPDP_032_dedicated_school_told_of_reopen_by_phone_is_recorded(
    api: Api, make_operator: MakeOperator
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    iid, tenant_id = _reopened(api, first, second, "dedicated")
    before = api.call("GET", f"/incidents/{iid}", first).json()["schools"][0]
    assert (before["reopen_pulled"], before["mode"]) == (0, "dedicated")
    res = _told(api, second, iid, tenant_id)
    assert res.status_code == 200, res.text
    (school,) = res.json()["schools"]
    assert school["reopen_pulled"] == 1
    assert school["reopen_pulled_at"] is not None
    (event,) = _events("incident.school_reopen_told", iid)
    assert event["summary"] == {
        "source": "operator",
        "reopen": 1,
        "note": "Principal told by phone",
    }
    assert event["subject_tenant_id"] == tenant_id
    assert event["actor_id"] == second.id
    # Treated as received: nothing pending any more, and a late report of the reopen adds nothing.
    again = _told(api, second, iid, tenant_id)
    assert (again.status_code, again.json()["code"]) == (409, "reopen_not_pending")
    assert incidents.mark_reopen_pulled(tenant_id, uuid.UUID(iid), 1) is False


def test_D49_FR_DPDP_032_reopen_told_is_refused_for_shared_schools(
    api: Api, make_operator: MakeOperator
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    iid, tenant_id = _reopened(api, first, second, "shared")
    res = _told(api, first, iid, tenant_id)
    assert (res.status_code, res.json()["code"]) == (409, "school_pulls")
    assert _events("incident.school_reopen_told", iid) == []


def test_D49_FR_DPDP_032_reopen_told_needs_something_pending_and_the_current_count(
    api: Api, make_operator: MakeOperator
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    # Never reopened: nothing pending.
    iid = _open(api, first, ago=HOUR, cert_in=False)
    tenant_id, _ = _school("dedicated")
    _with_school(api, first, second, tenant_id, iid)
    nothing = _told(api, first, iid, tenant_id, body={"reopen_seq": 1})
    assert (nothing.status_code, nothing.json()["code"]) == (409, "reopen_not_pending")
    reopened, school = _reopened(api, first, second, "dedicated")
    stale = _told(api, first, reopened, school, body={"reopen_seq": 2})
    assert (stale.status_code, stale.json()["code"]) == (409, "reopen_changed")
    other, _ = _school("dedicated")
    assert _told(api, first, reopened, other).status_code == 404


@pytest.mark.parametrize(
    "note",
    ["Told on 9876543210", "Mail sent to head@school.example", "x" * 201, "ok"],
)
def test_D49_FR_DPDP_030_reopen_told_note_follows_the_summary_rule(
    api: Api, make_operator: MakeOperator, note: str
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    iid, tenant_id = _reopened(api, first, second, "dedicated")
    res = _told(api, first, iid, tenant_id, body={"note": note})
    assert res.status_code == 422, res.text


def test_D49_FR_DPDP_032_reopen_told_needs_manage_and_step_up(
    api: Api, make_operator: MakeOperator
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    iid, tenant_id = _reopened(api, first, second, "dedicated")
    for role in ("platform_viewer", "support_agent", "billing_admin"):
        assert _told(api, make_operator(role), iid, tenant_id).status_code == 403
    stale = _told(api, first, iid, tenant_id, fresh=False)
    assert stale.status_code == 428
    assert _events("incident.school_reopen_told", iid) == []
