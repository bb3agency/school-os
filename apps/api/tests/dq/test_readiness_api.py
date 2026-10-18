"""Board and portal readiness routes (US-504..US-506, FR-DQ-040..FR-DQ-046, SEC-003, SEC-008,
SEC-015, invariants 4-6). Synthetic data only."""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Engine, text

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


def test_US_504_overview_has_one_summary_per_profile_in_scope(world: Any, api: Any) -> None:
    """``GET /dq/readiness``: several boards and portals in one call (one column per portal)."""
    ssc_student(world.a)
    ssc_student(world.a, section_key="section_9c")
    office = world.person("office_admin")
    overview = "/api/v1/dq/readiness"
    res = api.call(office, "GET", overview, params={"profile_keys": [SSC, "apaar"]})
    assert res.status_code == 200, res.text
    summaries = res.json()["summaries"]
    assert [s["profile"]["key"] for s in summaries] == [SSC, "apaar"]
    single = api.call(office, "GET", BASE).json()
    assert summaries[0]["totals"] == single["totals"]
    assert summaries[0]["sections"] == single["sections"]
    # Omitted: every readiness profile listed for the school (FR-TEN-020).
    profiles = api.call(office, "GET", "/api/v1/dq/profiles").json()
    listed = {p["key"] for p in profiles if p["readiness"]}
    default = api.call(office, "GET", overview).json()["summaries"]
    assert {s["profile"]["key"] for s in default} == listed
    # A class teacher sees only their own section in every column (SEC-015).
    teacher = api.call(
        world.person("class_teacher"), "GET", overview, params={"profile_keys": [SSC, "apaar"]}
    )
    assert teacher.status_code == 200, teacher.text
    for one in teacher.json()["summaries"]:
        assert {s["section_id"] for s in one["sections"]} <= {str(world.a.ids["section_9a"])}
    # Permission, unknown profiles and bounds.
    assert api.call(world.person("accountant"), "GET", overview).status_code == 403
    for bad in ("no-such-board", "udise-plus"):
        res = api.call(office, "GET", overview, params={"profile_keys": [SSC, bad]})
        assert res.status_code == 404
    bad_key = api.call(office, "GET", overview, params={"profile_keys": "Bad Key"})
    assert bad_key.status_code == 422
    too_many = api.call(office, "GET", overview, params={"profile_keys": [SSC] * 6})
    assert too_many.status_code == 422
    # Another school's sections never appear.
    other = api.call(world.b.people["owner"], "GET", overview, params={"profile_keys": [SSC]})
    assert other.status_code == 200
    own_sections = {str(v) for k, v in world.a.ids.items() if k.startswith("section_")}
    assert not {s["section_id"] for s in other.json()["summaries"][0]["sections"]} & own_sections


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


