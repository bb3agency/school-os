"""UDISE+ correction requests to the MEO (FO-02; FR-UDI-001..FR-UDI-010; invariants 3-7).
Synthetic data only.

The authz matrix (tests/security/test_authz_matrix.py, tests/dq/udise_security_support.py) and
BOLA (tests/security/test_bola.py) cover every role and school B's ids; these tests cover the
behaviour, an allowed and a disallowed role, another school's request and the audit trail.
"""

from __future__ import annotations

import re
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.dq.udise_corrections import load_udise_config
from app.students import service as students
from app.students.schemas import ValueIn

pytestmark = pytest.mark.db
DS = sys.modules["sos_test_dq_support"]
SSC = "bseap-ssc-2027"
BASE = "/api/v1/dq/udise-corrections"


@pytest.fixture(autouse=True)
def _keys(keyring: None) -> None:
    return None


def _student(
    school: Any, *, udise_name: str | None = None, udise_dob: str = "2012-03-14"
) -> tuple[uuid.UUID, str]:
    """A Class IX student: register and Aadhaar agree (dob 2012-03-14, male); UDISE+ may not."""
    name = str(DS.unique_name("Kommineni Venkata")) + " Sai Kumar"
    extra = [
        *DS.aadhaar(name=name, dob="2012-03-14", gender="male", last4="4821"),
        ValueIn(attribute_key="full_name", source="udise_plus", value=udise_name or name),
        ValueIn(attribute_key="dob", source="udise_plus", value=udise_dob),
        ValueIn(attribute_key="gender", source="udise_plus", value="male"),
        ValueIn(attribute_key="udise_pen", source="udise_plus", value=_pen()),
    ]
    sid = DS.student(school, name=name, section_key="section_9a", extra=extra)
    return sid, name


def _pen() -> str:
    return "9" + uuid.uuid4().int.__str__()[:10]


def _lines(body: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (line["student"]["id"], line["attribute_key"]): line
        for group in body["groups"]
        for line in group["lines"]
    }


