"""Legal holds over HTTP (FC-12; ADR-0059; FR-DPDP-039..FR-DPDP-052; lead decision D84).
Synthetic data only.

Placing and lifting are maker-checker between two holders of ``privacy.hold.manage`` (owner,
principal) with step-up; the reference is C3 (encrypted, never in audit, logs or access
reports); while a hold applies the erasure plan answers "held for legal reasons".
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core import holds
from app.core.db import tenant_session
from app.core.subjects import Subject
from app.privacy import service as privacy

pytestmark = pytest.mark.db
P = sys.modules["sos_test_privacy_support"]
W = sys.modules["sos_test_api_world"]
BASE = "/api/v1/privacy/holds"
REQ = "/api/v1/privacy/requests"
REFERENCE = "FIR 77/2026 Synthetica station"


def _today() -> dt.date:
    """Today in IST (the service's calendar)."""
    return dt.datetime.now(dt.UTC).astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).date()


def _body(student_id: uuid.UUID | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "scope": "student" if student_id else "record_class",
        "reason_code": "police_inquiry",
        "reference": REFERENCE,
        "review_on": (_today() + dt.timedelta(days=90)).isoformat(),
    }
    if student_id is not None:
        out["student_id"] = str(student_id)
    else:
        out["record_class"] = "breach_records"
    out.update(extra)
    return out


def _if(item: dict[str, Any]) -> dict[str, str]:
    return {"If-Match": f'W/"{item["version"]}"'}


def _place(api: Any, who: Any, body: dict[str, Any]) -> dict[str, Any]:
    res = api.call(who, "POST", BASE, json=body)
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def _active(api: Any, school: Any, body: dict[str, Any]) -> dict[str, Any]:
    pending = _place(api, school.people["principal"], body)
    res = api.call(
        school.people["owner"], "POST", f"{BASE}/{pending['id']}:approve", headers=_if(pending)
    )
    assert res.status_code == 200, res.text
    out: dict[str, Any] = res.json()
    return out


def _lift(api: Any, school: Any, hold: dict[str, Any]) -> None:
    asked = api.call(
        school.people["owner"], "POST", f"{BASE}/{hold['id']}:request-lift", headers=_if(hold)
    ).json()
    res = api.call(
        school.people["principal"], "POST", f"{BASE}/{hold['id']}:approve-lift", headers=_if(asked)
    )
    assert res.status_code == 200, res.text


