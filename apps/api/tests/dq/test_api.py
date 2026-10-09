"""Data-quality routes (docs/09 Data quality; US-501, US-502, FR-DQ-020, SEC-005, SEC-008,
SEC-015). Synthetic data only."""

from __future__ import annotations

import sys
import typing
import uuid
from typing import Any

import pytest

from app.core.languages import contains_telugu
from app.core.redaction import verhoeff_check_digit
from app.dq.rules import load_rules
from app.dq.schemas import RuleId

pytestmark = pytest.mark.db
DS = sys.modules["sos_test_dq_support"]
SECRET = "Bhimavarapu Surya Chandra"


@pytest.fixture(autouse=True)
def _keys(keyring: None) -> None:
    return None


def test_FR_DQ_001_rule_filter_literal_matches_the_catalog() -> None:
    assert set(typing.get_args(RuleId)) == set(load_rules())


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_US_501_run_then_list_findings_with_explanations(world: Any, api: Any) -> None:
    sid = DS.student(world.a, extra=DS.aadhaar(name=SECRET, gender="female"))
    who = world.person("exam_coordinator")
    body = {"scope": {"student_ids": [str(sid)]}, "profile_key": "cisce-registration-2026"}
    key = {"Idempotency-Key": f"dq-{uuid.uuid4()}"}
    res = api.call(who, "POST", "/api/v1/dq/runs", json=body, headers=key)
    assert res.status_code == 202, res.text
    run = res.json()
    assert run["status"] == "completed"
    assert res.headers["Location"] == f"/api/v1/dq/runs/{run['id']}"
    again = api.call(who, "POST", "/api/v1/dq/runs", json=body, headers=key)
    assert (again.status_code, again.json()["id"]) == (202, run["id"])
    got = api.call(who, "GET", f"/api/v1/dq/runs/{run['id']}")
    assert got.json()["stats"]["students"] == 1
    listed = api.call(
        who,
        "GET",
        "/api/v1/dq/findings",
        params={"student_id": str(sid), "profile_key": "cisce-registration-2026"},
    )
    assert listed.status_code == 200
    items = listed.json()["data"]
    assert items[0]["severity"] == "blocker"  # most severe first
    rules = {i["rule_id"] for i in items}
    assert {"DQ-001", "DQ-003", "DQ-005"} <= rules
    dq5 = next(i for i in items if i["rule_id"] == "DQ-005")
    assert dq5["explanation"]["en"].startswith("Required for CISCE registration 2026: ")
    assert "CISCE నమోదు 2026" in dq5["explanation"]["te"]
    assert SECRET.split(maxsplit=1)[0] not in listed.text
    one = api.call(who, "GET", f"/api/v1/dq/findings/{items[0]['id']}")
    assert one.headers["ETag"] == f'W/"{items[0]["version"]}"'


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_US_501_summary_rules_and_profiles(world: Any, api: Any) -> None:
    who = world.person("principal")
    rules = api.call(who, "GET", "/api/v1/dq/rules").json()
    assert [r["id"] for r in rules] == [f"DQ-{i:03d}" for i in (*range(1, 13), 21, 22)]
    assert all(r["explanation"]["en"] and r["explanation"]["te"] for r in rules)
    assert rules[0]["routes"][0] == {
        "code": "ROUTE-UIDAI",
        "en": "Parent should correct Aadhaar with UIDAI.",
        "te": "తల్లిదండ్రులు UIDAI ద్వారా ఆధార్‌లో సవరణ చేయించుకోవాలి.",
    }
    profiles = {p["key"] for p in api.call(who, "GET", "/api/v1/dq/profiles").json()}
    assert profiles == {"cisce-registration-2026", "udise-plus"}
    summary = api.call(
        who,
        "GET",
        "/api/v1/dq/summary",
        params={"profile_key": "udise-plus", "section_ids": [str(world.a.ids["section_9a"])]},
    )
    assert summary.status_code == 200
    body = summary.json()
    assert body["blockers"] + body["warnings"] == sum(body["by_severity"].values())
    bad = api.call(who, "GET", "/api/v1/dq/summary", params={"profile_key": "no-such-board"})
    assert bad.status_code == 422