def test_L_03_apaar_readiness_never_pushes_a_refused_student(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """FR-APC-006: a student whose parents refused (or withdrew) APAAR consent is not on the
    APAAR failure list and gets no DQ-031 finding of the ``apaar`` profile; the SSC check,
    which is not an APAAR action, still applies to them."""
    import datetime as dt

    from app.apaar import service as apaar_service
    from app.apaar.schemas import ConsentIn

    name = fresh_name()
    sid, _ = ssc_student(world.a, name=name, udise_name=name + " Reddy")
    refused = ConsentIn(status="refused", relationship="father", decided_on=dt.date(2026, 7, 1))
    DS.call(world.a, apaar_service.record_consent, sid, refused)
    admin = world.person("office_admin")
    listed = api.call(
        admin,
        "GET",
        "/api/v1/dq/readiness/apaar/students",
        params={"section_id": str(world.a.ids["section_9a"])},
    )
    assert listed.status_code == 200, listed.text
    mine = next(s for s in listed.json() if s["student"]["id"] == str(sid))
    assert (mine["status"], mine["owners"]) == ("ready", [])
    assert run(api, admin, sid, profile="apaar").status_code == 202
    assert not [
        f
        for f in DS.findings(admin_engine, sid)
        if f["rule_id"] == "DQ-031" and f["profile_key"] == "apaar" and f["status"] == "open"
    ]
    ssc = api.call(
        admin, "GET", f"{BASE}/students", params={"section_id": str(world.a.ids["section_9a"])}
    )
    assert next(s for s in ssc.json() if s["student"]["id"] == str(sid))["status"] == "needs_school"


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


def test_SEC_044_a_scoped_readiness_manager_runs_checks_only_in_their_scope(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """SEC-015, SEC-044: a readiness run is limited by the dq.readiness.manage grant's own
    scope. A member whose custom role holds it for 9A only (and who reads every student through
    another role) stores no findings for a 9C student, and a 9C section scope is refused like
    any section outside reach (422, no existence oracle)."""
    tid = world.a.tenant_id
    key = f"readiness_9a_{uuid.uuid4().hex[:8]}"
    role_id = uuid.uuid4()
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te) "
                "VALUES (:r, :t, :k, 'Synthetic readiness 9A', 'కృత్రిమ పాత్ర')"
            ),
            {"r": role_id, "t": tid, "k": key},
        )
        for perm in ("dq.readiness.read", "dq.readiness.manage"):
            c.execute(
                text(
                    "INSERT INTO core.role_permissions (tenant_id, role_id, permission_key) "
                    "VALUES (:t, :r, :p)"
                ),
                {"t": tid, "r": role_id, "p": perm},
            )
    # accountant: student.read_basic for the whole school, no data-quality permission.
    member = W.add_member(
        admin_engine, tid, [key, "accountant"], scopes=[("section", world.a.ids["section_9a"])]
    )
    name = fresh_name()
    sid_9c, _ = ssc_student(world.a, name=name, udise_dob="2012-03-15", section_key="section_9c")
    res = run(api, member, sid_9c)
    assert res.status_code == 202, res.text
    assert res.json()["stats"]["students"] == 0
    assert DS.findings(admin_engine, sid_9c, "DQ-031") == []
    refused = api.call(
        member,
        "POST",
        f"{BASE}/runs",
        json={"scope": {"section_ids": [str(world.a.ids["section_9c"])]}},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert refused.status_code == 422, refused.text
    sid_9a, _ = ssc_student(world.a, udise_dob="2012-03-15")
    own = run(api, member, sid_9a)
    assert own.status_code == 202, own.text
    assert own.json()["stats"]["students"] == 1


def test_SEC_043_section_slips_audit_whose_restricted_values_were_shown(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """Invariant 7, SEC-043: a section print of slips that shows Aadhaar-as-printed values
    (asked for, by a holder of ``student.read_sensitive``) names the students in its audit
    event, ids only; a print without restricted values names nobody."""
    sid, _ = ssc_student(world.a)
    section = str(world.a.ids["section_9a"])
    office = world.person("office_admin")  # holds student.read_sensitive
    res = api.call(
        office,
        "GET",
        f"{BASE}/slips",
        params={"section_id": section, "include_sensitive": "true"},
    )
    assert res.status_code == 200, res.text
    event = W.audit_events(admin_engine, world.a.tenant_id, "dq.readiness.slips_printed")[-1]
    assert event["summary"]["section_id"] == section
    assert event["summary"]["sensitive_shown"] >= 1
    assert str(sid) in event["summary"]["sensitive_student_ids"]
    assert len(event["summary"]["sensitive_student_ids"]) == event["summary"]["sensitive_shown"]
    staff = world.person("office_staff")  # no student.read_sensitive
    plain = api.call(staff, "GET", f"{BASE}/slips", params={"section_id": section})
    assert plain.status_code == 200
    latest = W.audit_events(admin_engine, world.a.tenant_id, "dq.readiness.slips_printed")[-1]
    assert latest["summary"]["sensitive_shown"] == 0
    assert "sensitive_student_ids" not in latest["summary"]


def test_SEC_045_slips_mask_restricted_values_unless_asked_for_with_step_up(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """SEC-045 (docs/07 §11, ADR-0021): the HTML slips mask Aadhaar-as-printed values by
    default, also for a holder of ``student.read_sensitive``. ``include_sensitive=true`` shows
    them, and needs that permission (403) and a recent sign-in with MFA (428), like the PDF."""
    name = fresh_name()
    aadhaar_name = name.replace(" Sai Kumar", " Saikumarr")
    sid, _ = ssc_student(world.a, name=name, aadhaar_name=aadhaar_name)
    section = str(world.a.ids["section_9a"])
    office = world.person("office_admin")  # holds student.read_sensitive
    for params in ({"student_id": str(sid)}, {"section_id": section}):
        masked = api.call(office, "GET", f"{BASE}/slips", params=params)
        assert masked.status_code == 200, masked.text
        assert "Saikumarr" not in masked.text
        event = W.audit_events(admin_engine, world.a.tenant_id, "dq.readiness.slips_printed")[-1]
        assert event["summary"]["sensitive_shown"] == 0
        shown = api.call(
            office, "GET", f"{BASE}/slips", params={**params, "include_sensitive": "true"}
        )
        assert shown.status_code == 200, shown.text
        assert "Saikumarr" in shown.text
        stale = api.call(
            office,
            "GET",
            f"{BASE}/slips",
            params={**params, "include_sensitive": "true"},
            auth_age_s=600,
        )
        assert stale.status_code == 428, stale.text
        no_mfa = api.call(
            office,
            "GET",
            f"{BASE}/slips",
            params={**params, "include_sensitive": "true"},
            mfa=False,
        )
        assert no_mfa.status_code in (403, 428)  # office_admin needs MFA for everything
    # check=true: every check, no page and no audit event (the screen confirms the sign-in).
    before = len(W.audit_events(admin_engine, world.a.tenant_id, "dq.readiness.slips_printed"))
    checked = api.call(
        office,
        "GET",
        f"{BASE}/slips",
        params={"student_id": str(sid), "include_sensitive": "true", "check": "true"},
    )
    assert checked.status_code == 204, checked.text
    assert checked.content == b""
    stale_check = api.call(
        office,
        "GET",
        f"{BASE}/slips",
        params={"student_id": str(sid), "include_sensitive": "true", "check": "true"},
        auth_age_s=600,
    )
    assert stale_check.status_code == 428
    after = W.audit_events(admin_engine, world.a.tenant_id, "dq.readiness.slips_printed")
    assert len(after) == before
    staff = world.person("office_staff")  # no student.read_sensitive
    refused = api.call(
        staff, "GET", f"{BASE}/slips", params={"student_id": str(sid), "include_sensitive": "true"}
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "sensitive_not_allowed"


@contextmanager
def _boards(
    admin: Engine, tenant_id: uuid.UUID, boards: list[str], class_boards: dict[str, str]
) -> Iterator[None]:
    """The school's boards and class map for one block (the world is shared)."""
    with admin.begin() as c:
        row = c.execute(
            text("SELECT boards, settings FROM core.tenants WHERE id = :t"), {"t": tenant_id}
        ).one()
        c.execute(
            text(
                "UPDATE core.tenants SET boards = :b, "
                "settings = settings || jsonb_build_object('class_boards', CAST(:cb AS jsonb)) "
                "WHERE id = :t"
            ),
            {"b": boards, "cb": json.dumps(class_boards), "t": tenant_id},
        )
    try:
        yield
    finally:
        with admin.begin() as c:
            c.execute(
                text("UPDATE core.tenants SET boards = :b, settings = :s WHERE id = :t"),
                {"b": list(row.boards), "s": json.dumps(row.settings), "t": tenant_id},
            )


def test_L_10_board_readiness_skips_classes_mapped_to_another_board(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """FR-TEN-021: a school with Class IX on CBSE does not check its Class IX students for the
    State Board SSC nominal roll: not in the summary, not in the default run scope, and a run
    on them stores no SSC finding. The portal (apaar) check still covers them."""
    name = fresh_name()
    sid, _ = ssc_student(world.a, name=name, udise_name=name + " Reddy")
    office = world.person("office_admin")
    with _boards(admin_engine, world.a.tenant_id, ["CBSE", "BSEAP"], {"IX": "CBSE"}):
        body = api.call(office, "GET", BASE).json()
        nine = {str(world.a.ids["section_9a"]), str(world.a.ids["section_9c"])}
        assert not nine & {s["section_id"] for s in body["sections"]}
        default = api.call(
            world.person("exam_coordinator"),
            "POST",
            f"{BASE}/runs",
            json={},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert default.status_code == 202, default.text
        assert default.json()["scope"]["class_ids"] == [str(world.a.ids["class_x"])]
        assert run(api, office, sid).status_code == 202
        ssc = [
            f
            for f in DS.findings(admin_engine, sid)
            if f["profile_key"] == SSC and f["status"] == "open"
        ]
        assert ssc == []
        listed = api.call(
            office,
            "GET",
            "/api/v1/dq/readiness/apaar/students",
            params={"section_id": str(world.a.ids["section_9a"])},
        ).json()
        mine = next(s for s in listed if s["student"]["id"] == str(sid))
        assert mine["status"] == "needs_school"
        ssc_list = api.call(
            office, "GET", f"{BASE}/students", params={"section_id": str(world.a.ids["section_9a"])}
        )
        assert ssc_list.status_code == 200, ssc_list.text
        assert str(sid) not in {s["student"]["id"] for s in ssc_list.json()}
