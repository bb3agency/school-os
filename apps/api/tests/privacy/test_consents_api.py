"""Consents for optional purposes over HTTP (R3-1B; ADR-0049; US-2701..US-2703;
FR-DPDP-020..026, PRV-006, SEC-015). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.redaction import verhoeff_check_digit
from app.privacy import consents_service
from app.tenancy import service as tenancy_service

pytestmark = pytest.mark.db
P = sys.modules["sos_test_consent_support"]
W = sys.modules["sos_test_api_world"]
BASE = "/api/v1/students/{}/consents"


def _events(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[Any]:
    events: list[Any] = W.audit_events(admin, tenant_id, action)
    return events


def _state(view: dict[str, Any], purpose_id: uuid.UUID) -> dict[str, Any]:
    found: dict[str, Any] = next(p for p in view["purposes"] if p["purpose_id"] == str(purpose_id))
    return found


# --- purposes (FR-DPDP-020) -------------------------------------------------------------------


def test_FR_DPDP_020_suggestions_are_listed_and_enabled_with_step_up(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    pid = P.shared_purpose(api, world.a, admin_engine)
    listed = api.call(world.person("office_staff"), "GET", "/api/v1/consent-purposes")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert {s["key"] for s in body["suggestions"]} >= {
        "photos_school_media",
        "name_results_public",
        "share_outside_organiser",
    }
    assert next(s for s in body["suggestions"] if s["key"] == P.SHARED_KEY)["enabled"] is True
    assert str(pid) in {p["id"] for p in body["data"]}
    stale = api.call(
        owner,
        "POST",
        "/api/v1/consent-purposes",
        json={"suggested_key": "name_results_public"},
        auth_age_s=301,
    )
    assert stale.status_code == 428
    office = api.call(
        world.person("office_admin"),
        "POST",
        "/api/v1/consent-purposes",
        json={"suggested_key": "name_results_public"},
    )
    assert office.status_code == 403
    again = api.call(
        owner, "POST", "/api/v1/consent-purposes", json={"suggested_key": P.SHARED_KEY}
    )
    assert again.status_code == 409
    assert again.json()["code"] == "purpose_exists"


def test_FR_DPDP_020_editing_an_answered_purpose_makes_a_new_version(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner, staff = world.person("owner"), world.person("office_staff")
    purpose = P.own_purpose(api, world.a, admin_engine)
    pid = uuid.UUID(purpose["id"])
    path = f"/api/v1/consent-purposes/{pid}"
    # Not answered yet: the text changes in place.
    first = api.call(
        owner,
        "PATCH",
        path,
        json={"label_en": "Name and class in the school magazine"},
        headers={"If-Match": 'W/"1"'},
    )
    assert first.status_code == 200, first.text
    assert first.json()["form_version"] == 1
    sid = P.student(world.a)
    assert P.record(api, staff, sid, P.refused(pid)).status_code == 201
    changed = api.call(
        owner,
        "PATCH",
        path,
        json={"description": "Your child's name and class in the printed magazine, one year."},
        headers={"If-Match": first.headers["ETag"]},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["form_version"] == 2
    rows = P.consent_rows(admin_engine, sid)
    assert [r["form_version"] for r in rows] == [1], "earlier answers stay with version 1"
    stale = api.call(owner, "PATCH", path, json={"active": False}, headers={"If-Match": 'W/"1"'})
    assert stale.status_code == 412
    (event,) = [
        e
        for e in _events(admin_engine, world.a.tenant_id, "consent.purpose.updated")
        if e["resource_id"] == pid and e["summary"]["form_version"] == 2
    ]
    assert event["summary"]["fields"] == ["description", "form_version"]
    assert "magazine" not in str(event["summary"])


def test_FR_DPDP_020_the_database_freezes_answered_text(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    purpose = P.own_purpose(api, world.a, admin_engine)
    pid = uuid.UUID(purpose["id"])
    sid = P.student(world.a)
    assert P.record(api, world.person("office_staff"), sid, P.refused(pid)).status_code == 201
    with (
        pytest.raises(Exception, match="consent_purposes_frozen"),
        tenant_session(world.a.tenant_id) as db,
    ):
        db.execute(
            text("UPDATE sis.consent_purposes SET label_en = 'Changed' WHERE id = :i"),
            {"i": pid},
        )


# --- decisions (FR-DPDP-021, FR-DPDP-022) -----------------------------------------------------


def test_FR_DPDP_021_given_needs_the_signed_form_and_history_is_append_only(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    staff = world.person("office_staff")
    pid = P.shared_purpose(api, world.a, admin_engine)
    sid = P.student(world.a)
    before = api.call(staff, "GET", BASE.format(sid))
    assert before.status_code == 200, before.text
    assert _state(before.json(), pid)["answer"] == "not_answered"
    version = before.headers["ETag"]
    no_form = P.record(
        api, staff, sid, {**P.given(pid, uuid.uuid4()), "evidence_document_id": None}
    )
    assert no_form.status_code == 422
    assert "signed_form_required" in no_form.text
    doc = P.signed_form(admin_engine, world.a, staff)
    res = P.record(api, staff, sid, P.given(pid, doc), headers={"If-Match": version})
    assert res.status_code == 201, res.text
    state = _state(res.json(), pid)
    assert (state["answer"], state["state"]) == ("given", "given")
    withdrawn = P.record(
        api,
        staff,
        sid,
        {
            "purpose_id": str(pid),
            "status": "withdrawn",
            "relationship": "mother",
            "decided_on": P.DECIDED_ON,
        },
    )
    assert withdrawn.status_code == 201, withdrawn.text
    out = _state(withdrawn.json(), pid)
    assert [h["status"] for h in out["history"]] == ["withdrawn", "given"]
    assert out["state"] == "not_given"
    with (
        pytest.raises(Exception, match="permission denied"),
        tenant_session(world.a.tenant_id) as db,
    ):
        db.execute(text("DELETE FROM sis.consents WHERE student_id = :s"), {"s": sid})
    (event,) = [
        e
        for e in _events(admin_engine, world.a.tenant_id, "consent.recorded")
        if e["resource_id"] == sid and e["summary"]["status"] == "given"
    ]
    assert event["summary"]["purpose_id"] == str(pid)
    assert event["summary"]["has_form"] is True


@pytest.mark.parametrize(
    ("first", "then", "code"),
    [
        (None, "withdrawn", "consent_not_given"),
        ("refused", "withdrawn", "consent_not_given"),
        ("refused", "pending", "consent_already_decided"),
    ],
)
def test_FR_DPDP_021_transitions_are_refused_with_a_reason(  # noqa: PLR0917
    world: Any, api: Any, admin_engine: Engine, first: str | None, then: str, code: str
) -> None:
    admin = world.person("office_admin")
    pid = P.shared_purpose(api, world.a, admin_engine)
    sid = P.student(world.a)
    if first is not None:
        assert P.record(api, admin, sid, P.refused(pid)).status_code == 201
    body = {
        "purpose_id": str(pid),
        "status": then,
        "relationship": "father",
        "decided_on": P.DECIDED_ON,
    }
    res = P.record(api, admin, sid, body)
    assert res.status_code == 409, res.text
    assert res.json()["code"] == code


def test_FR_DPDP_021_stale_if_match_is_refused(world: Any, api: Any, admin_engine: Engine) -> None:
    admin = world.person("office_admin")
    pid = P.shared_purpose(api, world.a, admin_engine)
    sid = P.student(world.a)
    assert P.record(api, admin, sid, P.refused(pid)).status_code == 201
    res = P.record(api, admin, sid, P.refused(pid), headers={"If-Match": 'W/"0"'})
    assert res.status_code == 412


def test_FR_DPDP_022_notes_refuse_aadhaar_numbers_and_are_not_audited(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    admin = world.person("office_admin")
    pid = P.shared_purpose(api, world.a, admin_engine)
    sid = P.student(world.a)
    base = "23456789012"
    aadhaar = base + verhoeff_check_digit(base)
    bad = P.record(api, admin, sid, P.refused(pid, note=f"Aadhaar {aadhaar}"))
    assert bad.status_code == 422
    assert "aadhaar_full_number_rejected" in bad.text
    note = "Father said no at the parent meeting"
    assert P.record(api, admin, sid, P.refused(pid, note=note)).status_code == 201
    summaries = " ".join(
        str(e["summary"]) for e in _events(admin_engine, world.a.tenant_id, "consent.recorded")
    )
    assert note not in summaries


def test_FR_DPDP_021_recording_needs_consent_record(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    pid = P.shared_purpose(api, world.a, admin_engine)
    sid = P.student(world.a)
    for role in ("owner", "exam_coordinator", "class_teacher", "auditor_readonly", "accountant"):
        res = P.record(api, world.person(role), sid, P.refused(pid))
        assert res.status_code == 403, (role, res.text)


# --- consent_state (FR-DPDP-024) --------------------------------------------------------------


def test_FR_DPDP_024_consent_state_is_given_only_for_a_current_given(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    staff, owner = world.person("office_staff"), world.person("owner")
    purpose = P.own_purpose(api, world.a, admin_engine)
    pid, key = uuid.UUID(purpose["id"]), purpose["key"]
    sid = P.student(world.a)

    def state() -> str:
        with tenant_session(world.a.tenant_id) as db:
            return consents_service.consent_state(db, sid, key)

    assert state() == "not_given", "no decision counts as not given"
    assert (
        P.record(api, staff, sid, {"purpose_id": str(pid), "status": "pending"}).status_code == 201
    )
    assert state() == "not_given"
    doc = P.signed_form(admin_engine, world.a, staff)
    assert P.record(api, staff, sid, P.given(pid, doc)).status_code == 201
    assert state() == "given"
    off = api.call(
        owner,
        "PATCH",
        f"/api/v1/consent-purposes/{pid}",
        json={"active": False},
        headers={"If-Match": 'W/"1"'},
    )
    assert off.status_code == 200, off.text
    assert state() == "not_given", "a purpose switched off is not given"
    with tenant_session(world.a.tenant_id) as db:
        assert consents_service.consent_state(db, sid, "no_such_purpose") == "not_given"


# --- the one consent view (FR-DPDP-025) -------------------------------------------------------


def test_FR_DPDP_025_view_shows_purposes_apaar_and_whatsapp_read_only(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    pid = P.shared_purpose(api, world.a, admin_engine)
    ids = P.SW.ensure_students(world)
    sid = ids["s9a"]
    principal = world.person("principal")
    res = api.call(principal, "GET", f"/api/v1/students/{sid}/consent-view")
    assert res.status_code == 200, res.text
    view = res.json()
    assert str(pid) in {p["purpose_id"] for p in view["purposes"]}
    assert view["apaar"]["status"] in ("given", "refused", "pending", "withdrawn")
    assert view["whatsapp"] is not None
    if view["whatsapp"]["available"]:
        assert str(ids["g9a"]) in {g["guardian_id"] for g in view["whatsapp"]["guardians"]}
    # An exam coordinator reads consents and APAAR but not WhatsApp opt-ins.
    coord = api.call(
        world.person("exam_coordinator"), "GET", f"/api/v1/students/{sid}/consent-view"
    )
    assert coord.status_code == 200, coord.text
    assert coord.json()["whatsapp"] is None
    assert coord.json()["apaar"] is not None


def test_FR_DPDP_025_view_scope_class_teacher_and_other_school(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    P.shared_purpose(api, world.a, admin_engine)
    ids = P.SW.ensure_students(world)
    ct = world.person("class_teacher")
    assert api.call(ct, "GET", f"/api/v1/students/{ids['s9a']}/consent-view").status_code == 200
    assert api.call(ct, "GET", f"/api/v1/students/{ids['s9c']}/consent-view").status_code == 404
    other = api.call(world.person("principal"), "GET", f"/api/v1/students/{ids['b_sb']}/consents")
    assert other.status_code == 404
    teacher = api.call(world.person("teacher"), "GET", f"/api/v1/students/{ids['s10a']}/consents")
    assert teacher.status_code == 403


# --- summary (FR-DPDP-026) --------------------------------------------------------------------


def test_FR_DPDP_026_section_counts_respect_the_readers_scope(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    pid = P.shared_purpose(api, world.a, admin_engine)
    P.SW.ensure_students(world)
    whole = api.call(world.person("principal"), "GET", "/api/v1/consents/summary")
    assert whole.status_code == 200, whole.text
    mine = api.call(world.person("class_teacher"), "GET", "/api/v1/consents/summary")
    assert mine.status_code == 200, mine.text
    purpose = next(p for p in mine.json()["purposes"] if p["purpose_id"] == str(pid))
    assert {s["section_id"] for s in purpose["sections"]} == {str(world.a.ids["section_9a"])}
    totals = purpose["totals"]
    assert totals["total"] == sum(
        totals[k] for k in ("given", "refused", "pending", "withdrawn", "not_answered")
    )
    all_sections = next(p for p in whole.json()["purposes"] if p["purpose_id"] == str(pid))
    assert len(all_sections["sections"]) >= 2


# --- forms (FR-DPDP-023) ----------------------------------------------------------------------


def test_FR_DPDP_023_forms_offer_refusal_and_never_an_aadhaar_number(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    P.shared_purpose(api, world.a, admin_engine)
    sid = P.student(world.a, name="Synthetica Form Child")
    staff = world.person("office_staff")
    page = api.call(staff, "GET", "/api/v1/consents/forms", params={"student_id": str(sid)})
    assert page.status_code == 200, page.text
    html = page.text
    assert "I agree" in html
    assert "I do not agree" in html
    assert "do not depend on your answers" in html
    assert "Synthetica Form Child" in html
    assert page.headers["Content-Security-Policy"].startswith("default-src 'none'")
    section = api.call(
        staff,
        "GET",
        "/api/v1/consents/forms",
        params={"section_id": str(world.a.ids["section_9a"])},
    )
    assert section.status_code == 200, section.text
    assert section.text.count("<article>") >= 2
    both = api.call(
        staff,
        "GET",
        "/api/v1/consents/forms",
        params={"student_id": str(sid), "section_id": str(world.a.ids["section_9a"])},
    )
    assert both.status_code == 422
    assert _events(admin_engine, world.a.tenant_id, "consent.form.printed")


TELUGU_REFUSAL = "నేను అంగీకరించడం లేదు"


def test_D47_forms_are_english_while_telugu_is_off(
    world: Any, api: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D47 (ADR-0036): neither ``language=te`` nor a Telugu parent language prints Telugu
    while ``SOS_TELUGU_ENABLED`` is off; the audit records ``en``."""
    P.shared_purpose(api, world.a, admin_engine)
    sid = P.student(world.a)
    staff = world.person("office_staff")
    monkeypatch.setattr(tenancy_service, "parent_language", lambda _s: "te")
    for params in ({"student_id": str(sid)}, {"student_id": str(sid), "language": "te"}):
        page = api.call(staff, "GET", "/api/v1/consents/forms", params=params)
        assert page.status_code == 200, page.text
        assert TELUGU_REFUSAL not in page.text
        assert "I do not agree" in page.text
    printed = _events(admin_engine, world.a.tenant_id, "consent.form.printed")
    assert printed[-1]["summary"]["language"] == "en"