def test_ADR_0036_findings_rules_and_profiles_show_no_telugu_while_telugu_is_hidden(
    world: Any, api: Any
) -> None:
    sid = DS.student(world.a, extra=DS.aadhaar(name=SECRET, gender="female"))
    who = world.person("exam_coordinator")
    body = {"scope": {"student_ids": [str(sid)]}, "profile_key": "cisce-registration-2026"}
    res = api.call(
        who, "POST", "/api/v1/dq/runs", json=body, headers={"Idempotency-Key": str(uuid.uuid4())}
    )
    assert res.status_code == 202, res.text
    listed = api.call(
        who,
        "GET",
        "/api/v1/dq/findings",
        params={"student_id": str(sid), "profile_key": "cisce-registration-2026"},
    )
    items = listed.json()["data"]
    assert items, "the synthetic student has findings"
    assert all(i["explanation"]["en"] and i["explanation"]["te"] == "" for i in items)
    assert all(r["te"] == "" for i in items for r in i["routes"])
    assert not contains_telugu(listed.text)
    rules = api.call(who, "GET", "/api/v1/dq/rules")
    assert all(r["explanation"]["te"] == "" for r in rules.json())
    assert not contains_telugu(rules.text)
    profiles = api.call(who, "GET", "/api/v1/dq/profiles")
    assert all(p["label_te"] == "" for p in profiles.json())
    assert not contains_telugu(profiles.text)
    attributes = api.call(who, "GET", "/api/v1/attributes")
    assert attributes.status_code == 200, attributes.text
    assert all(a["label_te"] == "" for a in attributes.json())
    assert not contains_telugu(attributes.text)


def test_US_501_run_validation(world: Any, api: Any) -> None:
    who = world.person("office_admin")
    unknown = api.call(who, "POST", "/api/v1/dq/runs", json={"profile_key": "no-such-board"})
    assert (unknown.status_code, unknown.json()["errors"][0]["code"]) == (422, "unknown_profile")
    other_school = api.call(
        who,
        "POST",
        "/api/v1/dq/runs",
        json={"scope": {"section_ids": [str(world.b.ids["section_9a"])]}},
    )
    assert other_school.status_code == 422
    assert other_school.json()["errors"][0]["field"] == "scope.section_ids.0"
    two = api.call(
        who,
        "POST",
        "/api/v1/dq/runs",
        json={
            "scope": {
                "section_ids": [str(world.a.ids["section_9a"])],
                "batch_id": str(uuid.uuid4()),
            }
        },
    )
    assert two.status_code == 422


def test_AA_18_run_scope_outside_the_callers_reach_answers_like_an_unknown_id(
    world: Any, api: Any
) -> None:
    """Starting a run needs ``dq.findings.resolve`` (owner decision 2026-10-07), which no system
    role holds scoped. A read-only class teacher gets the same 403 for an existing section and
    an unknown id; a custom role with ``dq.findings.resolve`` scoped to 9A cannot tell an
    existing section or class outside its scope from an id that does not exist: both are 422
    ``not_found`` on the same field (AA-18)."""
    import dataclasses

    from app.core.errors import ValidationFailed
    from app.dq import service as dq
    from app.dq.schemas import RunCreate, RunScopeIn

    teacher = world.person("class_teacher")
    refused = [
        api.call(teacher, "POST", "/api/v1/dq/runs", json={"scope": {"section_ids": [str(x)]}})
        for x in (world.a.ids["section_9c"], uuid.uuid4())
    ]
    assert [r.status_code for r in refused] == [403, 403]
    assert refused[0].json()["code"] == refused[1].json()["code"]

    base = DS.ctx(world.a, "class_teacher")
    custom = dataclasses.replace(
        base,
        permissions=base.permissions | {dq.RESOLVE},
        scoped_permissions=base.scoped_permissions | {dq.RESOLVE},
    )

    def run(scope: dict[str, Any]) -> Any:
        return DS.call(world.a, dq.request_run, RunCreate(scope=RunScopeIn(**scope)), as_ctx=custom)

    def errors(scope: dict[str, Any]) -> Any:
        with pytest.raises(ValidationFailed) as exc:
            run(scope)
        return exc.value.errors

    for field, existing in (
        ("section_ids", world.a.ids["section_9c"]),
        ("class_ids", world.a.ids["class_x"]),
    ):
        outside, unknown = errors({field: [existing]}), errors({field: [uuid.uuid4()]})
        assert outside == unknown
        [error] = outside
        assert (error["field"], error["code"]) == (f"scope.{field}.0", "not_found")
    # The caller's own section still runs.
    assert run({"section_ids": [world.a.ids["section_9a"]]}).id is not None
    assert run({"class_ids": [world.a.ids["class_ix"]]}).id is not None


