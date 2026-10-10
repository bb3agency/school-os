"""Board and portal readiness routes (US-504..US-506, FR-DQ-040..FR-DQ-046, SEC-003, SEC-008,
SEC-015, invariants 4-6). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.languages import contains_telugu
from app.students.schemas import ValueIn

pytestmark = pytest.mark.db
DS = sys.modules["sos_test_dq_support"]
W = sys.modules["sos_test_api_world"]
SSC = "bseap-ssc-2027"
BASE = f"/api/v1/dq/readiness/{SSC}"


@pytest.fixture(autouse=True)
def _keys(keyring: None) -> None:
    return None


def udise(name: str | None = None, dob: str = "2012-03-14", gender: str = "male") -> list[ValueIn]:
    out = [
        ValueIn(attribute_key="dob", source="udise_plus", value=dob),
        ValueIn(attribute_key="gender", source="udise_plus", value=gender),
    ]
    if name is not None:
        out.append(ValueIn(attribute_key="full_name", source="udise_plus", value=name))
    return out


def fresh_name() -> str:
    """A register name no other test student shares, ending in "Sai Kumar"."""
    return str(DS.unique_name("Kommineni Venkata")) + " Sai Kumar"


def ssc_student(
    school: Any,
    *,
    name: str | None = None,
    aadhaar_name: str | None = None,
    udise_name: str | None = None,
    udise_dob: str = "2012-03-14",
    section_key: str = "section_9a",
) -> tuple[uuid.UUID, str]:
    """A Class IX/X student (register dob 2012-03-14, male); Aadhaar and UDISE+ hold the same
    values unless given."""
    name = name or fresh_name()
    extra = [
        *DS.aadhaar(name=aadhaar_name or name, dob="2012-03-14", gender="male", last4="4821"),
        *udise(udise_name or name, dob=udise_dob),
    ]
    sid = DS.student(school, name=name, section_key=section_key, extra=extra)
    return sid, name


def run(api: Any, who: Any, *student_ids: uuid.UUID, profile: str = SSC) -> Any:
    return api.call(
        who,
        "POST",
        f"/api/v1/dq/readiness/{profile}/runs",
        json={"scope": {"student_ids": [str(s) for s in student_ids]}},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )


def field(detail: dict[str, Any], key: str) -> dict[str, Any]:
    return next(f for f in detail["fields"] if f["attribute_key"] == key)


def test_US_504_summary_counts_sections_in_scope(world: Any, api: Any) -> None:
    ssc_student(world.a)
    name = fresh_name()
    ssc_student(world.a, name=name, aadhaar_name=name.replace(" Sai Kumar", " Saikumar"))
    ssc_student(world.a, udise_dob="2012-03-15", section_key="section_9c")
    office = world.person("office_admin")
    res = api.call(office, "GET", BASE)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["profile"]["key"] == SSC
    assert body["profile"]["verified"] is False
    assert body["profile"]["source"]
    assert body["profile"]["classes"] == ["IX", "X"]
    sections = {s["section_id"]: s for s in body["sections"]}
    nine_a = sections[str(world.a.ids["section_9a"])]
    assert nine_a["needs_parent"] >= 1
    assert nine_a["ready"] >= 1
    assert sections[str(world.a.ids["section_9c"])]["needs_school"] >= 1
    totals = body["totals"]
    assert totals["students"] == sum(
        totals[k] for k in ("ready", "needs_parent", "needs_school", "blocked")
    )
    # A class teacher sees only their own section (SEC-015).
    teacher = api.call(world.person("class_teacher"), "GET", BASE)
    assert teacher.status_code == 200, teacher.text
    assert {s["section_id"] for s in teacher.json()["sections"]} == {str(world.a.ids["section_9a"])}
    # Unknown profiles, and profiles without a readiness check, are 404.
    assert api.call(office, "GET", "/api/v1/dq/readiness/no-such-board").status_code == 404
    assert api.call(office, "GET", "/api/v1/dq/readiness/udise-plus").status_code == 404


def test_SEC_003_readiness_permissions(world: Any, api: Any) -> None:
    for role in ("accountant", "teacher"):
        assert api.call(world.person(role), "GET", BASE).status_code == 403
    sid, _ = ssc_student(world.a)
    assert run(api, world.person("auditor_readonly"), sid).status_code == 403  # reads only
    assert run(api, world.person("class_teacher"), sid).status_code == 403


def test_US_505_student_detail_shows_the_exact_difference_and_who_fixes_it(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    name = fresh_name()
    # Aadhaar has the space between "Sai" and "Kumar" missing.
    sid, _ = ssc_student(world.a, name=name, aadhaar_name=name.replace("Sai Kumar", "SaiKumar"))
    office = world.person("office_admin")
    res = api.call(office, "GET", f"{BASE}/students/{sid}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert not contains_telugu(res.text)  # ADR-0036
    assert body["status"] == "needs_parent"
    assert body["values_shown"] is True
    full_name = field(body, "full_name")
    assert full_name["reference"] == "admission_register"
    (item,) = full_name["items"]
    assert item["owner"] == "parent_aadhaar"
    assert item["source"] == "aadhaar_as_printed"
    assert item["kinds"] == ["spacing"]
    assert item["changes"][0]["en"] == "space missing after “Sai”"
    assert any(s["op"] == "delete" and s["reference"] == " " for s in item["segments"])
    assert "Aadhaar centre" in item["owner_label"]["en"]
    shown = {v["source"]: v for v in full_name["values"]}
    assert shown["aadhaar_as_printed"]["value"] == name.replace("Sai Kumar", "SaiKumar")
    viewed = W.audit_events(admin_engine, world.a.tenant_id, "dq.readiness.student_viewed")
    assert any(e["resource_id"] == sid for e in viewed)
    # Office staff do not hold student.read_sensitive: Aadhaar values stay masked, no diff.
    staff = api.call(world.person("office_staff"), "GET", f"{BASE}/students/{sid}").json()
    assert staff["values_shown"] is False
    staff_name = field(staff, "full_name")
    hidden = {v["source"]: v for v in staff_name["values"]}["aadhaar_as_printed"]
    assert hidden["value"] is None
    assert hidden["masked"]
    assert hidden["sensitive"] is True
    assert staff_name["items"][0]["segments"] is None
    assert staff_name["items"][0]["kinds"] == ["spacing"]
    assert "SaiKumar" not in str(staff)


def test_SEC_015_readiness_bola_and_other_school(world: Any, api: Any) -> None:
    sid_9c, _ = ssc_student(world.a, section_key="section_9c")
    teacher = world.person("class_teacher")
    assert api.call(teacher, "GET", f"{BASE}/students/{sid_9c}").status_code == 404
    listed = api.call(
        teacher,
        "GET",
        f"{BASE}/students",
        params={"section_id": str(world.a.ids["section_9c"])},
    )
    assert listed.status_code == 404
    slips = api.call(teacher, "GET", f"{BASE}/slips", params={"student_id": str(sid_9c)})
    assert slips.status_code == 404
    other = DS.student(world.b, section_key="section_9a")
    office = world.person("office_admin")
    assert api.call(office, "GET", f"{BASE}/students/{other}").status_code == 404
    assert (
        api.call(
            office,
            "GET",
            f"{BASE}/students",
            params={"section_id": str(world.b.ids["section_9a"])},
        ).status_code
        == 404
    )
    own = api.call(
        teacher, "GET", f"{BASE}/students", params={"section_id": str(world.a.ids["section_9a"])}
    )
    assert own.status_code == 200
    assert all(s["section_id"] == str(world.a.ids["section_9a"]) for s in own.json())


def test_FR_DQ_043_runs_store_findings_idempotently_and_waivers_count(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    name = fresh_name()
    sid, _ = ssc_student(world.a, name=name, udise_name=name.upper(), udise_dob="2012-03-15")
    staff = world.person("office_staff")
    first = run(api, staff, sid)
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "completed"
    rows = DS.findings(admin_engine, sid, "DQ-031")
    by_attribute = {r["attribute_key"]: r for r in rows}
    assert set(by_attribute) == {"full_name", "dob"}
    dob = by_attribute["dob"]
    assert (dob["explanation_code"], dob["severity"], dob["profile_key"]) == (
        "DQ-031-UDISE",
        "blocker",
        SSC,
    )
    assert by_attribute["full_name"]["severity"] == "info"  # capital letters only: advisory
    assert "2012-03-15" not in str(rows)
    second = run(api, staff, sid)
    assert second.status_code == 202
    assert second.json()["stats"]["new"] == 0
    assert len(DS.findings(admin_engine, sid, "DQ-031")) == len(rows)
    detail = api.call(world.person("office_admin"), "GET", f"{BASE}/students/{sid}").json()
    assert detail["status"] == "needs_school"
    # The principal waives the UDISE+ date difference: the student counts as ready.
    waived = api.call(
        world.person("principal"),
        "POST",
        f"/api/v1/dq/findings/{dob['id']}/waive",
        json={"reason": "Board confirmed the date on the portal"},
    )
    assert waived.status_code == 200, waived.text
    after = api.call(world.person("office_admin"), "GET", f"{BASE}/students/{sid}").json()
    assert after["status"] == "ready"
    dob_item = field(after, "dob")["items"][0]
    assert dob_item["waived"] is True
    assert dob_item["finding_status"] == "waived"
    completed = W.audit_events(admin_engine, world.a.tenant_id, "dq.run.completed")
    assert any(e["summary"].get("profile_key") == SSC for e in completed)


def test_FR_DQ_042_default_run_scope_is_the_profile_classes(world: Any, api: Any) -> None:
    res = api.call(
        world.person("exam_coordinator"),
        "POST",
        f"{BASE}/runs",
        json={},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert res.status_code == 202, res.text
    assert set(res.json()["scope"]["class_ids"]) == {
        str(world.a.ids["class_ix"]),
        str(world.a.ids["class_x"]),
    }


def test_FR_DQ_044_apaar_failure_list(world: Any, api: Any) -> None:
    name = fresh_name()
    sid, _ = ssc_student(world.a, name=name, udise_name=name + " Reddy")
    res = api.call(
        world.person("office_admin"),
        "GET",
        "/api/v1/dq/readiness/apaar/students",
        params={"section_id": str(world.a.ids["section_9a"])},
    )
    assert res.status_code == 200, res.text
    mine = next(s for s in res.json() if s["student"]["id"] == str(sid))
    assert mine["status"] == "needs_school"
    assert mine["owners"] == ["school_udise"]
    assert mine["attribute_keys"] == ["full_name"]
    detail = api.call(
        world.person("office_admin"), "GET", f"/api/v1/dq/readiness/apaar/students/{sid}"
    ).json()
    (item,) = field(detail, "full_name")["items"]
    assert item["kinds"] == ["word_missing"]
    assert item["against"] == "admission_register"


def test_US_505_parent_slip_is_printable_and_audited(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    sid, name = ssc_student(world.a, udise_dob="2012-03-15")
    office = world.person("office_admin")
    res = api.call(office, "GET", f"{BASE}/slips", params={"student_id": str(sid)})
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["content-security-policy"].startswith("default-src 'none'")
    assert res.headers["cache-control"] == "no-store"
    page = res.text
    assert not contains_telugu(page)  # English only while Telugu is hidden (ADR-0036)
    assert "Parent verification slip" in page
    assert "<script" not in page
    assert "4821" not in page  # never any part of an Aadhaar number (invariant 4)
    assert "15/03/2012" in page
    assert "MEO / MIS coordinator" in page
    assert "signature" in page
    section = api.call(
        office, "GET", f"{BASE}/slips", params={"section_id": str(world.a.ids["section_9a"])}
    )
    assert section.status_code == 200
    assert section.text.count('class="slip"') >= 1
    both = api.call(
        office,
        "GET",
        f"{BASE}/slips",
        params={"student_id": str(sid), "section_id": str(world.a.ids["section_9a"])},
    )
    assert both.status_code == 422
    printed = W.audit_events(admin_engine, world.a.tenant_id, "dq.readiness.slips_printed")
    assert any(e["resource_id"] == sid for e in printed)
    assert name


def test_SEC_008_readiness_calls_do_not_log_personal_data(
    world: Any, api: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "Jonnalagadda Synthetica Hemanth"
    sid = DS.student(
        world.a,
        name=secret,
        extra=[
            *DS.aadhaar(name="Jonnalagadda Synthetica", dob="2011-01-05", gender="female"),
            *udise("Jonnalagadda Synthetica Hemanth", dob="2011-01-05"),
        ],
    )
    capsys.readouterr()
    office = world.person("office_admin")
    run(api, office, sid)
    api.call(office, "GET", BASE)
    api.call(office, "GET", f"{BASE}/students/{sid}")
    api.call(office, "GET", f"{BASE}/slips", params={"student_id": str(sid)})
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "dq.readiness.slips_printed" in logs
    for word in (*secret.split(), "2011-01-05"):
        assert word not in logs


def test_FR_DQ_046_profiles_catalog_marks_readiness_and_verification(world: Any, api: Any) -> None:
    res = api.call(world.person("office_staff"), "GET", "/api/v1/dq/profiles")
    by_key = {p["key"]: p for p in res.json()}
    assert by_key[SSC]["readiness"] is True
    assert by_key[SSC]["verified"] is False
    assert by_key["apaar"]["readiness"] is True
    assert by_key["udise-plus"]["readiness"] is False


def test_US_504_findings_list_explains_readiness_findings(world: Any, api: Any) -> None:
    sid, _ = ssc_student(world.a, udise_dob="2012-03-15")
    office = world.person("office_admin")
    run(api, office, sid)
    listed = api.call(
        office,
        "GET",
        "/api/v1/dq/findings",
        params={"student_id": str(sid), "rule_id": "DQ-031"},
    )
    assert listed.status_code == 200, listed.text
    (finding,) = listed.json()["data"]
    assert finding["explanation"]["en"] == (
        "Date of birth in UDISE+ differs from the right value (the day differs). "
        "Ask the MEO or MIS coordinator to correct UDISE+."
    )
    assert finding["routes"][0]["code"] == "ROUTE-UDISE"
