"""APAAR consent register over HTTP (ADR-0039; US-1901..US-1903; FR-APC-001..006, PRV-021,
SEC-015). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.redaction import verhoeff_check_digit

pytestmark = pytest.mark.db
A = sys.modules["sos_test_apaar_support"]
W = sys.modules["sos_test_api_world"]
BASE = "/api/v1/students/{}/apaar-consent"


def _events(admin: Engine, tenant_id: uuid.UUID, action: str, resource: uuid.UUID) -> list[Any]:
    return [e for e in W.audit_events(admin, tenant_id, action) if e["resource_id"] == resource]


# --- recording decisions (FR-APC-001..003) ---------------------------------------------------


def test_FR_APC_001_given_with_signed_form_is_appended_and_audited(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    staff = world.person("office_staff")
    sid = A.student(world.a)
    before = api.call(staff, "GET", BASE.format(sid))
    assert before.status_code == 200, before.text
    assert before.json()["status"] == "pending"
    assert before.json()["history"] == []
    assert before.headers["ETag"] == 'W/"0"'

    doc = A.signed_form(admin_engine, world.a, staff)
    res = A.record(api, staff, sid, A.given(doc), headers={"If-Match": 'W/"0"'})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "given"
    assert body["version"] == 1
    assert res.headers["ETag"] == 'W/"1"'
    current = body["current"]
    assert current["relationship"] == "mother"
    assert current["form_language"] == "te"
    assert current["evidence_document_id"] == str(doc)
    assert current["recorded_by"] == str(staff.user_id)
    assert current["recorded_by_name"]

    (row,) = A.consent_rows(admin_engine, sid)
    assert (row["seq"], row["status"], row["recorded_by_membership"]) == (
        1,
        "given",
        staff.membership_id,
    )
    (event,) = _events(admin_engine, world.a.tenant_id, "apaar.consent.recorded", sid)
    assert event["summary"]["status"] == "given"
    assert event["summary"]["previous_status"] == "pending"
    assert event["summary"]["has_form"] is True
    assert event["actor_id"] == staff.user_id


def test_FR_APC_002_history_is_kept_and_withdrawal_follows_consent(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    admin = world.person("office_admin")
    sid = A.student(world.a)
    assert A.record(api, admin, sid, A.refused()).status_code == 201
    doc = A.signed_form(admin_engine, world.a, admin)
    assert A.record(api, admin, sid, A.given(doc)).status_code == 201
    withdrawn = A.record(
        api,
        admin,
        sid,
        {"status": "withdrawn", "relationship": "mother", "decided_on": A.DECIDED_ON},
    )
    assert withdrawn.status_code == 201, withdrawn.text
    out = api.call(admin, "GET", BASE.format(sid)).json()
    assert out["status"] == "withdrawn"
    assert [h["status"] for h in out["history"]] == ["withdrawn", "given", "refused"]
    assert [r["seq"] for r in A.consent_rows(admin_engine, sid)] == [1, 2, 3]


@pytest.mark.parametrize(
    ("first", "then", "code"),
    [
        (None, "withdrawn", "consent_not_given"),
        ("refused", "withdrawn", "consent_not_given"),
        ("refused", "pending", "consent_already_decided"),
    ],
)
def test_FR_APC_002_transitions_are_refused_with_a_reason(
    world: Any, api: Any, first: str | None, then: str, code: str
) -> None:
    admin = world.person("office_admin")
    sid = A.student(world.a)
    if first is not None:
        assert A.record(api, admin, sid, A.refused()).status_code == 201
    body = {"status": then, "relationship": "father", "decided_on": A.DECIDED_ON}
    res = A.record(api, admin, sid, body)
    assert res.status_code == 409, res.text
    assert res.json()["code"] == code


def test_FR_APC_003_pending_marks_a_form_sent_home(world: Any, api: Any) -> None:
    admin = world.person("office_admin")
    sid = A.student(world.a)
    res = A.record(api, admin, sid, {"status": "pending", "note": "Form sent home with diary"})
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "pending"
    again = A.record(api, admin, sid, {"status": "pending"})
    assert again.status_code == 201, "a second reminder is fine while nobody decided"


_MOTHER = {"status": "refused", "relationship": "mother"}
_DATED = {**_MOTHER, "decided_on": A.DECIDED_ON}


@pytest.mark.parametrize(
    ("body", "field", "code"),
    [
        ({**_DATED, "status": "given"}, "evidence_document_id", "signed_form_required"),
        ({"status": "refused", "decided_on": A.DECIDED_ON}, "relationship", "missing"),
        (_MOTHER, "decided_on", "missing"),
        ({**_MOTHER, "decided_on": "2099-01-01"}, "decided_on", "date_in_future"),
        ({**_MOTHER, "decided_on": "2020-01-01"}, "decided_on", "date_too_early"),
        (
            {**_DATED, "evidence_document_id": str(uuid.uuid4())},
            "evidence_document_id",
            "not_found",
        ),
        (
            {"status": "refused", "decided_on": A.DECIDED_ON, "guardian_id": str(uuid.uuid4())},
            "guardian_id",
            "not_found",
        ),
    ],
)
def test_FR_APC_001_field_rules(
    world: Any, api: Any, body: dict[str, Any], field: str, code: str
) -> None:
    sid = A.student(world.a)
    res = A.record(api, world.person("office_admin"), sid, body)
    assert res.status_code == 422, res.text
    assert {"field": field, "code": code} in [
        {"field": e["field"], "code": e["code"]} for e in res.json()["errors"]
    ]


def test_PRV_021_note_refuses_a_full_aadhaar_number(world: Any, api: Any) -> None:
    body = "23456789012"
    aadhaar = body + verhoeff_check_digit(body)
    sid = A.student(world.a)
    res = A.record(api, world.person("office_admin"), sid, A.refused(note=f"card {aadhaar}"))
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "aadhaar_full_number_rejected"


def test_FR_APC_001_guardian_on_record_gives_the_relationship(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    sid = A.student(world.a)
    guardian = A.SW.add_guardian(world.a, sid, full_name="Synthetica Guardian Decider")
    admin = world.person("office_admin")
    res = A.record(
        api,
        admin,
        sid,
        {"status": "refused", "guardian_id": str(guardian), "decided_on": A.DECIDED_ON},
    )
    assert res.status_code == 201, res.text
    assert res.json()["current"]["relationship"] == "father"  # the link's relationship
    assert res.json()["current"]["guardian_id"] == str(guardian)
    # Data minimisation: unlinking the guardian deletes the record; the decision stays.
    version = A.SW.version(admin_engine, "sis.guardians", guardian)
    gone = api.call(
        admin,
        "DELETE",
        f"/api/v1/students/{sid}/guardians/{guardian}",
        headers={"If-Match": f'W/"{version}"'},
    )
    assert gone.status_code == 204, gone.text
    (row,) = A.consent_rows(admin_engine, sid)
    assert row["guardian_id"] is None
    assert (row["relationship"], row["status"]) == ("father", "refused")


def test_FR_APC_002_stale_if_match_and_idempotent_replay(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    admin = world.person("office_admin")
    sid = A.student(world.a)
    key = {"Idempotency-Key": str(uuid.uuid4())}
    first = A.record(api, admin, sid, A.refused(), headers=key)
    replay = A.record(api, admin, sid, A.refused(), headers=key)
    assert (first.status_code, replay.status_code) == (201, 201)
    assert replay.json() == first.json()
    assert len(A.consent_rows(admin_engine, sid)) == 1
    stale = A.record(api, admin, sid, A.refused(), headers={"If-Match": 'W/"0"'})
    assert stale.status_code == 412


def test_FR_APC_006_refusal_queues_an_apaar_readiness_recheck(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    sid = A.student(world.a)
    assert A.record(api, world.person("office_admin"), sid, A.refused()).status_code == 201
    events = A.D.outbox_events(admin_engine, world.a.tenant_id, "student.values.changed")
    assert {"student_ids": [str(sid)], "attribute_keys": ["apaar_consent"]} in events


# --- authorization, scope and tenants (SEC-003, SEC-015) -------------------------------------


@pytest.mark.parametrize(
    ("role", "read", "write"),
    [
        ("owner", 200, 403),
        ("principal", 200, 201),
        ("office_admin", 200, 201),
        ("office_staff", 200, 201),
        ("exam_coordinator", 200, 403),
        ("class_teacher", 200, 403),
        ("auditor_readonly", 200, 403),
        ("accountant", 403, 403),
        ("teacher", 403, 403),
    ],
)
def test_SEC_003_roles(world: Any, api: Any, role: str, read: int, write: int) -> None:
    sid = A.student(world.a)  # in 9A: the class teacher's section
    who = world.person(role)
    assert api.call(who, "GET", BASE.format(sid)).status_code == read
    assert A.record(api, who, sid, A.refused()).status_code == write


def test_SEC_015_class_teacher_sees_only_own_sections(world: Any, api: Any) -> None:
    teacher = world.person("class_teacher")
    mine, theirs = A.student(world.a), A.student(world.a, section_key="section_9c")
    assert api.call(teacher, "GET", BASE.format(mine)).status_code == 200
    assert api.call(teacher, "GET", BASE.format(theirs)).status_code == 404
    assert api.call(teacher, "GET", BASE.format(theirs) + "/form").status_code == 404
    listed = api.call(teacher, "GET", "/api/v1/apaar/consents", params={"limit": 200}).json()
    sections = {r["section_id"] for r in listed["data"]}
    assert sections <= {str(world.a.ids["section_9a"])}
    other = api.call(
        teacher, "GET", f"/api/v1/sections/{world.a.ids['section_9c']}/apaar-consent-forms"
    )
    assert other.status_code == 404


def test_SEC_015_other_school_ids_are_not_found(world: Any, api: Any) -> None:
    other = A.SW.ensure_students(world)["b_sb"]
    admin = world.person("office_admin")
    assert api.call(admin, "GET", BASE.format(other)).status_code == 404
    assert A.record(api, admin, other, A.refused()).status_code == 404
    assert api.call(admin, "GET", BASE.format(other) + "/form").status_code == 404
    section_b = world.b.ids["section_9a"]
    assert (
        api.call(admin, "GET", f"/api/v1/sections/{section_b}/apaar-consent-forms").status_code
        == 404
    )


def test_SEC_015_evidence_must_be_this_schools_document(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    doc_b = A.signed_form(admin_engine, world.b, world.b.people["owner"])
    sid = A.student(world.a)
    res = A.record(api, world.person("office_admin"), sid, A.given(doc_b))
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "evidence_document_id"


# --- summary and follow-up (FR-APC-005) ------------------------------------------------------


def _section_counts(api: Any, who: Any, section: uuid.UUID) -> dict[str, int]:
    out = api.call(
        who, "GET", "/api/v1/apaar/consents/summary", params={"section_id": str(section)}
    )
    assert out.status_code == 200, out.text
    (row,) = out.json()["sections"]
    counts: dict[str, int] = row["counts"]
    return counts


def test_FR_APC_005_summary_counts_and_pending_follow_up(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    admin = world.person("office_admin")
    section = world.a.ids["section_9c"]
    before = _section_counts(api, admin, section)
    a, b, c = (A.student(world.a, section_key="section_9c") for _ in range(3))
    doc = A.signed_form(admin_engine, world.a, admin)
    assert A.record(api, admin, a, A.given(doc)).status_code == 201
    assert A.record(api, admin, b, A.refused()).status_code == 201
    after = _section_counts(api, admin, section)
    delta = {k: after[k] - before[k] for k in after}
    assert delta == {"total": 3, "given": 1, "refused": 1, "pending": 1, "withdrawn": 0}

    pending = api.call(
        admin,
        "GET",
        "/api/v1/apaar/consents",
        params={"section_id": str(section), "status": "pending", "limit": 200},
    ).json()["data"]
    ids = {r["student_id"] for r in pending}
    assert str(c) in ids
    assert str(a) not in ids
    assert str(b) not in ids, "a refusal is a decision, never 'pending'"
    row = next(r for r in pending if r["student_id"] == str(c))
    assert row["class_section"] == "IX-C"
    assert row["has_form"] is False


# --- printed forms (FR-APC-004) ---------------------------------------------------------------


def test_FR_APC_004_student_form_offers_refusal_and_is_audited(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    staff = world.person("office_staff")
    sid = A.student(world.a, name="Synthetica Formprint Kumari")
    res = api.call(staff, "GET", BASE.format(sid) + "/form")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in res.headers["content-security-policy"]
    page = res.text
    assert "Synthetica Formprint Kumari" in page
    assert "I DO NOT GIVE consent" in page
    assert "I GIVE consent" in page
    assert "<script" not in page
    te = api.call(staff, "GET", BASE.format(sid) + "/form", params={"language": "te"})
    assert "నేను సమ్మతి ఇవ్వడం లేదు" in te.text
    (event, _) = _events(admin_engine, world.a.tenant_id, "apaar.consent_form.printed", sid)
    assert event["summary"]["count"] == 1
    assert event["summary"]["language"] == "en"


def test_FR_APC_004_section_forms_one_page_per_student(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    teacher = world.person("class_teacher")
    section = world.a.ids["section_9a"]
    fresh = A.student(world.a)
    res = api.call(
        teacher,
        "GET",
        f"/api/v1/sections/{section}/apaar-consent-forms",
        params={"status": "pending"},
    )
    assert res.status_code == 200, res.text
    pages = res.text.count("<article>")
    assert pages >= 1
    assert res.text.count("I DO NOT GIVE consent") == pages
    events = _events(admin_engine, world.a.tenant_id, "apaar.consent_form.printed", section)
    assert events[-1]["summary"]["count"] == pages
    assert events[-1]["summary"]["status_filter"] == "pending"
    assert fresh  # a pending student of 9A exists, so the print is never empty


# --- settings (owner decision D9) -------------------------------------------------------------


def test_D9_parent_form_language_is_chosen_per_school(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    principal = world.person("principal")
    current = api.call(principal, "GET", "/api/v1/apaar/settings")
    assert current.status_code == 200
    version = current.json()["version"]
    stale_mfa = api.call(
        principal,
        "PUT",
        "/api/v1/apaar/settings",
        json={"form_language": "te"},
        headers={"If-Match": f'W/"{version}"'},
        auth_age_s=600,
    )
    assert stale_mfa.status_code == 428
    staff = api.call(
        world.person("office_staff"),
        "PUT",
        "/api/v1/apaar/settings",
        json={"form_language": "te"},
        headers={"If-Match": f'W/"{version}"'},
    )
    assert staff.status_code == 403
    ok = api.call(
        principal,
        "PUT",
        "/api/v1/apaar/settings",
        json={"form_language": "te"},
        headers={"If-Match": f'W/"{version}"'},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["form_language"] == "te"
    sid = A.student(world.a)
    page = api.call(world.person("office_staff"), "GET", BASE.format(sid) + "/form").text
    assert 'lang="te"' in page
    back = api.call(
        principal,
        "PUT",
        "/api/v1/apaar/settings",
        json={"form_language": "en"},
        headers={"If-Match": ok.headers["ETag"]},
    )
    assert back.status_code == 200
    assert W.audit_events(admin_engine, world.a.tenant_id, "apaar.settings.updated")