def test_D47_forms_follow_the_parent_language_when_telugu_is_on(
    world: Any, api: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch, telugu_on: None
) -> None:
    P.shared_purpose(api, world.a, admin_engine)
    sid = P.student(world.a)
    staff = world.person("office_staff")
    asked = api.call(
        staff, "GET", "/api/v1/consents/forms", params={"student_id": str(sid), "language": "te"}
    )
    assert asked.status_code == 200
    assert TELUGU_REFUSAL in asked.text
    monkeypatch.setattr(tenancy_service, "parent_language", lambda _s: "te")
    school = api.call(staff, "GET", "/api/v1/consents/forms", params={"student_id": str(sid)})
    assert school.status_code == 200
    assert TELUGU_REFUSAL in school.text


def test_FR_DPDP_023_forms_pdf_is_an_export_with_step_up(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    P.shared_purpose(api, world.a, admin_engine)
    sid = P.student(world.a)
    staff = world.person("office_staff")
    body = {"student_id": str(sid)}
    stale = api.call(staff, "POST", "/api/v1/exports/consent-forms", json=body, auth_age_s=301)
    assert stale.status_code == 428
    res = api.call(staff, "POST", "/api/v1/exports/consent-forms", json=body)
    assert res.status_code == 202, res.text
    assert res.json()["kind"] == "consent_forms"
    only = api.call(
        staff, "POST", "/api/v1/exports/consent-forms", json={**body, "only_unanswered": True}
    )
    assert only.status_code == 422
    assert (
        api.call(
            world.person("accountant"), "POST", "/api/v1/exports/consent-forms", json=body
        ).status_code
        == 403
    )


# --- withdrawal through a data-principal request (FR-DPDP-026, ADR-0049 decision 6) ----------


def test_FR_DPDP_026_withdrawal_request_records_and_links_both_ways(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    rq = sys.modules["sos_test_privacy_support"]
    principal, admin = world.person("principal"), world.person("office_admin")
    pid = P.shared_purpose(api, world.a, admin_engine)
    sid = P.student(world.a)
    doc = P.signed_form(admin_engine, world.a, admin)
    assert P.record(api, admin, sid, P.given(pid, doc)).status_code == 201
    request = rq.logged(api, admin, rq.body("consent_withdrawal", student_id=sid))
    path = f"/api/v1/privacy/requests/{request['id']}/consent-withdrawal"
    body = {"purpose_id": str(pid), "relationship": "mother", "decided_on": "2026-10-01"}
    owner = api.call(world.person("owner"), "POST", path, json=body)
    assert owner.status_code == 403, "the owner cannot record consent decisions"
    res = api.call(principal, "POST", path, json=body)
    assert res.status_code == 200, res.text
    out = res.json()
    (link,) = [x for x in out["links"] if x["link_kind"] == "consent"]
    assert "consent_withdrawn" in [e["event"] for e in out["events"]]
    rows = P.consent_rows(admin_engine, sid)
    assert rows[-1]["status"] == "withdrawn"
    assert str(rows[-1]["privacy_request_id"]) == request["id"]
    assert str(rows[-1]["id"]) == link["target_id"]
    again = api.call(principal, "POST", path, json=body)
    assert again.status_code == 409
    assert again.json()["code"] == "consent_not_given"
    access = rq.logged(api, admin, rq.body("access", student_id=sid))
    wrong = api.call(
        principal, "POST", f"/api/v1/privacy/requests/{access['id']}/consent-withdrawal", json=body
    )
    assert wrong.status_code == 409
    assert wrong.json()["code"] == "not_a_consent_withdrawal"


def test_FR_DPDP_009_access_report_lists_consents_and_breaches(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    from app.core.subjects import Subject
    from app.privacy import breach_service, consents_service

    staff = world.person("office_staff")
    pid = P.shared_purpose(api, world.a, admin_engine)
    sid = P.student(world.a)
    assert P.record(api, staff, sid, P.refused(pid, note="Private office note")).status_code == 201
    breach = P.create_breach(api, world.person("owner"))
    res = api.call(
        world.person("owner"),
        "PUT",
        f"/api/v1/privacy/breaches/{breach['id']}/affected",
        json={"student_ids": [str(sid)]},
    )
    assert res.status_code == 200, res.text
    with tenant_session(world.a.tenant_id) as db:
        (consents,) = consents_service.subject_report(db, Subject("student", sid))
        (breaches,) = breach_service.subject_report(db, Subject("student", sid))
    assert consents.class_key == "consents"
    assert "Refused" in consents.rows[0]
    assert "Private office note" not in str(consents.rows)
    assert breaches.class_key == "breach_records"
    assert P.DESCRIPTION not in str(breaches.rows)
