"""Breach clocks, reminders and the pull of SchoolOS notices (R3-1B; ADR-0050 decisions 2 and 5;
FR-DPDP-032, FR-DPDP-035; US-2802). Synthetic data only."""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session
from app.platform import service as control_plane
from app.privacy import breach_service, breach_sync

pytestmark = pytest.mark.db
P = sys.modules["sos_test_consent_support"]
W = sys.modules["sos_test_api_world"]


def _notifications(admin: Engine, tenant_id: uuid.UUID, key: str, breach_id: Any) -> int:
    with admin.connect() as c:
        value: int = c.execute(
            text(
                "SELECT count(*) FROM ops.notifications WHERE tenant_id = :t "
                "AND template_key = :k AND resource_id = :r"
            ),
            {"t": tenant_id, "k": key, "r": breach_id},
        ).scalar_one()
    return value


def test_FR_DPDP_035_reminders_at_24_and_60_hours_once(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    out = P.create_breach(api, owner, noticed_at=P.noticed(1))
    bid = out["id"]
    noticed = dt.datetime.fromisoformat(out["noticed_at"])
    key = "privacy.breach.reminder"
    assert (
        breach_service.send_reminders(world.a.tenant_id, now=noticed + dt.timedelta(hours=23)) >= 0
    )
    assert _notifications(admin_engine, world.a.tenant_id, key, bid) == 0
    breach_service.send_reminders(world.a.tenant_id, now=noticed + dt.timedelta(hours=25))
    first = _notifications(admin_engine, world.a.tenant_id, key, bid)
    assert first == 2, "owner and principal"
    breach_service.send_reminders(world.a.tenant_id, now=noticed + dt.timedelta(hours=26))
    assert _notifications(admin_engine, world.a.tenant_id, key, bid) == first, "idempotent"
    breach_service.send_reminders(world.a.tenant_id, now=noticed + dt.timedelta(hours=61))
    assert _notifications(admin_engine, world.a.tenant_id, key, bid) == 2 * first
    with admin_engine.connect() as c:
        reminders: list[str] = list(
            c.execute(
                text("SELECT reminder FROM ops.breach_reminders WHERE breach_id = :b ORDER BY 1"),
                {"b": bid},
            )
            .scalars()
            .all()
        )
    assert reminders == ["board_24h", "report_60h"]


def test_FR_DPDP_035_no_reminder_once_the_board_report_is_recorded(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    out = P.create_breach(api, owner, noticed_at=P.noticed(1))
    done = dt.datetime.now(dt.UTC).isoformat()
    res = api.call(
        owner,
        "PATCH",
        f"/api/v1/privacy/breaches/{out['id']}",
        json={"board_report": {"done_at": done, "method": "portal"}},
        headers={"If-Match": 'W/"1"'},
    )
    assert res.status_code == 200, res.text
    noticed = dt.datetime.fromisoformat(out["noticed_at"])
    breach_service.send_reminders(world.a.tenant_id, now=noticed + dt.timedelta(hours=70))
    assert (
        _notifications(admin_engine, world.a.tenant_id, "privacy.breach.reminder", out["id"]) == 0
    )


# --- the pull (FR-DPDP-032) ---------------------------------------------------------------------


def _confirmed_incident(tenant_id: uuid.UUID, *, confirmed: bool = True) -> uuid.UUID:
    """An incident affecting ``tenant_id``, confirmed by two synthetic operators (written as
    sos_platform, like the control plane)."""
    ops = [uuid.uuid4(), uuid.uuid4()]
    incident = uuid.uuid4()
    with platform_session() as s:
        for oid in ops:
            s.execute(
                text(
                    "INSERT INTO platform.operators (id, idp_subject, email, display_name, status, "
                    "mfa_enrolled) VALUES (:i, :s, :e, 'Synthetic Operator', 'active', true)"
                ),
                {"i": oid, "s": f"op-{oid}", "e": f"op-{oid.hex[:10]}@example.test"},
            )
        s.execute(
            text(
                "INSERT INTO platform.incidents (id, opened_at, severity, summary, "
                "cert_in_required, lead_operator_id, created_by, confirmed_by, "
                "first_confirmed_at, second_confirmed_by, confirmed_at) VALUES (:i, "
                "now() - interval '3 hours', 'P1', 'Attendance and contact records of one school "
                "exposed through a misconfigured export link', true, :a, :a, :a, now(), "
                ":b, CASE WHEN :c THEN now() END)"
            ),
            {"i": incident, "a": ops[0], "b": ops[1] if confirmed else None, "c": confirmed},
        )
        s.execute(
            text(
                "INSERT INTO platform.incident_schools (id, incident_id, tenant_id, categories, "
                "approx_people, window_from, window_to) VALUES (:i, :n, :t, "
                "ARRAY['attendance','contact'], 120, now() - interval '2 days', now())"
            ),
            {"i": uuid.uuid4(), "n": incident, "t": tenant_id},
        )
    return incident


def _breaches_for(admin: Engine, incident: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text("SELECT * FROM ops.breach_records WHERE platform_incident_id = :i"),
                {"i": incident},
            )
        ]


def test_FR_DPDP_032_confirmed_notice_is_pulled_once_and_reported(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    incident = _confirmed_incident(world.a.tenant_id)
    assert breach_sync.sync_school(world.a.tenant_id) >= 1
    (row,) = _breaches_for(admin_engine, incident)
    assert row["origin"] == "processor_notice"
    assert row["categories"] == ["attendance", "contact"]
    assert row["processor_facts"]["approx_people"] == 120
    assert row["recorded_by"] is None
    assert breach_sync.sync_school(world.a.tenant_id) == 0, "idempotent"
    assert len(_breaches_for(admin_engine, incident)) == 1
    with platform_session() as s:
        pulled: Any = s.execute(
            text(
                "SELECT pulled_at, school_notified_at FROM platform.incident_schools "
                "WHERE incident_id = :i"
            ),
            {"i": incident},
        ).one()
    assert pulled.pulled_at is not None
    assert pulled.school_notified_at is not None
    assert (
        _notifications(admin_engine, world.a.tenant_id, "privacy.breach.notice_received", row["id"])
        == 2
    )
    (event,) = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "privacy.breach.notice_received")
        if e["resource_id"] == row["id"]
    ]
    assert event["summary"]["platform_incident_id"] == str(incident)
    # The register shows it with its facts to the principal; school B never sees it.
    detail = api.call(world.person("principal"), "GET", f"/api/v1/privacy/breaches/{row['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["processor_facts"]["severity"] == "P1"
    assert "misconfigured export link" in detail.json()["description"]
    b_owner = world.b.people["owner"]
    assert api.call(b_owner, "GET", f"/api/v1/privacy/breaches/{row['id']}").status_code == 404


def test_FR_DPDP_032_unconfirmed_incidents_are_not_pulled(world: Any, admin_engine: Engine) -> None:
    incident = _confirmed_incident(world.a.tenant_id, confirmed=False)
    breach_sync.sync_school(world.a.tenant_id)
    assert _breaches_for(admin_engine, incident) == []


def test_FR_DPDP_032_a_report_failure_is_retried_by_the_next_sweep(
    world: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident = _confirmed_incident(world.a.tenant_id)
    real = control_plane.record_incident_notice_pulled

    def down(*_: Any) -> bool:
        from app.core.errors import ServiceUnavailable

        raise ServiceUnavailable()

    monkeypatch.setattr(control_plane, "record_incident_notice_pulled", down)
    breach_sync.sync_school(world.a.tenant_id)
    assert len(_breaches_for(admin_engine, incident)) == 1, "stored although not reported"
    monkeypatch.setattr(control_plane, "record_incident_notice_pulled", real)
    assert breach_sync.sync_school(world.a.tenant_id) == 0
    with platform_session() as s:
        pulled: object = s.execute(
            text("SELECT pulled_at FROM platform.incident_schools WHERE incident_id = :i"),
            {"i": incident},
        ).scalar_one()
    assert pulled is not None
    assert len(_breaches_for(admin_engine, incident)) == 1


def test_FR_DPDP_032_a_control_plane_outage_only_delays_the_pull(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.privacy import breach_tasks

    def down(*_: Any) -> list[Any]:
        from sqlalchemy.exc import OperationalError

        raise OperationalError("SELECT", {}, Exception("control plane unreachable"))

    monkeypatch.setattr(control_plane, "incident_notices_for_school", down)
    totals = breach_tasks.sweep_all()
    assert totals["failed"] >= 1
    assert totals["tenants"] >= 2
