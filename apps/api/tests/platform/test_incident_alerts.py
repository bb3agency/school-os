"""Incident deadline alerts for operators (R3-2G; lead decision D49 part 2; ADR-0050;
FR-DPDP-031, SEC-052; incident drill 2026-11-01 gap 2).

The CERT-In clock (T0 + 6 h) and each school-notice clock (confirmation + 24 h) alert once when
2 hours or less are left and once when overdue: a structured log line with ids and clock names
only (CloudWatch alarms on it) and the platform audit event ``incident.clock_alert``, which is
also the dedupe key. Time is frozen by passing ``at``. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import platform_session
from app.platform import incident_alerts, incidents
from app.platform.incident_alerts import ClockAlert, check_clocks, stage_at

from .conftest import Api, MakeOperator, letters

SCHOOL_NAME = "Synthetic Clockwork School"
SUMMARY = "Contact records of one school exposed to a wrong recipient for an hour"
HOUR = dt.timedelta(hours=1)
MINUTE = dt.timedelta(minutes=1)


# --- pure rules -------------------------------------------------------------------------------


def test_D49_FR_DPDP_031_stage_is_due_soon_within_the_window_and_overdue_after() -> None:
    due = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)
    window = 2 * HOUR
    assert stage_at(due, due - window - MINUTE, window) is None
    assert stage_at(due, due - window, window) == "due_soon"
    assert stage_at(due, due, window) == "due_soon"
    assert stage_at(due, due + MINUTE, window) == "overdue"


def test_D49_SEC_052_window_comes_from_versioned_config() -> None:
    """Invariant 13: the 2-hour lead time lives in billing.yaml, not in code."""
    assert incident_alerts.due_soon_window() == 2 * HOUR


# --- with the database ------------------------------------------------------------------------


def _school() -> uuid.UUID:
    tenant_id = uuid.uuid4()
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.deployments (id, tenant_id, tenant_code, school_name, mode, "
                "tenant_status, status) VALUES (:i, :t, :c, :n, 'shared', 'active', 'healthy')"
            ),
            {"i": uuid.uuid4(), "t": tenant_id, "c": f"t-{letters(12)}", "n": SCHOOL_NAME},
        )
    return tenant_id


def _open(api: Api, op: Any, *, cert_in: bool = True) -> tuple[uuid.UUID, dt.datetime]:
    opened = (dt.datetime.now(dt.UTC) - HOUR).replace(microsecond=0)
    res = api.call(
        "POST",
        "/incidents",
        op,
        json={
            "opened_at": opened.isoformat(),
            "severity": "P1",
            "summary": SUMMARY,
            "cert_in_required": cert_in,
        },
    )
    assert res.status_code == 201, res.text
    return uuid.UUID(res.json()["id"]), opened


def _confirmed(api: Api, make_operator: MakeOperator, schools: int) -> tuple[uuid.UUID, ...]:
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    iid, _ = _open(api, first, cert_in=False)
    tenants = [_school() for _ in range(schools)]
    for tenant_id in tenants:
        payload = {"tenant_id": str(tenant_id), "categories": ["contact"], "approx_people": 12}
        assert api.call("POST", f"/incidents/{iid}/schools", first, json=payload).status_code == 200
    assert api.call("POST", f"/incidents/{iid}/confirm", first).status_code == 200
    assert api.call("POST", f"/incidents/{iid}/confirm", second).status_code == 200
    return iid, *tenants


def _confirmed_at(incident_id: uuid.UUID) -> dt.datetime:
    with platform_session() as s:
        value: dt.datetime = s.execute(
            text("SELECT confirmed_at FROM platform.incidents WHERE id = :i"), {"i": incident_id}
        ).scalar_one()
    return value


def _mine(alerts: list[ClockAlert], incident_id: uuid.UUID) -> list[tuple[str, str, Any]]:
    return [(a.clock, a.stage, a.tenant_id) for a in alerts if a.incident_id == incident_id]


def _audited(incident_id: uuid.UUID) -> list[dict[str, Any]]:
    with platform_session() as s:
        return [
            dict(r._mapping)
            for r in s.execute(
                text(
                    "SELECT summary, actor_type, subject_tenant_id FROM platform.audit_events "
                    "WHERE action = 'incident.clock_alert' AND resource_id = :r ORDER BY seq"
                ),
                {"r": incident_id},
            )
        ]


def _logged(caplog: pytest.LogCaptureFixture, incident_id: uuid.UUID) -> list[dict[str, Any]]:
    """The structured event dicts the JSON pipeline writes for this incident."""
    return [
        dict(r.msg)
        for r in caplog.records
        if isinstance(r.msg, dict) and r.msg.get("incident_id") == str(incident_id)
    ]


@pytest.mark.db
def test_D49_FR_DPDP_031_cert_in_clock_alerts_once_per_stage(
    api: Api, make_operator: MakeOperator, caplog: pytest.LogCaptureFixture
) -> None:
    iid, opened = _open(api, make_operator("platform_owner"))
    due = opened + 6 * HOUR
    assert _mine(check_clocks(due - 2 * HOUR - MINUTE), iid) == []
    assert _mine(check_clocks(due - 2 * HOUR), iid) == [("cert_in", "due_soon", None)]
    # Idempotent across runs: the same stage never alerts twice.
    assert _mine(check_clocks(due - 2 * HOUR), iid) == []
    assert _mine(check_clocks(due - MINUTE), iid) == []
    assert _mine(check_clocks(due + MINUTE), iid) == [("cert_in", "overdue", None)]
    assert _mine(check_clocks(due + 5 * HOUR), iid) == []

    audited = _audited(iid)
    assert [(e["summary"]["clock"], e["summary"]["stage"]) for e in audited] == [
        ("cert_in", "due_soon"),
        ("cert_in", "overdue"),
    ]
    assert audited[0]["summary"]["minutes_left"] == 120
    assert audited[1]["summary"]["minutes_left"] == -1
    assert {e["actor_type"] for e in audited} == {"system"}

    logged = _logged(caplog, iid)
    assert [(r["event"], r["clock"], r["minutes_left"]) for r in logged] == [
        ("platform.incident.clock_due_soon", "cert_in", 120),
        ("platform.incident.clock_overdue", "cert_in", -1),
    ]
    assert [r["level"] for r in logged] == ["WARN", "ERROR"]


@pytest.mark.db
def test_D49_FR_DPDP_031_reported_cert_in_clock_stops_alerting(
    api: Api, make_operator: MakeOperator
) -> None:
    op = make_operator("platform_owner")
    iid, opened = _open(api, op)
    res = api.call(
        "PATCH",
        f"/incidents/{iid}",
        op,
        json={"cert_in_reported_at": dt.datetime.now(dt.UTC).isoformat()},
        headers=api.if_match(f"/incidents/{iid}", op),
    )
    assert res.status_code == 200, res.text
    assert _mine(check_clocks(opened + 7 * HOUR), iid) == []
    # Not required at all: no CERT-In clock.
    other, opened = _open(api, op, cert_in=False)
    assert _mine(check_clocks(opened + 7 * HOUR), other) == []


@pytest.mark.db
def test_D49_FR_DPDP_031_each_unnotified_school_alerts_once_per_stage(
    api: Api, make_operator: MakeOperator
) -> None:
    iid, pulled, waiting = _confirmed(api, make_operator, schools=2)
    due = _confirmed_at(iid) + 24 * HOUR
    assert _mine(check_clocks(due - 3 * HOUR), iid) == []
    soon = _mine(check_clocks(due - 90 * MINUTE), iid)
    assert sorted(soon, key=str) == sorted(
        [("school_notice", "due_soon", pulled), ("school_notice", "due_soon", waiting)], key=str
    )
    assert _mine(check_clocks(due - HOUR), iid) == []
    # One school pulls its notice: only the other one is still on the clock.
    assert incidents.mark_notice_pulled(pulled, iid)
    assert _mine(check_clocks(due + MINUTE), iid) == [("school_notice", "overdue", waiting)]
    assert _mine(check_clocks(due + 2 * HOUR), iid) == []
    audited = _audited(iid)
    assert [e["subject_tenant_id"] for e in audited].count(waiting) == 2
    assert [e["subject_tenant_id"] for e in audited].count(pulled) == 1


@pytest.mark.db
def test_D49_SEC_052_late_recorded_incident_alerts_overdue_only(
    api: Api, make_operator: MakeOperator
) -> None:
    """An incident recorded after its CERT-In deadline skips due-soon and alerts overdue once."""
    iid, opened = _open(api, make_operator("platform_owner"))
    assert _mine(check_clocks(opened + 8 * HOUR), iid) == [("cert_in", "overdue", None)]
    assert _mine(check_clocks(opened + 9 * HOUR), iid) == []


@pytest.mark.db
def test_D49_SEC_052_closed_incidents_do_not_alert(api: Api, make_operator: MakeOperator) -> None:
    op = make_operator("platform_owner")
    iid, opened = _open(api, op)
    res = api.call(
        "PATCH",
        f"/incidents/{iid}",
        op,
        json={"status": "closed"},
        headers=api.if_match(f"/incidents/{iid}", op),
    )
    if res.status_code == 409:  # D49 part 1 (built elsewhere) refuses this close: nothing to test
        pytest.skip("closing with an unreported CERT-In clock is refused (D49 part 1)")
    assert res.status_code == 200, res.text
    assert _mine(check_clocks(opened + 8 * HOUR), iid) == []


@pytest.mark.db
def test_SEC_008_D49_alerts_carry_ids_and_clock_names_only(
    api: Api,
    make_operator: MakeOperator,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No summary text, school name or count reaches a log line or the audit summary."""
    iid, _pulled, waiting = _confirmed(api, make_operator, schools=2)
    due = _confirmed_at(iid) + 24 * HOUR
    sent = _mine(check_clocks(due + MINUTE), iid)
    assert len(sent) == 2
    logged = _logged(caplog, iid)
    assert {r["event"] for r in logged} == {"platform.incident.clock_overdue"}
    allowed = {"event", "level", "logger", "ts", "service", "version", "env", "trace_id"}
    allowed |= {"span_id", "incident_id", "clock", "minutes_left", "alert", "tenant_id"}
    for record in logged:
        assert set(record) <= allowed, set(record) - allowed
    assert str(waiting) in {r["tenant_id"] for r in logged}
    out, err = capsys.readouterr()
    everything = out + err + caplog.text + str(logged)
    for secret in (SCHOOL_NAME, SUMMARY, "Contact records", "approx_people"):
        assert secret not in everything, secret
    for event in _audited(iid):
        assert set(event["summary"]) == {"clock", "stage", "minutes_left"}
