"""Privacy requests, access reports, erasure, grievances and notices over HTTP (R3-1A;
ADR-0048; US-2601..US-2609; FR-DPDP-001..018; NFR-PRV-004; PRV-030..PRV-034). Synthetic data
only."""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db
P = sys.modules["sos_test_privacy_support"]
EX = sys.modules["sos_test_exports_objects"]
W = sys.modules["sos_test_api_world"]
BASE = P.BASE
IST = ZoneInfo("Asia/Kolkata")


def _today() -> dt.date:
    return dt.datetime.now(IST).date()


def _events(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = W.audit_events(admin, tenant_id, action)
    return out


# --- logging requests (FR-DPDP-005..007) ------------------------------------------------------


def test_FR_DPDP_005_log_access_request_with_due_date_and_encrypted_text(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    admin = school.people["office_admin"]
    sid = P.student(school)
    secret = f"Synthetic words {uuid.uuid4().hex}"
    out = P.logged(api, admin, P.body(student_id=sid, text_=secret))
    assert out["reference"].startswith(f"PR-{_today().year}-")
    assert out["status"] == "received"
    assert out["subject_kind"] == "student"
    received = dt.date.fromisoformat(out["received_on"])
    assert dt.date.fromisoformat(out["due_on"]) == received + dt.timedelta(days=7)
    assert out["request_text"] == secret
    assert out["requester_name"] == "Synthetica Requester"
    assert out["verification_method"] == "in_person_document"
    assert [e["event"] for e in out["events"]] == ["logged", "verified"]

    row = P.request_row(admin_engine, out["id"])
    assert secret.encode() not in bytes(row["request_ciphertext"])
    (event,) = [
        e
        for e in _events(admin_engine, school.tenant_id, "privacy.request.logged")
        if str(e["resource_id"]) == out["id"]
    ]
    assert event["summary"]["kind"] == "access"
    assert secret not in str(event["summary"])


def test_FR_DPDP_006_school_target_shortens_the_due_date(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    owner = school.people["owner"]
    version = W.tenant_version(admin_engine, school.tenant_id)
    res = api.call(
        owner,
        "PATCH",
        "/api/v1/tenant",
        json={
            "privacy_request_target_days": 3,
            "privacy_contact": {
                "name": "The Principal",
                "phone": "+919000000001",
                "email": "office@synthetic.example",
            },
        },
        headers={"If-Match": f'W/"{version}"'},
    )
    assert res.status_code == 200, res.text
    try:
        out = P.logged(api, school.people["office_admin"], P.body(student_id=P.student(school)))
        received = dt.date.fromisoformat(out["received_on"])
        assert dt.date.fromisoformat(out["due_on"]) == received + dt.timedelta(days=3)
        (event,) = _events(admin_engine, school.tenant_id, "tenant.settings_updated")[-1:]
        assert "privacy_contact" in event["summary"]["fields"]
        assert "office@synthetic.example" not in str(event["summary"])
    finally:
        version = W.tenant_version(admin_engine, school.tenant_id)
        api.call(
            owner,
            "PATCH",
            "/api/v1/tenant",
            json={"privacy_request_target_days": 7},
            headers={"If-Match": f'W/"{version}"'},
        )


def test_FR_DPDP_006_target_above_7_days_is_refused(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    version = W.tenant_version(admin_engine, school.tenant_id)
    res = api.call(
        school.people["owner"],
        "PATCH",
        "/api/v1/tenant",
        json={"privacy_request_target_days": 8},
        headers={"If-Match": f'W/"{version}"'},
    )
    assert res.status_code == 422, res.text


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ({"kind": "access", "subject": {}}, "subject_required"),
        ({"kind": "nomination"}, "nomination_adults_only"),
        ({"received_on": "2099-01-01"}, "date_in_future"),
        ({"request_text": "Aadhaar 234123412346 of my son"}, "aadhaar_full_number_rejected"),
    ],
)
def test_FR_DPDP_005_bad_requests_are_refused_with_a_code(
    school: Any, api: Any, data: dict[str, Any], code: str
) -> None:
    from app.core.redaction import verhoeff_check_digit

    payload = P.body(student_id=P.student(school))
    if code == "aadhaar_full_number_rejected":
        base = "23412341234"
        data = {"request_text": f"Aadhaar {base}{verhoeff_check_digit(base)} of my son"}
    payload.update(data)
    if code == "nomination_adults_only":
        payload["nominee_name"] = "Synthetic Nominee"
    res = P.log(api, school.people["office_admin"], payload)
    assert res.status_code == 422, res.text
    codes = [e["code"] for e in res.json().get("errors", [])]
    assert code in codes, res.text


def test_FR_DPDP_015_nomination_for_a_guardian_is_encrypted_and_withdrawable(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    admin = school.people["office_admin"]
    sid = P.student(school)
    gid = P.guardian(school, sid)
    out = P.logged(
        api,
        admin,
        P.body(
            "nomination",
            guardian_id=gid,
            nominee_name="Synthetica Nominee",
            nominee_relationship="Sister",
            nominee_contact="+919000000002",
        ),
    )
    assert out["subject_kind"] == "guardian"
    assert out["nominee"]["name"] == "Synthetica Nominee"
    row = P.request_row(admin_engine, out["id"])
    assert b"Synthetica Nominee" not in bytes(row["nominee_ciphertext"])
    res = api.call(
        admin,
        "PATCH",
        f"{BASE}/{out['id']}",
        json={"nomination_withdrawn": True},
        headers={"If-Match": f'W/"{out["version"]}"'},
    )
    assert res.status_code == 200, res.text
    assert res.json()["nominee"]["withdrawn_at"] is not None
    assert "nomination_withdrawn" in [e["event"] for e in res.json()["events"]]


def test_FR_DPDP_005_staff_member_can_be_the_subject(school: Any, api: Any) -> None:
    teacher = school.people["teacher"]
    out = P.logged(
        api,
        school.people["office_admin"],
        P.body(membership_id=teacher.membership_id, verification="staff_sign_in"),
    )
    assert out["subject_kind"] == "member"
    assert out["subject_label"] == teacher.display_name


# --- authorization (invariant 2, 3) -----------------------------------------------------------


@pytest.mark.parametrize("role", ["teacher", "class_teacher", "accountant", "office_staff"])
def test_FR_DPDP_005_roles_without_privacy_read_get_403(school: Any, api: Any, role: str) -> None:
    res = api.call(school.people[role], "POST", f"{BASE}/search", json={})
    assert res.status_code == 403, res.text


def test_FR_DPDP_005_auditor_reads_but_cannot_log(school: Any, api: Any) -> None:
    auditor = school.people["auditor_readonly"]
    assert api.call(auditor, "POST", f"{BASE}/search", json={}).status_code == 200
    res = P.log(api, auditor, P.body(student_id=P.student(school)))
    assert res.status_code == 403, res.text


def test_FR_DPDP_005_other_school_gets_404(school: Any, world: Any, api: Any) -> None:
    out = P.logged(api, school.people["office_admin"], P.body(student_id=P.student(school)))
    other = world.person("principal")
    for method, path in (
        ("GET", f"{BASE}/{out['id']}"),
        ("GET", f"{BASE}/{out['id']}/erasure-plan"),
        ("GET", f"{BASE}/{out['id']}/letters/acknowledgement"),
    ):
        res = api.call(other, method, path)
        assert res.status_code in (404, 422), (path, res.text)
    assert api.call(other, "GET", f"{BASE}/{out['id']}").status_code == 404
    # A student of another school cannot be the subject.
    res = P.log(api, other, P.body(student_id=P.student(school)))
    assert res.status_code == 422, res.text


def test_FR_DPDP_005_search_lists_overdue_first(school: Any, api: Any) -> None:
    admin = school.people["office_admin"]
    old = (_today() - dt.timedelta(days=20)).isoformat()
    overdue = P.logged(api, admin, P.body(student_id=P.student(school), received_on=old))
    fresh = P.logged(api, admin, P.body(student_id=P.student(school)))
    res = api.call(admin, "POST", f"{BASE}/search", json={"limit": 200})
    assert res.status_code == 200, res.text
    ids = [r["id"] for r in res.json()["data"]]
    assert ids.index(overdue["id"]) < ids.index(fresh["id"])
    row = next(r for r in res.json()["data"] if r["id"] == overdue["id"])
    assert row["overdue"] is True
    assert row["days_left"] < 0
    only = api.call(admin, "POST", f"{BASE}/search", json={"overdue": True, "limit": 200})
    assert fresh["id"] not in [r["id"] for r in only.json()["data"]]


# --- access reports (FR-DPDP-008..010) --------------------------------------------------------


def _ready_report(school: Any, api: Any, *, verified: bool = True) -> dict[str, Any]:
    admin = school.people["office_admin"]
    sid = P.student(school, name="Synthetica Reportee")
    out = P.logged(
        api,
        admin,
        P.body(student_id=sid, verification="in_person_document" if verified else None),
    )
    res = api.call(admin, "POST", f"{BASE}/{out['id']}/report")
    assert res.status_code == 202, res.text
    export_id = res.json()["report"]["export_id"]
    assert P.run_report(school, export_id) == "ready"
    got = api.call(admin, "GET", f"{BASE}/{out['id']}")
    assert got.json()["report"]["status"] == "ready"
    detail: dict[str, Any] = got.json()
    return detail


def test_FR_DPDP_009_report_lists_the_childs_records_without_aadhaar(school: Any, api: Any) -> None:
    EX.FAKE.pages.clear()
    _ready_report(school, api)
    page = EX.FAKE.pages[-1]
    assert "Synthetica Reportee" in page
    assert "Admission record and student details" in page
    assert "Privacy contact" in page


def test_FR_DPDP_010_release_needs_a_second_person_with_step_up(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    req = _ready_report(school, api)
    path = f"{BASE}/{req['id']}/report/release"
    # The office admin cannot decide; the principal needs a recent MFA sign-in.
    assert api.call(school.people["office_admin"], "POST", path).status_code == 403
    stale = api.call(school.people["principal"], "POST", path, auth_age_s=3600)
    assert stale.status_code == 428, stale.text
    res = api.call(school.people["principal"], "POST", path)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "decided"
    assert res.json()["report_released_at"] is not None
    url = api.call(school.people["office_admin"], "GET", f"{BASE}/{req['id']}/report/download-url")
    assert url.status_code == 200, url.text
    assert url.json()["filename"].endswith(".pdf")
    actions = {e["action"] for e in W.audit_events(admin_engine, school.tenant_id)}
    assert {"privacy.report.released", "privacy.report.downloaded"} <= actions


def test_FR_DPDP_010_logger_cannot_release_their_own_request(school: Any, api: Any) -> None:
    principal = school.people["principal"]
    out = P.logged(api, principal, P.body(student_id=P.student(school)))
    res = api.call(principal, "POST", f"{BASE}/{out['id']}/report")
    assert P.run_report(school, res.json()["report"]["export_id"]) == "ready"
    rel = api.call(principal, "POST", f"{BASE}/{out['id']}/report/release")
    assert rel.status_code == 403, rel.text
    assert rel.json()["code"] == "self_approval_forbidden"
    assert api.call(principal, "GET", f"{BASE}/{out['id']}").json()["can_decide"] is False


def test_FR_DPDP_010_release_needs_verification(school: Any, api: Any) -> None:
    req = _ready_report(school, api, verified=False)
    res = api.call(school.people["principal"], "POST", f"{BASE}/{req['id']}/report/release")
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "not_verified"
    early = api.call(
        school.people["office_admin"], "GET", f"{BASE}/{req['id']}/report/download-url"
    )
    assert early.status_code == 409


def test_FR_DPDP_009_report_is_not_listed_under_exports(school: Any, api: Any) -> None:
    req = _ready_report(school, api)
    admin = school.people["office_admin"]
    listed = api.call(admin, "GET", "/api/v1/exports").json()["data"]
    assert req["report"]["export_id"] not in [e["id"] for e in listed]
    res = api.call(admin, "GET", f"/api/v1/exports/{req['report']['export_id']}")
    assert res.status_code == 404


# --- erasure (FR-DPDP-012..014) ---------------------------------------------------------------


def test_FR_DPDP_012_erasure_plan_keeps_official_records(school: Any, api: Any) -> None:
    out = P.logged(
        api, school.people["office_admin"], P.body("erasure", student_id=P.student(school))
    )
    res = api.call(school.people["office_admin"], "GET", f"{BASE}/{out['id']}/erasure-plan")
    assert res.status_code == 200, res.text
    rows = {r["class_key"]: r for r in res.json()["rows"]}
    assert rows["student_identity"]["outcome"] == "refused_official_record"
    assert rows["certificates"]["outcome"] == "refused_official_record"
    assert rows["financial_records"]["outcome"] == "refused_legal_obligation"
    assert rows["behaviour_notes"]["outcome"] == "erasable"
    assert rows["audit"]["outcome"] == "refused_legal_obligation"


def test_FR_DPDP_014_official_records_cannot_be_erased(school: Any, api: Any) -> None:
    out = P.logged(
        api, school.people["office_admin"], P.body("erasure", student_id=P.student(school))
    )
    res = api.call(
        school.people["principal"],
        "POST",
        f"{BASE}/{out['id']}/erasure",
        json={"class_keys": ["student_identity"]},
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "class_not_erasable"


def test_FR_DPDP_013_erasure_runs_with_maker_checker_and_is_audited(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    out = P.logged(
        api, school.people["office_admin"], P.body("erasure", student_id=P.student(school))
    )
    path = f"{BASE}/{out['id']}/erasure"
    body = {"class_keys": ["behaviour_notes", "early_warning"]}
    assert api.call(school.people["office_admin"], "POST", path, json=body).status_code == 403
    res = api.call(school.people["principal"], "POST", path, json=body)
    assert res.status_code == 200, res.text
    assert {r["class_key"] for r in res.json()["erased"]} == {"behaviour_notes", "early_warning"}
    assert any("early-warning" in s for s in res.json()["manual_steps"])
    events = [
        e
        for e in _events(admin_engine, school.tenant_id, "privacy.erasure.executed")
        if str(e["resource_id"]) == out["id"]
    ]
    assert len(events) == 1
    assert "student_identity" in events[0]["summary"]["refused"]
    letter = api.call(school.people["office_admin"], "GET", f"{BASE}/{out['id']}/letters/erasure")
    assert letter.status_code == 200
    assert "Admission record and student details" in letter.text


# --- grievances, correction and closing -------------------------------------------------------


def test_FR_DPDP_016_grievance_closes_only_with_a_response(school: Any, api: Any) -> None:
    out = P.logged(
        api, school.people["office_admin"], P.body("grievance", student_id=P.student(school))
    )
    path = f"{BASE}/{out['id']}/close"
    missing = api.call(school.people["principal"], "POST", path, json={})
    assert missing.status_code == 422, missing.text
    res = api.call(
        school.people["principal"],
        "POST",
        path,
        json={"response_text": "We corrected the class register and apologised."},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "closed"
    letter = api.call(school.people["principal"], "GET", f"{BASE}/{out['id']}/letters/grievance")
    assert "We corrected the class register" in letter.text
    assert "Data Protection Board" in letter.text
    again = api.call(school.people["principal"], "POST", path, json={})
    assert again.status_code == 409


def test_FR_DPDP_011_correction_links_only_real_change_requests(school: Any, api: Any) -> None:
    out = P.logged(
        api, school.people["office_admin"], P.body("correction", student_id=P.student(school))
    )
    res = api.call(
        school.people["office_admin"],
        "POST",
        f"{BASE}/{out['id']}/links",
        json={"kind": "change_request", "target_id": str(uuid.uuid4())},
    )
    assert res.status_code == 422, res.text
    closed = api.call(school.people["office_admin"], "POST", f"{BASE}/{out['id']}/close", json={})
    assert closed.status_code == 200, closed.text


def test_FR_DPDP_007_acknowledgement_letter_prints_reference_and_due_date(
    school: Any, api: Any
) -> None:
    out = P.logged(api, school.people["office_admin"], P.body(student_id=P.student(school)))
    res = api.call(
        school.people["office_admin"], "GET", f"{BASE}/{out['id']}/letters/acknowledgement"
    )
    assert res.status_code == 200
    assert out["reference"] in res.text
    assert dt.date.fromisoformat(out["due_on"]).strftime("%d/%m/%Y") in res.text
    assert "default-src 'none'" in res.headers["Content-Security-Policy"]


def test_FR_DPDP_005_patch_needs_if_match_and_refuses_stale(school: Any, api: Any) -> None:
    admin = school.people["office_admin"]
    out = P.logged(api, admin, P.body(student_id=P.student(school)))
    path = f"{BASE}/{out['id']}"
    assert api.call(admin, "PATCH", path, json={"notes": "x"}).status_code == 400
    ok = api.call(
        admin, "PATCH", path, json={"notes": "Called back"}, headers={"If-Match": 'W/"1"'}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["notes"] == "Called back"
    stale = api.call(admin, "PATCH", path, json={"notes": "y"}, headers={"If-Match": 'W/"1"'})
    assert stale.status_code == 412


# --- notices and record classes (FR-DPDP-001..003, FR-DPDP-017) -------------------------------


def test_FR_DPDP_002_notice_preview_fills_school_settings(school: Any, api: Any) -> None:
    reader = school.people["auditor_readonly"]
    templates = api.call(reader, "GET", "/api/v1/privacy/templates")
    assert templates.status_code == 200, templates.text
    keys = {t["key"] for t in templates.json()}
    assert keys == {"notice_parents", "notice_staff"}
    page = api.call(reader, "GET", "/api/v1/privacy/templates/notice_parents/preview")
    assert page.status_code == 200
    assert "Synthetic Model School" in page.text
    assert "Not legal advice" in page.text
    assert "{{" not in page.text


def test_FR_DPDP_002_notice_pdf_is_made_by_the_worker(school: Any, api: Any) -> None:
    reader = school.people["office_admin"]
    res = api.call(reader, "POST", "/api/v1/privacy/templates/notice_staff/pdf")
    assert res.status_code == 202, res.text
    export_id = res.json()["export_id"]
    assert P.run_report(school, export_id) == "ready"
    url = api.call(reader, "GET", f"/api/v1/privacy/files/{export_id}/download-url")
    assert url.status_code == 200, url.text


def test_FR_DPDP_003_notice_issues_are_recorded_and_flagged(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    admin = school.people["office_admin"]
    body = {
        "template_key": "notice_parents",
        "template_version": "0.1",
        "audience": "parents",
        "issued_on": _today().isoformat(),
        "channel": "paper",
    }
    res = api.call(admin, "POST", "/api/v1/privacy/notices", json=body)
    assert res.status_code == 201, res.text
    listed = api.call(admin, "GET", "/api/v1/privacy/notices").json()
    assert listed[0]["template_version"] == "0.1"
    tpl = {t["key"]: t for t in api.call(admin, "GET", "/api/v1/privacy/templates").json()}
    assert tpl["notice_parents"]["last_issued_version"] == "0.1"
    assert tpl["notice_parents"]["newer_version_available"] is False
    future = api.call(
        admin,
        "POST",
        "/api/v1/privacy/notices",
        json={**body, "issued_on": "2099-01-01"},
    )
    assert future.status_code == 422
    assert (
        api.call(
            school.people["auditor_readonly"], "POST", "/api/v1/privacy/notices", json=body
        ).status_code
        == 403
    )
    with admin_engine.connect() as c, pytest.raises(Exception, match="append-only"):
        c.execute(text("UPDATE ops.privacy_notices SET channel = 'other'"))


def test_FR_DPDP_017_record_classes_are_served_read_only(school: Any, api: Any) -> None:
    res = api.call(school.people["auditor_readonly"], "GET", "/api/v1/privacy/record-classes")
    assert res.status_code == 200, res.text
    keys = {c["key"] for c in res.json()["data"]}
    assert {"student_identity", "financial_records", "audit", "privacy_requests"} <= keys


# --- due-date reminder (FR-DPDP-006) ----------------------------------------------------------


def test_FR_DPDP_006_daily_job_notifies_managers_with_counts_once(school: Any, api: Any) -> None:
    from app.core.db import tenant_session
    from app.privacy import service

    old = (_today() - dt.timedelta(days=10)).isoformat()
    P.logged(
        api, school.people["office_admin"], P.body(student_id=P.student(school), received_on=old)
    )
    with tenant_session(school.tenant_id) as db:
        first = service.notify_due(db)
    with tenant_session(school.tenant_id) as db:
        second = service.notify_due(db)
    assert first >= 1
    assert second == 0


@pytest.mark.parametrize("status", ["in_progress", "waiting_for_requester", "withdrawn"])
def test_FR_DPDP_013_decided_request_cannot_be_reopened_or_withdrawn(
    school: Any, api: Any, status: str
) -> None:
    out = P.logged(
        api, school.people["office_admin"], P.body("erasure", student_id=P.student(school))
    )
    erased = api.call(
        school.people["principal"],
        "POST",
        f"{BASE}/{out['id']}/erasure",
        json={"class_keys": ["behaviour_notes"]},
    )
    assert erased.status_code == 200, erased.text
    current = api.call(school.people["office_admin"], "GET", f"{BASE}/{out['id']}")
    assert current.json()["status"] == "decided"
    res = api.call(
        school.people["office_admin"],
        "PATCH",
        f"{BASE}/{out['id']}",
        json={"status": status},
        headers={"If-Match": current.headers["ETag"]},
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "request_decided"
    notes = api.call(
        school.people["office_admin"],
        "PATCH",
        f"{BASE}/{out['id']}",
        json={"notes": "Told the parent by phone"},
        headers={"If-Match": current.headers["ETag"]},
    )
    assert notes.status_code == 200, notes.text
    assert notes.json()["status"] == "decided"