def _audit(admin: Engine, resource_id: str, action: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows: Any = c.execute(
            text("SELECT summary FROM audit.events WHERE resource_id = :r AND action = :a"),
            {"r": resource_id, "a": action},
        ).scalars()
        return list(rows)


def test_config_is_unverified_and_names_roles_not_people() -> None:
    cfg = load_udise_config()
    assert cfg.verified is False
    assert cfg.source
    assert {"meo", "mis_coordinator"} <= set(cfg.recipients)
    assert [g.key for g in cfg.groups] == ["name", "dob", "gender"]


def test_FR_UDI_001_candidates_list_udise_differences_by_field(world: Any, api: Any) -> None:
    named, name = _student(world.a, udise_name=None)
    named_wrong, wrong_name = _student(world.a)
    # A UDISE+ spelling that differs from the register (and Aadhaar) for one student.
    DS.call(
        world.a,
        students.record_value,
        named_wrong,
        "full_name",
        "udise_plus",
        wrong_name.replace("Sai Kumar", "Saikumar"),
    )
    dob_wrong, _ = _student(world.a, udise_dob="2012-04-14")
    office = world.person("office_admin")
    res = api.call(office, "GET", f"{BASE}/candidates", params={"profile_key": SSC})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["verified"] is False
    lines = _lines(body)
    assert (str(named), "full_name") not in lines
    line = lines[(str(named_wrong), "full_name")]
    assert line["udise_value"] == wrong_name.replace("Sai Kumar", "Saikumar")
    assert line["correct_value"] == wrong_name
    assert line["correct_source"] == "Admission register"
    assert line["matches_aadhaar"] == "yes"
    assert re.fullmatch(r"[0-9]{11}", line["pen"] or "")
    assert line["state"] == "open"
    dob = lines[(str(dob_wrong), "dob")]
    assert (dob["udise_value"], dob["correct_value"]) == ("14/04/2012", "14/03/2012")
    groups = {g["key"]: g for g in body["groups"]}
    assert (str(named_wrong), "full_name") in _lines({"groups": [groups["name"]]})
    assert (str(dob_wrong), "dob") in _lines({"groups": [groups["dob"]]})
    assert name  # the register name itself is never needed for the check above

    # A role without dq.readiness.manage: 403.
    teacher = api.call(
        world.person("teacher"), "GET", f"{BASE}/candidates", params={"profile_key": SSC}
    )
    assert teacher.status_code == 403
    # A profile without a UDISE+ comparison: 404.
    unknown = api.call(office, "GET", f"{BASE}/candidates", params={"profile_key": "udise-plus"})
    assert unknown.status_code == 404


def test_FR_UDI_003_to_008_prepare_print_send_and_follow_up(  # noqa: PLR0915 - one story
    world: Any, api: Any, admin_engine: Engine
) -> None:
    sid, name = _student(world.a)
    wrong = name.replace("Sai Kumar", "Saikumar")
    DS.call(world.a, students.record_value, sid, "full_name", "udise_plus", wrong)
    office = world.person("office_admin")
    lines = [{"student_id": str(sid), "attribute_key": "full_name"}]
    made = api.call(
        office,
        "POST",
        BASE,
        json={"profile_key": SSC, "lines": lines},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert made.status_code == 201, made.text
    body = made.json()
    request_id = body["id"]
    assert (body["status"], body["lines_count"], body["students_count"]) == ("prepared", 1, 1)
    assert body["open_count"] == 1
    assert made.headers["Location"] == f"{BASE}/{request_id}"
    (summary,) = _audit(admin_engine, request_id, "dq.udise_request.prepared")
    assert summary == {
        "profile_key": SSC,
        "lines": 1,
        "students": 1,
        "groups": [{"group": "name", "lines": 1}],
    }
    assert name not in str(summary)

    # The same line cannot be asked for twice while a request is open.
    again = api.call(
        office,
        "POST",
        BASE,
        json={"profile_key": SSC, "lines": lines},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert again.status_code == 422
    candidates = api.call(office, "GET", f"{BASE}/candidates", params={"profile_key": SSC})
    assert _lines(candidates.json())[(str(sid), "full_name")]["requested_in"] == request_id

    # The letter: export.portal (principal), never an Aadhaar number; audited with counts.
    principal = world.person("principal")
    letter = api.call(principal, "GET", f"{BASE}/{request_id}/letter")
    assert letter.status_code == 200, letter.text
    page = letter.text
    assert "Request to correct student details in UDISE+" in page
    assert name in page
    assert wrong in page
    assert "Matches Aadhaar" in page
    assert not re.search(
        r"\d{4}\s?\d{4}\s?\d{4}", page.replace(body["groups"][0]["lines"][0]["pen"] or "", "")
    )
    assert "default-src 'none'" in letter.headers["Content-Security-Policy"]
    assert _audit(admin_engine, request_id, "dq.udise_request.printed") == [
        {"lines": 1, "students": 1}
    ]
    check = api.call(principal, "GET", f"{BASE}/{request_id}/letter", params={"check": "true"})
    assert check.status_code == 204
    assert len(_audit(admin_engine, request_id, "dq.udise_request.printed")) == 1
    # office_staff holds dq.readiness.manage but not export.portal: no letter.
    staff = api.call(world.person("office_staff"), "GET", f"{BASE}/{request_id}/letter")
    assert staff.status_code == 403

    # Sent to the MEO (a role, never a name), then closed.
    got = api.call(office, "GET", f"{BASE}/{request_id}")
    etag = got.headers["ETag"]
    bad = api.call(
        office,
        "POST",
        f"{BASE}/{request_id}/sent",
        json={"sent_on": "2026-11-05", "sent_to": "Mr Synthetic"},
        headers={"If-Match": etag},
    )
    assert bad.status_code == 422
    sent = api.call(
        office,
        "POST",
        f"{BASE}/{request_id}/sent",
        json={"sent_on": "2026-11-05", "sent_to": "meo"},
        headers={"If-Match": etag},
    )
    assert sent.status_code == 200, sent.text
    assert (sent.json()["status"], sent.json()["sent_to_label"]) == (
        "sent",
        "Mandal Educational Officer (MEO)",
    )
    stale = api.call(
        office,
        "POST",
        f"{BASE}/{request_id}/sent",
        json={"sent_on": "2026-11-05", "sent_to": "meo"},
        headers={"If-Match": etag},
    )
    assert stale.status_code == 412

    # The MEO corrected UDISE+; the next UDISE+ import records the right spelling: fixed.
    DS.call(world.a, students.record_value, sid, "full_name", "udise_plus", name)
    after = api.call(office, "GET", f"{BASE}/{request_id}")
    assert (after.json()["fixed_count"], after.json()["open_count"]) == (1, 0)
    nothing = api.call(principal, "GET", f"{BASE}/{request_id}/letter")
    assert nothing.status_code == 409
    assert nothing.json()["code"] == "nothing_to_print"
    closed = api.call(
        office,
        "POST",
        f"{BASE}/{request_id}/close",
        json={"outcome": "done"},
        headers={"If-Match": after.headers["ETag"]},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "done"
    assert _audit(admin_engine, request_id, "dq.udise_request.done") == [
        {"from": "sent", "outcome": "done"}
    ]
    listed = api.call(office, "GET", BASE)
    assert request_id in {r["id"] for r in listed.json()}

    # A random id is 404 (school B's ids: tests/security/test_bola.py).
    assert api.call(office, "GET", f"{BASE}/{uuid.uuid4()}").status_code == 404
