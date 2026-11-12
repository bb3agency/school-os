"""Safety drills and periodic checks over HTTP (FC-09; FR-COMP-046..FR-COMP-055). Real database
(RLS), synthetic data only."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.compliance import checks

pytestmark = pytest.mark.db
S = sys.modules["sos_test_compliance_support"]
W = S.W


def _load() -> ModuleType:
    name = "sos_test_compliance_safety_support"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("safety_support.py")
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


X = _load()
CHECKS = X.CHECKS


def _codes(res: Any) -> set[str]:
    return {e["code"] for e in res.json().get("errors", [])}


def _status(overview: dict[str, Any], kind: str) -> dict[str, Any]:
    (row,) = [s for s in overview["statuses"] if s["kind"] == kind]
    out: dict[str, Any] = row
    return out


def test_FR_COMP_054_read_and_manage_by_role(api: Any, school: Any) -> None:
    for role in S.ROLES:
        who = school.people[role]
        res = api.call(who, "GET", CHECKS)
        assert res.status_code == (200 if role in S.READERS else 403), (role, res.text)
        res = api.call(
            who,
            "POST",
            f"{CHECKS}/entries",
            json={"kind": "first_aid_check", "done_on": S.today().isoformat()},
        )
        assert res.status_code == (201 if role in S.MANAGERS else 403), (role, res.text)


def test_FR_COMP_054_other_school_entry_is_404(api: Any, school: Any, admin_engine: Engine) -> None:
    other = S.make_school(admin_engine)
    cid = X.check(admin_engine, other)
    owner = school.people["owner"]
    res = api.call(owner, "POST", f"{CHECKS}/entries/{cid}:remove", headers={"If-Match": 'W/"1"'})
    assert res.status_code == 404
    assert api.call(owner, "GET", f"{CHECKS}/entries").json()["items"] == []


def test_FR_COMP_048_log_a_drill_and_see_the_next_due_date(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    office = school.people["office_admin"]
    before = api.call(office, "GET", CHECKS).json()
    assert _status(before, "fire_drill")["state"] == "never"
    assert before["verified"] is False
    assert "cctv_check" not in {s["kind"] for s in before["statuses"]}  # AP State Board school
    assert before["leaders"]
    done = S.today() - dt.timedelta(days=10)
    res = api.call(
        office,
        "POST",
        f"{CHECKS}/entries",
        json={
            "kind": "fire_drill",
            "done_on": done.isoformat(),
            "participants_count": 412,
            "duration_minutes": 6,
            "result": "action_needed",
            "observations": "Stair B exit was slow; ask the caretaker to clear it.",
            "led_by_membership_id": str(school.people["principal"].membership_id),
        },
    )
    assert res.status_code == 201, res.text
    entry = res.json()
    assert res.headers["ETag"] == 'W/"1"'
    assert entry["kind_label"] == "Fire evacuation drill"
    assert entry["led_by"]["membership_id"] == str(school.people["principal"].membership_id)
    after = api.call(school.people["auditor_readonly"], "GET", CHECKS).json()
    status = _status(after, "fire_drill")
    assert status["state"] == "ok"
    assert status["last_done_on"] == done.isoformat()
    assert status["next_due"] == X.cfg_next(done, 4).isoformat()
    assert after["recent"][0]["id"] == entry["id"]
    assert after["leaders"] == []  # not a manager
    (event,) = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "compliance.check.recorded")
        if str(e["resource_id"]) == entry["id"]
    ]
    assert event["summary"]["participants_count"] == 412
    assert "caretaker" not in str(event)  # typed notes are never audited


def test_FR_COMP_048_refused_inputs(api: Any, school: Any, admin_engine: Engine) -> None:
    office = school.people["office_admin"]
    other = S.make_school(admin_engine)
    foreign = S.document(admin_engine, other)
    left = W.add_member(admin_engine, school.tenant_id, ["teacher"], status="suspended")
    res = api.call(
        office,
        "POST",
        f"{CHECKS}/entries",
        json={
            "kind": "electrical_check",
            "done_on": (S.today() + dt.timedelta(days=2)).isoformat(),
            "participants_count": 10,
            "document_id": str(foreign),
            "led_by_membership_id": str(left.membership_id),
            "label": "Wiring",
        },
    )
    assert res.status_code == 422, res.text
    assert {
        "done_in_future",
        "drill_fields_not_allowed",
        "document_not_found",
        "leader_not_active",
        "label_not_allowed",
    } <= _codes(res)
    res = api.call(
        office, "POST", f"{CHECKS}/entries", json={"kind": "other", "done_on": "2026-11-01"}
    )
    assert "label_required" in _codes(res)
    res = api.call(
        office, "POST", f"{CHECKS}/entries", json={"kind": "lift_check", "done_on": "2026-11-01"}
    )
    assert "unknown_kind" in _codes(res)


def test_FR_COMP_049_remove_an_entry_logged_by_mistake(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    office = school.people["office_admin"]
    cid = X.check(admin_engine, school, kind="water_test")
    path = f"{CHECKS}/entries/{cid}:remove"
    assert api.call(office, "POST", path, headers={"If-Match": 'W/"9"'}).status_code == 412
    res = api.call(office, "POST", path, headers={"If-Match": 'W/"1"'})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "removed"
    again = api.call(office, "POST", path, headers={"If-Match": 'W/"2"'})
    assert again.status_code == 409
    assert again.json()["code"] == "check_removed"
    overview = api.call(office, "GET", CHECKS).json()
    assert _status(overview, "water_test")["state"] == "never"
    assert W.audit_events(admin_engine, school.tenant_id, "compliance.check.removed")


def _notices(admin: Engine, tenant_id: uuid.UUID, resource_id: uuid.UUID) -> list[Any]:
    with admin.connect() as c:
        return list(
            c.execute(
                text(
                    "SELECT recipient_membership_id AS membership_id, params "
                    "FROM ops.notifications WHERE tenant_id = :t "
                    "AND template_key = 'compliance.check.due' AND resource_id = :r"
                ),
                {"t": tenant_id, "r": resource_id},
            )
        )


def test_FR_COMP_051_reminders_to_managers_once_per_lead_day(
    school: Any, admin_engine: Engine
) -> None:
    # Done 4 months ago less 5 days: the drill falls due in about 5 days (lead day 7).
    done = X.cfg_next(S.today() + dt.timedelta(days=5), -4)
    cid = X.check(admin_engine, school, kind="fire_drill", done_on=done)
    X.check(admin_engine, school, kind="other", label="Lift check", done_on=done)
    assert checks.send_reminders(school.tenant_id) == 1
    sent = _notices(admin_engine, school.tenant_id, cid)
    recipients = {r.membership_id for r in sent}
    assert school.people["principal"].membership_id in recipients
    assert school.people["teacher"].membership_id not in recipients
    assert sent[0].params["check"] == "Fire evacuation drill"
    assert sent[0].params["state"] == "due"
    assert checks.send_reminders(school.tenant_id) == 0
    (event,) = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "compliance.check.reminded")
        if str(e["resource_id"]) == str(cid)
    ]
    assert event["summary"]["lead_day"] == 7


def test_FR_COMP_052_overdue_reminder_and_never_done_kinds_stay_quiet(
    school: Any, admin_engine: Engine
) -> None:
    cid = X.check(
        admin_engine, school, kind="first_aid_check", done_on=S.today() - dt.timedelta(days=120)
    )
    assert checks.send_reminders(school.tenant_id) == 1  # only the logged kind
    notices = _notices(admin_engine, school.tenant_id, cid)
    assert notices
    assert all(n.params["state"] == "overdue" for n in notices)
    # A newer entry of the kind restarts: nothing due.
    X.check(admin_engine, school, kind="first_aid_check", done_on=S.today())
    assert checks.send_reminders(school.tenant_id) == 0


def test_FR_COMP_055_log_is_in_the_full_export(school: Any, admin_engine: Engine) -> None:
    from app.compliance import service
    from app.core.db import tenant_session

    cid = X.check(admin_engine, school)
    with tenant_session(school.tenant_id) as db:
        tables = {t.name: t for t in service.export_records(db)}
    assert str(cid) in str(tables["compliance_checks"].rows)
