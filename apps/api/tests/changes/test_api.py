"""Change-request routes over HTTP (docs/09 Change requests; US-601; FR-CR-001..005; SEC-014;
docs/12 §7 Gherkin "Maker-checker for identity corrections (FR-CR-002)").

Members come from the real resolver (roles in the database), MFA/step-up from the synthetic
principal headers of ``tests/api/world.py``.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db
CR = sys.modules["sos_test_changes_objects"]
BASE = "/api/v1/change-requests"


def _body(sid: uuid.UUID, doc: uuid.UUID, **kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "student_id": str(sid),
        "attribute_key": "dob",
        "new_value_date": "2012-03-15",
        "reason": "Birth certificate shows 15/03/2012",
        "evidence_document_id": str(doc),
    }
    body.update(kw)
    return body


def _submit(api: Any, admin: Engine, school: Any, who: str, sid: uuid.UUID, **kw: Any) -> Any:
    doc = CR.evidence(admin, school, school.people[who])
    res = api.call(school.people[who], "POST", BASE, json=_body(sid, doc, **kw))
    assert res.status_code == 201, res.text
    return res


def _decide(
    api: Any, school: Any, who: str, rid: str, action: str, *, version: int = 1, **kw: Any
) -> Any:
    return api.call(
        school.people[who],
        "POST",
        f"{BASE}/{rid}/{action}",
        headers={"If-Match": f'W/"{version}"'},
        **kw,
    )


# --- docs/12 §7: Feature: Maker-checker for identity corrections (FR-CR-002) ------------------


def test_FR_CR_002_requester_cannot_approve_their_own_correction(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    """Scenario: Requester cannot approve their own correction."""
    # Given office admin "Lakshmi" submitted a DOB correction with evidence for student "S-457"
    sid = CR.student(school, admission_no=f"S-457{uuid.uuid4().hex[:4]}")
    rid = _submit(api, admin_engine, school, "dual", sid).json()["id"]
    # When "Lakshmi" tries to approve it (she also holds the approve permission, fresh MFA)
    res = _decide(api, school, "dual", rid, "approve")
    # Then the request is rejected with code "self_approval_forbidden"
    assert res.status_code == 403
    assert res.json()["code"] == "self_approval_forbidden"
    # And the correction remains "pending"
    got = api.call(school.people["dual"], "GET", f"{BASE}/{rid}").json()
    assert got["status"] == "pending"
    assert got["can_decide"] is False
    assert got["can_cancel"] is True


def test_FR_CR_002_maker_without_approve_permission_gets_403(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    sid = CR.student(school)
    rid = _submit(api, admin_engine, school, "office_admin", sid).json()["id"]
    res = _decide(api, school, "office_admin", rid, "approve")
    assert (res.status_code, res.json()["code"]) == (403, "forbidden")


def test_FR_CR_002_principal_approves_with_fresh_mfa(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    """Scenario: Principal approves with fresh MFA."""
    sid = CR.student(school)
    old_id = CR.SW.current_value_id(admin_engine, sid, "dob", "admission_register")
    rid = _submit(api, admin_engine, school, "office_admin", sid).json()["id"]
    # Without a sign-in in the last 5 minutes: 428 (docs/09 §5.3), nothing changes.
    stale = _decide(api, school, "principal", rid, "approve", auth_age_s=301)
    assert (stale.status_code, stale.json()["code"]) == (428, "step_up_required")
    assert CR.row(admin_engine, uuid.UUID(rid))["status"] == "pending"
    # Given the principal completed MFA within the last 5 minutes, When they approve
    res = _decide(api, school, "principal", rid, "approve", json={"note": None})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "approved"
    assert res.headers["ETag"] == 'W/"2"'
    # Then a new verified admission-register value is recorded
    history = api.call(
        school.people["principal"],
        "GET",
        f"/api/v1/students/{sid}/values",
        params={"attribute": "dob"},
    ).json()
    current = [v for v in history if v["current"] and v["source"] == "admission_register"]
    assert len(current) == 1
    assert current[0]["value"] == "2012-03-15"
    assert current[0]["verification_status"] == "verified"
    assert current[0]["change_request_id"] == rid
    # And the previous value is kept in history
    previous = next(v for v in history if v["id"] == str(old_id))
    assert (previous["value"], previous["current"]) == ("2012-03-14", False)
    # And DQ findings for the student are re-evaluated (DQ consumes this outbox event)
    events = CR.outbox(admin_engine, school.tenant_id, uuid.UUID(rid))
    assert ("change_request.approved", {"change_request_id": rid, "student_id": str(sid),
            "attribute_key": "dob"}) in events  # fmt: skip
    # And two audit events exist: the approval and the recorded value (each exactly once)
    acts = CR.actions(admin_engine, school.tenant_id, uuid.UUID(rid))
    assert acts.count("change_request.approved") == 1
    assert acts.count("student.value.recorded") == 1


# --- HTTP conventions ----------------------------------------------------------------------------


def test_docs_09_submit_is_idempotent_and_returns_location_and_etag(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    sid = CR.student(school)
    doc = CR.evidence(admin_engine, school, school.people["office_admin"])
    key = {"Idempotency-Key": f"cr-{uuid.uuid4().hex}"}
    first = api.call(school.people["office_admin"], "POST", BASE, json=_body(sid, doc), headers=key)
    assert first.status_code == 201, first.text
    rid = first.json()["id"]
    assert first.headers["Location"] == f"{BASE}/{rid}"
    assert first.headers["ETag"] == 'W/"1"'
    again = api.call(school.people["office_admin"], "POST", BASE, json=_body(sid, doc), headers=key)
    assert again.status_code == 201
    assert again.json()["id"] == rid
    assert again.headers.get("Idempotent-Replayed") == "true"


def test_docs_09_decisions_need_a_current_if_match(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    rid = _submit(api, admin_engine, school, "office_admin", CR.student(school)).json()["id"]
    principal = school.people["principal"]
    missing = api.call(principal, "POST", f"{BASE}/{rid}/approve")
    assert (missing.status_code, missing.json()["code"]) == (400, "if_match_required")
    stale = _decide(api, school, "principal", rid, "approve", version=5)
    assert stale.status_code == 412
    assert _decide(api, school, "principal", rid, "approve").status_code == 200
    again = _decide(api, school, "owner", rid, "approve", version=2)
    assert (again.status_code, again.json()["code"]) == (409, "request_not_pending")


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"attribute_key": "mother_tongue", "new_value": "Telugu", "new_value_date": None},
         (422, "not_identity_attribute")),
        ({"evidence_document_id": "RANDOM"}, (422, "evidence_required")),
        ({"new_value": "2012-01-01"}, (422, "validation_error")),
        ({"new_value_date": None}, (422, "validation_error")),
        ({"reason": "short"}, (422, "validation_error")),
        ({"student_id": "RANDOM"}, (404, "not_found")),
    ],
)  # fmt: skip
def test_docs_09_submit_errors(
    school: Any, api: Any, admin_engine: Engine, change: dict[str, Any], expected: tuple[int, str]
) -> None:
    doc = CR.evidence(admin_engine, school, school.people["office_admin"])
    body = _body(CR.student(school), doc)
    body.update({k: (str(uuid.uuid4()) if v == "RANDOM" else v) for k, v in change.items()})
    body = {k: v for k, v in body.items() if v is not None}
    res = api.call(school.people["office_admin"], "POST", BASE, json=body)
    assert (res.status_code, res.json()["code"]) == expected, res.text


def test_FR_CR_004_reject_needs_a_reason_and_notifies_the_requester(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    rid = _submit(api, admin_engine, school, "office_staff", CR.student(school)).json()["id"]
    assert _decide(api, school, "owner", rid, "reject", json={}).status_code == 422
    short = _decide(api, school, "owner", rid, "reject", json={"reason": "no"})
    assert short.status_code == 422
    assert short.json()["errors"][0]["code"] == "too_short"
    res = _decide(api, school, "owner", rid, "reject", json={"reason": "Evidence is not readable"})
    assert res.status_code == 200, res.text
    assert (res.json()["status"], res.json()["decision_note"]) == (
        "rejected",
        "Evidence is not readable",
    )
    staff = school.people["office_staff"]
    inbox = api.call(staff, "GET", "/api/v1/notifications", params={"limit": 200}).json()["data"]
    assert any(
        n["template_key"] == "change_request.rejected" and n["resource_id"] == rid for n in inbox
    )


def test_FR_CR_004_only_the_requester_cancels(school: Any, api: Any, admin_engine: Engine) -> None:
    rid = _submit(api, admin_engine, school, "office_staff", CR.student(school)).json()["id"]
    other = _decide(api, school, "office_admin", rid, "cancel")
    assert (other.status_code, other.json()["code"]) == (403, "not_requester")
    mine = _decide(api, school, "office_staff", rid, "cancel")
    assert (mine.status_code, mine.json()["status"]) == (200, "cancelled")


# --- lists and scope -----------------------------------------------------------------------------


def test_docs_09_list_filters_by_status_and_student(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    sid = CR.student(school)
    rid = _submit(api, admin_engine, school, "office_admin", sid).json()["id"]
    owner = school.people["owner"]  # approve permission only: may read the queue
    pending = api.call(owner, "GET", BASE, params={"status": "pending", "limit": 200}).json()
    assert rid in {r["id"] for r in pending["data"]}
    assert {r["status"] for r in pending["data"]} == {"pending"}
    mine = api.call(owner, "GET", BASE, params={"student_id": str(sid)}).json()["data"]
    assert [r["id"] for r in mine] == [rid]
    approved = api.call(owner, "GET", BASE, params={"status": "approved", "limit": 200}).json()
    assert rid not in {r["id"] for r in approved["data"]}
    assert api.call(owner, "GET", BASE, params={"status": "bogus"}).status_code == 422


def test_docs_09_list_pages_with_a_cursor(school: Any, api: Any, admin_engine: Engine) -> None:
    for _ in range(3):
        _submit(api, admin_engine, school, "office_admin", CR.student(school))
    owner = school.people["owner"]
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(50):
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = api.call(owner, "GET", BASE, params=params).json()
        seen.extend(r["id"] for r in page["data"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) >= 3
    bad = api.call(owner, "GET", BASE, params={"cursor": "not-a-cursor"})
    assert bad.status_code == 422


def test_SEC_003_readers_need_request_or_approve_permission(school: Any, api: Any) -> None:
    res = api.call(school.people["accountant"], "GET", BASE)
    assert res.status_code == 403


def test_SEC_015_scoped_maker_sees_only_their_sections(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    clerk = school.people["scoped_clerk"]
    outside = _submit(api, admin_engine, school, "office_admin", CR.student(school, "section_9c"))
    rid_out = outside.json()["id"]
    doc = CR.evidence(
        admin_engine,
        school,
        school.people["owner"],
        acl=[("section", str(school.ids["section_9a"]))],
    )
    inside = api.call(clerk, "POST", BASE, json=_body(CR.student(school, "section_9a"), doc))
    assert inside.status_code == 201, inside.text
    listed = {r["id"] for r in api.call(clerk, "GET", BASE, params={"limit": 200}).json()["data"]}
    assert inside.json()["id"] in listed
    assert rid_out not in listed
    for suffix in ("", "/memo"):
        assert api.call(clerk, "GET", f"{BASE}/{rid_out}{suffix}").status_code == 404
    assert _decide(api, school, "scoped_clerk", rid_out, "cancel").status_code == 404
    denied = api.call(clerk, "POST", BASE, json=_body(CR.student(school, "section_9c"), doc))
    assert denied.status_code == 404


def test_SEC_001_other_school_cannot_reach_requests(
    school: Any, world: Any, api: Any, admin_engine: Engine
) -> None:
    rid = _submit(api, admin_engine, school, "office_admin", CR.student(school)).json()["id"]
    b_owner = world.b.people["owner"]
    assert api.call(b_owner, "GET", f"{BASE}/{rid}").status_code == 404
    assert api.call(b_owner, "GET", f"{BASE}/{rid}/memo").status_code == 404
    res = api.call(b_owner, "POST", f"{BASE}/{rid}/approve", headers={"If-Match": 'W/"1"'})
    assert res.status_code == 404
    listed = api.call(b_owner, "GET", BASE, params={"limit": 200}).json()["data"]
    assert rid not in {r["id"] for r in listed}
    assert CR.row(admin_engine, uuid.UUID(rid))["status"] == "pending"