def _events(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = W.audit_events(admin, tenant_id, action)
    return out


def test_FR_DPDP_042_placing_needs_a_second_person_and_is_audited(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    student = P.student(school)
    pending = _place(api, school.people["principal"], _body(student))
    assert pending["status"] == "pending"
    assert pending["effective"] is False
    assert pending["reference"] == REFERENCE
    assert pending["can_approve"] is False
    with admin_engine.connect() as c:
        blob: bytes = c.execute(
            text("SELECT reference_ciphertext FROM ops.legal_holds WHERE id = :i"),
            {"i": pending["id"]},
        ).scalar_one()
    assert REFERENCE.encode() not in bytes(blob)
    path = f"{BASE}/{pending['id']}:approve"
    own = api.call(school.people["principal"], "POST", path, headers=_if(pending))
    assert own.status_code == 403, own.text
    assert own.json()["code"] == "self_approval_forbidden"
    res = api.call(school.people["owner"], "POST", path, headers=_if(pending))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "active"
    assert res.json()["effective"] is True
    for action in ("privacy.hold.requested", "privacy.hold.placed"):
        (event,) = [
            e
            for e in _events(admin_engine, school.tenant_id, action)
            if str(e["resource_id"]) == pending["id"]
        ]
        assert event["summary"]["reason_code"] == "police_inquiry"
        assert "FIR" not in str(event["summary"])
    _lift(api, school, res.json())


def test_FR_DPDP_042_lifting_needs_a_second_person_and_holds_until_approved(
    school: Any, api: Any
) -> None:
    student = P.student(school)
    hold = _active(api, school, _body(student))
    asked = api.call(
        school.people["owner"], "POST", f"{BASE}/{hold['id']}:request-lift", headers=_if(hold)
    )
    assert asked.status_code == 200, asked.text
    assert asked.json()["status"] == "lift_pending"
    with tenant_session(school.tenant_id) as db:
        assert student in privacy.hold_scope(db, "behaviour_notes").student_ids
    path = f"{BASE}/{hold['id']}:approve-lift"
    own = api.call(school.people["owner"], "POST", path, headers=_if(asked.json()))
    assert own.status_code == 403
    stale = api.call(school.people["principal"], "POST", path, headers=_if(hold))
    assert stale.status_code == 412
    done = api.call(school.people["principal"], "POST", path, headers=_if(asked.json()))
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "lifted"
    with tenant_session(school.tenant_id) as db:
        assert student not in privacy.hold_scope(db, "behaviour_notes").student_ids


def test_FR_DPDP_042_reject_and_cancel_lift(school: Any, api: Any) -> None:
    pending = _place(api, school.people["principal"], _body(P.student(school)))
    res = api.call(
        school.people["principal"], "POST", f"{BASE}/{pending['id']}:reject", headers=_if(pending)
    )
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"
    again = api.call(
        school.people["owner"], "POST", f"{BASE}/{pending['id']}:approve", headers=_if(res.json())
    )
    assert again.status_code == 409
    assert again.json()["code"] == "hold_wrong_state"
    hold = _active(api, school, _body(P.student(school)))
    asked = api.call(
        school.people["owner"], "POST", f"{BASE}/{hold['id']}:request-lift", headers=_if(hold)
    ).json()
    back = api.call(
        school.people["principal"], "POST", f"{BASE}/{hold['id']}:cancel-lift", headers=_if(asked)
    )
    assert back.status_code == 200
    assert back.json()["status"] == "active"
    _lift(api, school, back.json())


def test_FR_DPDP_041_validation(school: Any, api: Any) -> None:
    who = school.people["principal"]
    far = (_today() + dt.timedelta(days=400)).isoformat()
    past = (_today() - dt.timedelta(days=2)).isoformat()
    cases = [
        (_body(P.student(school), review_on=far), "review_too_far"),
        (_body(P.student(school), review_on=past), "date_in_past"),
        (_body(P.student(school), reason_code="whim"), "unknown_reason_code"),
        (_body(record_class="school_structure"), "unknown_record_class"),
        (_body(record_class="no_such_class"), "unknown_record_class"),
        (_body(student_id=None, scope="student", record_class=None), "student_required"),
        (_body(uuid.uuid4()), "not_found"),
    ]
    for body, code in cases:
        res = api.call(who, "POST", BASE, json=body)
        assert res.status_code == 422, (code, res.text)
        assert code in res.text, (code, res.text)
    listed = api.call(who, "GET", BASE).json()
    assert {o["code"] for o in listed["reason_codes"]} >= {"police_inquiry", "court_case"}
    assert "school_structure" not in {o["code"] for o in listed["record_classes"]}
    assert "behaviour_notes" in {o["code"] for o in listed["record_classes"]}


def test_FR_DPDP_048_only_owner_and_principal_with_step_up(world: Any, api: Any) -> None:
    body = _body(record_class="exports")
    stale = api.call(world.person("principal"), "POST", BASE, json=body, auth_age_s=301)
    assert stale.status_code == 428
    for role in ("office_admin", "auditor_readonly", "class_teacher", "accountant"):
        assert api.call(world.person(role), "GET", BASE).status_code == 403, role
        assert api.call(world.person(role), "POST", BASE, json=body).status_code == 403, role
    for role in ("owner", "principal"):
        assert api.call(world.person(role), "GET", BASE).status_code == 200, role


def test_FR_DPDP_048_other_school_is_404(world: Any, api: Any) -> None:
    out = _place(api, world.person("principal"), _body(record_class="exports"))
    b_owner = world.b.people["owner"]
    assert api.call(b_owner, "GET", f"{BASE}/{out['id']}").status_code == 404
    res = api.call(b_owner, "POST", f"{BASE}/{out['id']}:approve", headers=_if(out))
    assert res.status_code == 404
    assert api.call(world.person("owner"), "GET", f"{BASE}/{uuid.uuid4()}").status_code == 404
    api.call(world.person("principal"), "POST", f"{BASE}/{out['id']}:reject", headers=_if(out))


def test_FR_DPDP_045_erasure_of_a_held_student_is_answered_held(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    student = P.student(school)
    hold = _active(api, school, _body(student))
    out = P.logged(api, school.people["office_admin"], P.body("erasure", student_id=student))
    plan = api.call(school.people["office_admin"], "GET", f"{REQ}/{out['id']}/erasure-plan")
    assert plan.status_code == 200, plan.text
    rows = {r["class_key"]: r for r in plan.json()["rows"]}
    assert rows["behaviour_notes"]["outcome"] == "legal_hold"
    assert "Held for legal reasons" in rows["behaviour_notes"]["reason"]
    assert "FIR" not in plan.text
    assert "police" not in plan.text.lower()
    assert rows["student_identity"]["outcome"] == "refused_official_record"
    res = api.call(
        school.people["principal"],
        "POST",
        f"{REQ}/{out['id']}/erasure",
        json={"class_keys": ["behaviour_notes"]},
    )
    assert res.status_code == 409
    assert res.json()["code"] == "class_not_erasable"
    _lift(api, school, hold)


def test_FR_DPDP_046_access_report_says_held_without_the_reason(school: Any, api: Any) -> None:
    student = P.student(school)
    hold = _active(api, school, _body(student))
    with tenant_session(school.tenant_id) as db:
        sections = privacy.subject_report(db, Subject("student", student))
    (held,) = [s for s in sections if s.class_key == "legal_holds"]
    assert held.rows == (("Yes",),)
    assert "FIR" not in str(held)
    assert "police" not in str(held).lower()
    _lift(api, school, hold)
    with tenant_session(school.tenant_id) as db:
        sections = privacy.subject_report(db, Subject("student", student))
    assert not [s for s in sections if s.class_key == "legal_holds"]


def test_FR_DPDP_043_a_class_hold_stops_the_breach_purge(school: Any, api: Any) -> None:
    hold = _active(api, school, _body(record_class="breach_records"))
    with tenant_session(school.tenant_id) as db:
        scope = holds.check(db, job="privacy.breach_purge", record_class="breach_records")
        assert scope.whole_class
        assert not holds.check(db, job="exports.purge_expired", record_class="exports").whole_class
        from app.privacy import breach_service

        assert breach_service.purge_expired(db) == 0
    _lift(api, school, hold)


def test_FR_DPDP_049_review_reminders_go_to_hold_managers(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    from app.privacy import holds_service

    hold = _active(
        api,
        school,
        _body(P.student(school), review_on=(_today() + dt.timedelta(days=3)).isoformat()),
    )
    sent = holds_service.send_review_reminders(school.tenant_id)
    assert sent >= 1
    assert holds_service.send_review_reminders(school.tenant_id) == 0, "once per period"
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT recipient_membership_id, params FROM ops.notifications "
                "WHERE tenant_id = :t AND template_key = 'privacy.hold.review_due' "
                "AND resource_id = :r"
            ),
            {"t": school.tenant_id, "r": hold["id"]},
        ).all()
    recipients = {r[0] for r in rows}
    assert school.people["owner"].membership_id in recipients
    assert school.people["principal"].membership_id in recipients
    assert school.people["office_admin"].membership_id not in recipients
    assert all("FIR" not in str(r[1]) for r in rows)
    _lift(api, school, hold)
