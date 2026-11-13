"""Policy library with yearly review and staff acknowledgement over HTTP (FC-10;
FR-COMP-056..FR-COMP-070). Real database (RLS), synthetic data only."""

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

from app.compliance import policies

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
POLICIES, MINE = X.POLICIES, X.MINE


def _codes(res: Any) -> set[str]:
    return {e["code"] for e in res.json().get("errors", [])}


def _create(api: Any, who: Any, **body: Any) -> dict[str, Any]:
    res = api.call(who, "POST", POLICIES, json=body)
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def _ack_notices(admin: Engine, tenant_id: uuid.UUID, policy_id: str) -> set[uuid.UUID]:
    with admin.connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT recipient_membership_id FROM ops.notifications WHERE tenant_id = :t "
                    "AND template_key = 'compliance.policy.acknowledge' AND resource_id = :r"
                ),
                {"t": tenant_id, "r": policy_id},
            ).scalars()
        )


# --- authorization (FR-COMP-070) ------------------------------------------------------------


def test_FR_COMP_070_library_by_role(api: Any, school: Any, admin_engine: Engine) -> None:
    for role in S.ROLES:
        who = school.people[role]
        res = api.call(who, "GET", POLICIES)
        assert res.status_code == (200 if role in S.READERS else 403), (role, res.text)
        doc = X.policy_document(admin_engine, school)
        res = api.call(
            who,
            "POST",
            POLICIES,
            json={"title": f"Policy {role}", "document_id": str(doc), "notify": False},
        )
        assert res.status_code == (201 if role in S.MANAGERS else 403), (role, res.text)
        # Every member has their own list (implicit session.authenticated).
        assert api.call(who, "GET", MINE).status_code == 200


def test_FR_COMP_070_other_school_policy_is_404(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    other = S.make_school(admin_engine)
    pid = X.policy(admin_engine, other, roles=list(S.ROLES))
    owner = school.people["owner"]
    assert api.call(owner, "GET", f"{POLICIES}/{pid}").status_code == 404
    res = api.call(
        owner, "POST", f"{POLICIES}/{pid}:review", json={}, headers={"If-Match": 'W/"1"'}
    )
    assert res.status_code == 404
    assert api.call(owner, "POST", f"{POLICIES}/{pid}:request-acknowledgement").status_code == 404
    assert api.call(owner, "GET", f"{MINE}/{pid}/file").status_code == 404
    res = api.call(
        owner,
        "POST",
        f"{MINE}/{pid}:acknowledge",
        json={"document_version_id": str(uuid.uuid4())},
    )
    assert res.status_code == 404
    assert api.call(owner, "GET", MINE).json()["items"] == []


# --- library (FR-COMP-056..FR-COMP-059) -----------------------------------------------------


def test_FR_COMP_056_add_a_policy_and_ask_staff(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    office = school.people["office_admin"]
    doc = X.policy_document(admin_engine, school)
    out = _create(
        api,
        office,
        title="Child protection policy",
        document_id=str(doc),
        ack_roles=["teacher", "class_teacher"],
    )
    assert out["review_due"] == X.cfg_next(S.today(), 12).isoformat()
    assert out["review_state"] == "ok"
    assert out["targeted"] == 2
    assert out["acknowledged"] == 0
    assert out["current_version_id"] == str(X.current_version(admin_engine, doc))
    assert {r["role"] for r in out["ack_roles"]} == {"teacher", "class_teacher"}
    asked = _ack_notices(admin_engine, school.tenant_id, out["id"])
    assert asked == {
        school.people["teacher"].membership_id,
        school.people["class_teacher"].membership_id,
    }
    library = api.call(school.people["auditor_readonly"], "GET", POLICIES).json()
    assert library["counts"]["pending_acks"] == 2
    assert library["verified"] is False
    assert {"teacher", "owner"} <= {r["role"] for r in library["roles"]}
    (event,) = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "compliance.policy.created")
        if str(e["resource_id"]) == out["id"]
    ]
    assert event["summary"]["notified"] == 2
    assert "Child protection" not in str(event)  # titles are never audited