def test_FR_DQ_020_resolve_needs_a_note_or_change_request(world: Any, api: Any) -> None:
    fid = DS.high_finding(world.a)
    who = world.person("office_staff")
    empty = api.call(who, "POST", f"/api/v1/dq/findings/{fid}/resolve", json={})
    assert empty.status_code == 422
    body = "45678901234"
    aadhaar = body + verhoeff_check_digit(body)
    leaked = api.call(
        who, "POST", f"/api/v1/dq/findings/{fid}/resolve", json={"note": f"See {aadhaar}"}
    )
    assert (leaked.status_code, leaked.json()["errors"][0]["code"]) == (
        422,
        "aadhaar_full_number_rejected",
    )
    stale = api.call(
        who,
        "POST",
        f"/api/v1/dq/findings/{fid}/resolve",
        json={"note": "Fixed on the register"},
        headers={"If-Match": 'W/"999"'},
    )
    assert stale.status_code == 412
    ok = api.call(
        who, "POST", f"/api/v1/dq/findings/{fid}/resolve", json={"note": "Fixed on the register"}
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "resolved"
    twice = api.call(who, "POST", f"/api/v1/dq/findings/{fid}/resolve", json={"note": "again"})
    assert (twice.status_code, twice.json()["code"]) == (409, "finding_not_open")


def test_SEC_005_waive_needs_step_up_and_a_reason(world: Any, api: Any) -> None:
    fid = DS.blocker_finding(world.a)
    who = world.person("office_admin")
    path = f"/api/v1/dq/findings/{fid}/waive"
    stale = api.call(who, "POST", path, json={"reason": "Accepted by board"}, auth_age_s=301)
    assert (stale.status_code, stale.json()["code"]) == (428, "step_up_required")
    assert api.call(who, "POST", path, json={"reason": "x"}).status_code == 422
    denied = api.call(world.person("office_staff"), "POST", path, json={"reason": "Accepted"})
    assert denied.status_code == 403
    ok = api.call(who, "POST", path, json={"reason": "Accepted by board"})
    assert ok.status_code == 200
    assert (ok.json()["status"], ok.json()["waived_reason"]) == ("waived", "Accepted by board")


def test_DL_06_resolving_a_blocker_needs_the_waive_permission_and_step_up(
    world: Any, api: Any
) -> None:
    """FR-CERT-002 / FR-DQ-020: a blocker is cleared only by the change request that corrects it
    or by a waive holder with a fresh MFA sign-in; "resolve with a note" is no way around it."""
    fid = DS.blocker_finding(world.a)
    path = f"/api/v1/dq/findings/{fid}/resolve"
    note = {"note": "Checked the admission register"}
    staff = api.call(world.person("office_staff"), "POST", path, json=note)
    assert (staff.status_code, staff.json()["code"]) == (403, "blocker_needs_waive")
    cr = api.call(
        world.person("office_staff"),
        "POST",
        path,
        json={"change_request_id": str(uuid.uuid4())},
    )
    assert (cr.status_code, cr.json()["code"]) == (403, "blocker_needs_waive")
    admin = world.person("office_admin")  # holds dq.findings.waive
    stale = api.call(admin, "POST", path, json=note, auth_age_s=301)
    assert (stale.status_code, stale.json()["code"]) == (428, "step_up_required")
    still = api.call(admin, "GET", f"/api/v1/dq/findings/{fid}")
    assert still.json()["status"] == "open"
    ok = api.call(admin, "POST", path, json=note)
    assert ok.status_code == 200, ok.text
    assert (ok.json()["status"], ok.json()["resolution"]) == ("resolved", "note")


def test_DL_06_ordinary_findings_still_resolve_with_a_note(world: Any, api: Any) -> None:
    fid = DS.high_finding(world.a)
    staff = world.person("office_staff")  # no dq.findings.waive
    path = f"/api/v1/dq/findings/{fid}/resolve"
    ok = api.call(staff, "POST", path, json={"note": "Fixed on the register"}, auth_age_s=3600)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "resolved"


def test_SEC_015_class_teacher_sees_only_own_sections(world: Any, api: Any) -> None:
    inside = DS.high_finding(world.a)
    outside = DS.high_finding(world.a, section_key="section_9c")
    teacher = world.person("class_teacher")
    listed = api.call(teacher, "GET", "/api/v1/dq/findings", params={"limit": 200})
    ids = {f["id"] for f in listed.json()["data"]}
    assert str(inside) in ids
    assert str(outside) not in ids
    assert api.call(teacher, "GET", f"/api/v1/dq/findings/{outside}").status_code == 404
    assert api.call(teacher, "GET", f"/api/v1/dq/findings/{uuid.uuid4()}").status_code == 404
    other = api.call(
        teacher,
        "GET",
        "/api/v1/dq/findings",
        params={"section_id": str(world.a.ids["section_9c"])},
    )
    assert other.json()["data"] == []


@pytest.mark.parametrize("role", ["auditor_readonly", "class_teacher"])
def test_SEC_003_read_only_holders_cannot_start_a_check_run(
    world: Any, api: Any, role: str
) -> None:
    """App-logic hardening (owner decision 2026-10-07): starting a run is work on the school's
    findings, so it needs ``dq.findings.resolve``; readers keep reading. Value changes still
    re-check students automatically (event runs)."""
    sid = DS.student(world.a)
    body = {"scope": {"student_ids": [str(sid)]}}
    res = api.call(world.person(role), "POST", "/api/v1/dq/runs", json=body)
    assert res.status_code == 403, res.text
    assert api.call(world.person(role), "GET", "/api/v1/dq/findings").status_code == 200
    ok = api.call(world.person("office_staff"), "POST", "/api/v1/dq/runs", json=body)
    assert ok.status_code == 202, ok.text


def test_SEC_001_lists_never_show_other_school(world: Any, api: Any) -> None:
    b = DS.high_finding(world.b)
    res = api.call(world.person("owner"), "GET", "/api/v1/dq/findings", params={"limit": 200})
    assert str(b) not in {f["id"] for f in res.json()["data"]}
    filtered = api.call(
        world.person("owner"),
        "GET",
        "/api/v1/dq/findings",
        params={"section_id": str(world.b.ids["section_9a"])},
    )
    assert filtered.json()["data"] == []


def test_SEC_008_dq_calls_do_not_log_personal_data(
    world: Any, api: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    sid = DS.student(
        world.a,
        name="Jonnalagadda Synthetica Hemanth",
        extra=DS.aadhaar(name=SECRET, dob="2011-01-05", gender="female", last4="7319"),
    )
    who = world.person("office_admin")
    api.call(who, "POST", "/api/v1/dq/runs", json={"scope": {"student_ids": [str(sid)]}})
    listed = api.call(who, "GET", "/api/v1/dq/findings", params={"student_id": str(sid)})
    fid = listed.json()["data"][0]["id"]
    api.call(who, "POST", f"/api/v1/dq/findings/{fid}/resolve", json={"note": f"{SECRET} ok"})
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "dq.run.completed" in logs
    for secret in (*SECRET.split(), "Jonnalagadda", "Hemanth", "2011-01-05", "7319"):
        assert secret not in logs
