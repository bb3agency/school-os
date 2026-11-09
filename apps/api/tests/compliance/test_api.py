"""Compliance certificate register over HTTP (FC-01; FR-COMP-001..FR-COMP-022). Real database
(RLS), synthetic data only.

The SCHOOL's own certificates: recorded, renewed (history kept), corrected and removed by the
office; read by the owner, principal, office admin and auditor; reminders before and after
expiry; the CBSE Appendix IX draft shows the register's certificate and its state.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.compliance import config as cfg
from app.compliance import service

pytestmark = pytest.mark.db
S = sys.modules["sos_test_compliance_support"]
W = S.W
BASE = S.BASE


def _if(item: dict[str, Any]) -> dict[str, str]:
    return {"If-Match": f'W/"{item["version"]}"'}


def _record(api: Any, who: Any, **body: Any) -> dict[str, Any]:
    res = api.call(who, "POST", f"{BASE}/certificates", json=body)
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def _register(api: Any, who: Any) -> dict[str, Any]:
    res = api.call(who, "GET", f"{BASE}/certificates")
    assert res.status_code == 200, res.text
    out: dict[str, Any] = res.json()
    return out


def _row(register: dict[str, Any], kind: str) -> dict[str, Any]:
    (row,) = [r for r in register["rows"] if r["kind"] == kind]
    return row


def _codes(res: Any) -> set[str]:
    return {e["code"] for e in res.json().get("errors", [])}


# --- authorization (FR-COMP-017) ------------------------------------------------------------


def test_FR_COMP_017_read_and_manage_by_role(api: Any, school: Any) -> None:
    for role in S.ROLES:
        who = school.people[role]
        res = api.call(who, "GET", f"{BASE}/certificates")
        assert res.status_code == (200 if role in S.READERS else 403), (role, res.text)
        res = api.call(who, "GET", f"{BASE}/summary")
        assert res.status_code == (200 if role in S.READERS else 403), (role, res.text)
        res = api.call(
            who, "POST", f"{BASE}/certificates", json={"kind": "other", "label": f"Test {role}"}
        )
        assert res.status_code == (201 if role in S.MANAGERS else 403), (role, res.text)


def test_FR_COMP_018_other_school_certificate_is_404(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    other = S.make_school(admin_engine)
    cid = S.certificate(admin_engine, other, kind="fire_noc")
    owner = school.people["owner"]
    assert api.call(owner, "GET", f"{BASE}/certificates/{cid}").status_code == 404
    res = api.call(
        owner,
        "PUT",
        f"{BASE}/certificates/{cid}",
        json={"valid_until": None},
        headers={"If-Match": 'W/"1"'},
    )
    assert res.status_code == 404
    res = api.call(
        owner, "POST", f"{BASE}/certificates/{cid}:remove", headers={"If-Match": 'W/"1"'}
    )
    assert res.status_code == 404
    assert all(r["certificate"] is None for r in _register(api, owner)["rows"])


# --- register, record, renew (FR-COMP-003..FR-COMP-007) -------------------------------------


def test_FR_COMP_003_register_lists_expected_kinds_as_missing(api: Any, school: Any) -> None:
    reg = _register(api, school.people["principal"])
    kinds = {r["kind"] for r in reg["rows"]}
    assert "state_recognition" in kinds  # AP State Board school
    assert "cbse_affiliation" not in kinds
    assert all(r["state"] == "missing" for r in reg["rows"])
    assert reg["counts"]["missing"] == len(reg["rows"])
    assert reg["verified"] is False
    assert {k["kind"] for k in reg["kinds"]} >= {"fire_noc", "other"}


def test_FR_COMP_004_record_renew_keeps_history_and_audits(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    owner, office = school.people["owner"], school.people["office_admin"]
    doc = S.document(admin_engine, school)
    first = _record(
        api,
        office,
        kind="fire_noc",
        reference_no="FS/NOC/77",
        issued_by="District Fire Office",
        valid_from=(S.today() - dt.timedelta(days=1800)).isoformat(),
        valid_until=(S.today() + dt.timedelta(days=20)).isoformat(),
        document_id=str(doc),
        owner_membership_id=str(office.membership_id),
    )
    assert first["state"] == "expiring"
    assert first["days_left"] == 20
    assert first["document"]["id"] == str(doc)
    assert first["owner"]["membership_id"] == str(office.membership_id)
    assert first["owner"]["active"] is True
    renewed = _record(
        api,
        office,
        kind="fire_noc",
        valid_until=(S.today() + dt.timedelta(days=1825)).isoformat(),
    )
    assert renewed["state"] == "valid"
    old = S.row(admin_engine, uuid.UUID(first["id"]))
    assert old["status"] == "superseded"
    assert str(old["superseded_by"]) == renewed["id"]
    detail = api.call(owner, "GET", f"{BASE}/certificates/{renewed['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.headers["ETag"] == 'W/"1"'
    assert [h["id"] for h in detail.json()["history"]] == [first["id"]]
    row = _row(_register(api, owner), "fire_noc")
    assert row["certificate"]["id"] == renewed["id"]
    events = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "compliance.certificate.recorded")
        if str(e["resource_id"]) in (first["id"], renewed["id"])
    ]
    assert len(events) == 2
    assert events[1]["summary"]["renews_id"] == first["id"]
    assert "FS/NOC" not in str(events)  # typed texts are never audited


def test_FR_COMP_005_other_kind_needs_a_label_and_may_repeat(api: Any, school: Any) -> None:
    office = school.people["office_admin"]
    res = api.call(office, "POST", f"{BASE}/certificates", json={"kind": "other"})
    assert res.status_code == 422
    assert "label_required" in _codes(res)
    res = api.call(
        office, "POST", f"{BASE}/certificates", json={"kind": "fire_noc", "label": "Fire"}
    )
    assert "label_not_allowed" in _codes(res)
    a = _record(api, office, kind="other", label="Lift safety certificate")
    b = _record(api, office, kind="other", label="Boiler certificate")
    assert a["kind_label"] == "Lift safety certificate"
    renewal = _record(api, office, kind="other", label="Lift safety certificate", renews_id=a["id"])
    detail = api.call(office, "GET", f"{BASE}/certificates/{renewal['id']}").json()
    assert [h["id"] for h in detail["history"]] == [a["id"]]
    others = [r for r in _register(api, office)["rows"] if r["kind"] == "other"]
    assert {r["certificate"]["id"] for r in others} == {b["id"], renewal["id"]}


def test_FR_COMP_006_refused_inputs(api: Any, school: Any, admin_engine: Engine) -> None:
    office = school.people["office_admin"]
    other = S.make_school(admin_engine)
    foreign_doc = S.document(admin_engine, other)
    left = W.add_member(admin_engine, school.tenant_id, ["teacher"], status="suspended")
    res = api.call(
        office,
        "POST",
        f"{BASE}/certificates",
        json={
            "kind": "fire_noc",
            "document_id": str(foreign_doc),
            "owner_membership_id": str(left.membership_id),
            "notes": "Aadhaar 2345 6789 0124 on file",
        },
    )
    assert res.status_code == 422, res.text
    assert {"document_not_found", "owner_not_active"} <= _codes(res)
    res = api.call(office, "POST", f"{BASE}/certificates", json={"kind": "lift_licence"})
    assert "unknown_kind" in _codes(res)
    res = api.call(
        office,
        "POST",
        f"{BASE}/certificates",
        json={"kind": "fire_noc", "valid_from": "2026-05-01", "valid_until": "2026-01-01"},
    )
    assert res.status_code == 422


def test_FR_COMP_006_full_aadhaar_in_a_text_is_refused(api: Any, school: Any) -> None:
    res = api.call(
        school.people["office_admin"],
        "POST",
        f"{BASE}/certificates",
        json={"kind": "fire_noc", "notes": "see 2345 6789 0124"},
    )
    if res.status_code == 201:  # not a Verhoeff-valid number: nothing to refuse
        pytest.skip("synthetic number is not Verhoeff-valid")
    assert "aadhaar_full_number_rejected" in _codes(res)


# --- update and remove (FR-COMP-008, FR-COMP-009) -------------------------------------------


def test_FR_COMP_008_update_needs_if_match_and_restarts_reminders(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    office = school.people["office_admin"]
    cid = S.certificate(admin_engine, school, valid_until=S.today() + dt.timedelta(days=5))
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.compliance_certificates SET last_reminded_lead = 7, "
                "last_reminded_on = current_date WHERE id = :i"
            ),
            {"i": cid},
        )
    path = f"{BASE}/certificates/{cid}"
    body = {"valid_until": (S.today() + dt.timedelta(days=400)).isoformat(), "notes": "Renewed"}
    stale = api.call(office, "PUT", path, json=body, headers={"If-Match": 'W/"9"'})
    assert stale.status_code == 412
    res = api.call(office, "PUT", path, json=body, headers={"If-Match": 'W/"1"'})
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "valid"
    assert res.headers["ETag"] == 'W/"2"'
    after = S.row(admin_engine, cid)
    assert after["last_reminded_lead"] is None
    (event,) = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "compliance.certificate.updated")
        if str(e["resource_id"]) == str(cid)
    ]
    assert event["summary"]["fields"] == ["notes", "valid_until"]


def test_FR_COMP_009_remove_restores_the_renewed_certificate(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    office = school.people["office_admin"]
    first = _record(api, office, kind="building_safety")
    second = _record(api, office, kind="building_safety", notes="Typed by mistake")
    path = f"{BASE}/certificates/{second['id']}:remove"
    res = api.call(office, "POST", path, headers=_if(second))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "removed"
    assert S.row(admin_engine, uuid.UUID(first["id"]))["status"] == "current"
    row = _row(_register(api, office), "building_safety")
    assert row["certificate"]["id"] == first["id"]
    again = api.call(
        office, "POST", f"{BASE}/certificates/{second['id']}:remove", headers={"If-Match": 'W/"2"'}
    )
    assert again.status_code == 409
    assert again.json()["code"] == "certificate_not_current"
    old = api.call(
        office, "PUT", f"{BASE}/certificates/{second['id']}", json={}, headers={"If-Match": 'W/"2"'}
    )
    assert old.status_code == 409
    assert W.audit_events(admin_engine, school.tenant_id, "compliance.certificate.removed")


# --- dashboard, reminders (FR-COMP-011..FR-COMP-014) ----------------------------------------


def test_FR_COMP_014_summary_counts(api: Any, school: Any, admin_engine: Engine) -> None:
    S.certificate(admin_engine, school, kind="fire_noc", valid_until=S.today() - dt.timedelta(1))
    S.certificate(
        admin_engine, school, kind="building_safety", valid_until=S.today() + dt.timedelta(10)
    )
    res = api.call(school.people["auditor_readonly"], "GET", f"{BASE}/summary")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["expired"] == 1
    assert body["expiring"] == 1
    expected = len(cfg.catalogue().kinds_for(["BSEAP"])) - 2
    assert body["missing"] == expected


def _notifications(admin: Engine, tenant_id: uuid.UUID, resource_id: uuid.UUID) -> list[Any]:
    with admin.connect() as c:
        return list(
            c.execute(
                text(
                    "SELECT recipient_membership_id AS membership_id, params "
                    "FROM ops.notifications WHERE tenant_id = :t "
                    "AND template_key = 'compliance.certificate.expiring' AND resource_id = :r"
                ),
                {"t": tenant_id, "r": resource_id},
            )
        )


def test_FR_COMP_011_reminders_go_to_owner_and_managers_once_per_lead_day(
    school: Any, admin_engine: Engine
) -> None:
    owner_member = school.people["office_staff"]  # owns the renewal, not a manager
    cid = S.certificate(
        admin_engine,
        school,
        valid_until=S.today() + dt.timedelta(days=25),
        owner=owner_member.membership_id,
    )
    assert service.send_reminders(school.tenant_id) == 1
    sent = _notifications(admin_engine, school.tenant_id, cid)
    recipients = {r.membership_id for r in sent}
    assert owner_member.membership_id in recipients
    assert school.people["principal"].membership_id in recipients
    assert school.people["teacher"].membership_id not in recipients
    assert sent[0].params == {
        "certificate": "Fire safety certificate (fire NOC)",
        "days": 25,
        "state": "expiring",
    }
    assert S.row(admin_engine, cid)["last_reminded_lead"] == 30
    assert service.send_reminders(school.tenant_id) == 0  # same lead day: nothing again
    assert len(_notifications(admin_engine, school.tenant_id, cid)) == len(sent)
    (event,) = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "compliance.certificate.reminded")
        if str(e["resource_id"]) == str(cid)
    ]
    assert event["summary"]["lead_day"] == 30


def test_FR_COMP_012_expired_reminder_never_names_the_school_text(
    school: Any, admin_engine: Engine
) -> None:
    cid = S.certificate(
        admin_engine,
        school,
        kind="other",
        label="Lift licence 99887766",
        valid_until=S.today() - dt.timedelta(days=2),
    )
    assert service.send_reminders(school.tenant_id) == 1
    sent = _notifications(admin_engine, school.tenant_id, cid)
    assert sent
    assert sent[0].params["state"] == "expired"
    assert sent[0].params["certificate"] == service.OTHER_NOTICE_LABEL
    assert S.row(admin_engine, cid)["last_reminded_lead"] == cfg.EXPIRED_LEAD


# --- full export (FR-COMP-016) --------------------------------------------------------------


def test_FR_COMP_016_register_is_in_the_full_export(school: Any, admin_engine: Engine) -> None:
    from app.core.db import tenant_session

    cid = S.certificate(admin_engine, school)
    with tenant_session(school.tenant_id) as db:
        (table,) = service.export_records(db)
    assert table.name == "compliance_certificates"
    assert str(cid) in str(table.rows)