def test_FR_COMP_056_refused_inputs(api: Any, school: Any, admin_engine: Engine) -> None:
    office = school.people["office_admin"]
    circular = S.D.make_document(
        admin_engine, school.tenant_id, school.people["owner"].user_id, sensitivity="C1"
    )
    scanning = X.policy_document(admin_engine, school, status="scanning")
    for doc in (circular, scanning, uuid.uuid4()):
        res = api.call(
            office,
            "POST",
            POLICIES,
            json={
                "title": "Safety policy",
                "document_id": str(doc),
                "ack_roles": ["head_cook"],
                "review_due": "2020-01-01",
            },
        )
        assert res.status_code == 422, res.text
        assert {"policy_document_not_ready", "unknown_role", "review_in_past"} <= _codes(res)


def test_FR_COMP_057_update_retire_and_review(api: Any, school: Any, admin_engine: Engine) -> None:
    office = school.people["office_admin"]
    doc = X.policy_document(admin_engine, school)
    pid = X.policy(admin_engine, school, document_id=doc, review_due=S.today() + dt.timedelta(5))
    detail = api.call(office, "GET", f"{POLICIES}/{pid}")
    assert detail.headers["ETag"] == 'W/"1"'
    assert detail.json()["review_state"] == "due_soon"
    body = {
        "title": "Child protection policy 2026",
        "document_id": str(doc),
        "review_due": (S.today() + dt.timedelta(days=90)).isoformat(),
        "ack_roles": ["teacher"],
        "status": "active",
    }
    stale = api.call(office, "PUT", f"{POLICIES}/{pid}", json=body, headers={"If-Match": 'W/"7"'})
    assert stale.status_code == 412
    res = api.call(office, "PUT", f"{POLICIES}/{pid}", json=body, headers={"If-Match": 'W/"1"'})
    assert res.status_code == 200, res.text
    assert res.json()["review_state"] == "ok"
    reviewed = api.call(
        office, "POST", f"{POLICIES}/{pid}:review", json={}, headers={"If-Match": 'W/"2"'}
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["last_reviewed_on"] == S.today().isoformat()
    assert reviewed.json()["review_due"] == X.cfg_next(S.today(), 12).isoformat()
    retired = api.call(
        office,
        "PUT",
        f"{POLICIES}/{pid}",
        json={**body, "status": "retired"},
        headers={"If-Match": 'W/"3"'},
    )
    assert retired.status_code == 200, retired.text
    assert retired.json()["status"] == "retired"
    assert retired.json()["targeted"] == 0
    again = api.call(
        office, "POST", f"{POLICIES}/{pid}:review", json={}, headers={"If-Match": 'W/"4"'}
    )
    assert again.status_code == 409
    assert again.json()["code"] == "policy_retired"
    teacher = school.people["teacher"]
    assert api.call(teacher, "GET", MINE).json()["items"] == []
    assert W.audit_events(admin_engine, school.tenant_id, "compliance.policy.reviewed")


# --- acknowledgement (FR-COMP-063..FR-COMP-067) ---------------------------------------------


def test_FR_COMP_063_staff_read_and_acknowledge_the_exact_version(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    teacher, office = school.people["teacher"], school.people["office_admin"]
    doc = X.policy_document(admin_engine, school)
    pid = X.policy(admin_engine, school, roles=["teacher", "class_teacher"], document_id=doc)
    version = X.current_version(admin_engine, doc)
    mine = api.call(teacher, "GET", MINE).json()
    assert mine["pending"] == 1
    assert mine["items"][0]["needs_ack"] is True
    file = api.call(teacher, "GET", f"{MINE}/{pid}/file")
    assert file.status_code == 200, file.text
    assert file.json()["document_version_id"] == str(version)
    # Not asked of the accountant: 404 (existence not shown).
    accountant = school.people["accountant"]
    assert api.call(accountant, "GET", f"{MINE}/{pid}/file").status_code == 404
    wrong = api.call(
        teacher,
        "POST",
        f"{MINE}/{pid}:acknowledge",
        json={"document_version_id": str(uuid.uuid4())},
    )
    assert wrong.status_code == 409
    assert wrong.json()["code"] == "policy_version_changed"
    path = f"{MINE}/{pid}:acknowledge"
    res = api.call(teacher, "POST", path, json={"document_version_id": str(version)})
    assert res.status_code == 200, res.text
    assert res.json()["needs_ack"] is False
    again = api.call(teacher, "POST", path, json={"document_version_id": str(version)})
    assert again.status_code == 200
    assert len(X.acks(admin_engine, pid)) == 1
    detail = api.call(office, "GET", f"{POLICIES}/{pid}").json()
    assert detail["acknowledged"] == 1
    assert detail["targeted"] == 2
    first, second = detail["members"]
    assert first["membership_id"] == str(school.people["class_teacher"].membership_id)
    assert first["acknowledged_at"] is None  # not yet acknowledged comes first
    assert second["acknowledged_at"] is not None
    events = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "compliance.policy.acknowledged")
        if str(e["resource_id"]) == str(pid)
    ]
    assert len(events) == 1
    assert W.audit_events(admin_engine, school.tenant_id, "compliance.policy.opened")


def test_FR_COMP_065_acknowledgements_are_append_only(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    from sqlalchemy.exc import DBAPIError

    doc = X.policy_document(admin_engine, school)
    pid = X.policy(admin_engine, school, document_id=doc)
    version = X.current_version(admin_engine, doc)
    res = api.call(
        school.people["teacher"],
        "POST",
        f"{MINE}/{pid}:acknowledge",
        json={"document_version_id": str(version)},
    )
    assert res.status_code == 200, res.text
    with pytest.raises(DBAPIError, match="append-only"), admin_engine.begin() as c:
        c.execute(text("DELETE FROM ops.compliance_policy_acks WHERE policy_id = :p"), {"p": pid})


def test_FR_COMP_064_ask_again_only_those_still_to_acknowledge(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    doc = X.policy_document(admin_engine, school)
    pid = X.policy(admin_engine, school, roles=["teacher", "class_teacher"], document_id=doc)
    version = X.current_version(admin_engine, doc)
    api.call(
        school.people["teacher"],
        "POST",
        f"{MINE}/{pid}:acknowledge",
        json={"document_version_id": str(version)},
    )
    res = api.call(school.people["principal"], "POST", f"{POLICIES}/{pid}:request-acknowledgement")
    assert res.status_code == 200, res.text
    assert res.json()["notified"] == 1
    assert _ack_notices(admin_engine, school.tenant_id, str(pid)) == {
        school.people["class_teacher"].membership_id
    }


def _review_notices(admin: Engine, tenant_id: uuid.UUID, policy_id: uuid.UUID) -> list[Any]:
    with admin.connect() as c:
        return list(
            c.execute(
                text(
                    "SELECT recipient_membership_id AS membership_id, params "
                    "FROM ops.notifications WHERE tenant_id = :t "
                    "AND template_key = 'compliance.policy.review_due' AND resource_id = :r"
                ),
                {"t": tenant_id, "r": policy_id},
            )
        )


def test_FR_COMP_061_review_reminders_to_managers(school: Any, admin_engine: Engine) -> None:
    pid = X.policy(admin_engine, school, review_due=S.today() + dt.timedelta(days=20))
    assert policies.send_reminders(school.tenant_id) == 1
    sent = _review_notices(admin_engine, school.tenant_id, pid)
    recipients = {r.membership_id for r in sent}
    assert school.people["office_admin"].membership_id in recipients
    assert school.people["teacher"].membership_id not in recipients
    assert sent[0].params == {"days": 20, "state": "due"}
    assert policies.send_reminders(school.tenant_id) == 0


def test_FR_COMP_069_access_report_and_export(school: Any, api: Any, admin_engine: Engine) -> None:
    from app.compliance import service
    from app.core.db import tenant_session
    from app.core.subjects import Subject

    teacher = school.people["teacher"]
    doc = X.policy_document(admin_engine, school)
    pid = X.policy(admin_engine, school, document_id=doc)
    api.call(
        teacher,
        "POST",
        f"{MINE}/{pid}:acknowledge",
        json={"document_version_id": str(X.current_version(admin_engine, doc))},
    )
    with tenant_session(school.tenant_id) as db:
        (section,) = service.subject_report(db, Subject("member", teacher.membership_id))
        assert service.subject_report(db, Subject("student", uuid.uuid4())) == []
        tables = {t.name: t for t in service.export_records(db)}
    assert section.class_key == "policy_acknowledgements"
    assert section.rows[0][0] == "Child protection policy"
    assert str(pid) in str(tables["compliance_policy_acks"].rows)
    assert str(pid) in str(tables["compliance_policies"].rows)
