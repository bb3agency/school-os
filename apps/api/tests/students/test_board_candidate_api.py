"""Board candidate grid over HTTP (FO-10; FR-STU-026..029, FR-STU-035).

School A of the synthetic world declares no board, so every scheme applies to its classes IX and
X (FR-TEN-020) and the tests name the scheme. Synthetic data only.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.redaction import verhoeff_check_digit

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
W = SW.W


def grid_path(section_id: Any, scheme: str | None = "bseap-ssc") -> str:
    path = f"/api/v1/sections/{section_id}/board-candidates"
    return f"{path}?scheme={scheme}" if scheme else path


def candidate(school: Any, name: str, section_key: str = "section_9a") -> uuid.UUID:
    out: uuid.UUID = SW.create(school, name=name, section_key=section_key)
    return out


def values_of(admin: Engine, sid: uuid.UUID, key: str) -> list[tuple[str, str, bool]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT value_text, source, superseded_by IS NULL AS current "
                "FROM sis.attribute_values WHERE student_id = :s AND attribute_key = :k "
                "ORDER BY recorded_at"
            ),
            {"s": sid, "k": key},
        )
        return [(r.value_text, r.source, r.current) for r in rows]


def test_FR_STU_026_schemes_are_listed_for_readers(world: Any, api: Any) -> None:
    res = api.call(world.person("exam_coordinator"), "GET", "/api/v1/board-candidates/schemes")
    assert res.status_code == 200, res.text
    keys = {s["key"]: s for s in res.json()}
    assert set(keys) == {"bseap-ssc", "cbse-secondary"}
    assert keys["cbse-secondary"]["verified"] is False
    maths = keys["cbse-secondary"]["subjects"][0]
    assert {o["code"] for o in maths["options"]} == {"041", "241"}


def test_FR_STU_027_save_and_read_the_section_grid(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    school = world.a
    sid = candidate(school, "Synthetica Board Ready")
    admin = world.person("office_admin")
    body = {
        "scheme": "bseap-ssc",
        "source": "parent_form",
        "rows": [
            {
                "student_id": str(sid),
                "exam_medium": "Telugu",
                "first_language": "telugu",
                "second_language": "HINDI",
                "third_language": "ENGLISH",
                "photo_signature_ready": True,
            }
        ],
    }
    res = api.call(admin, "PUT", grid_path(school.ids["section_9a"], None), json=body)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["recorded"] == 5
    assert out["unchanged"] == 0
    row = next(r for r in out["grid"]["rows"] if r["student_id"] == str(sid))
    assert row["values"]["first_language"] == "TELUGU"
    assert row["values"]["exam_medium"] == "telugu"
    assert row["ready"] is True
    assert row["issues"] == []
    assert out["grid"]["scheme"]["key"] == "bseap-ssc"
    assert out["grid"]["class_code"] == "IX"
    # Stored per source; the photo tick is always a staff observation (manual_entry).
    assert values_of(admin_engine, sid, "first_language") == [("TELUGU", "parent_form", True)]
    assert values_of(admin_engine, sid, "photo_signature_ready") == [("yes", "manual_entry", True)]

    # Sending the same again changes nothing.
    again = api.call(admin, "PUT", grid_path(school.ids["section_9a"], None), json=body)
    assert again.json()["recorded"] == 0
    assert again.json()["unchanged"] == 5

    # Audit: one value event per recorded value, one summary event per save (same transaction).
    saved = W.audit_events(admin_engine, school.tenant_id, "student.board_candidate.saved")
    mine = [e for e in saved if e["resource_id"] == school.ids["section_9a"]]
    assert mine
    assert mine[-1]["summary"]["scheme"] == "bseap-ssc"
    assert {"recorded", "unchanged", "students", "source"} <= set(mine[-1]["summary"])
    assert "TELUGU" not in str(mine[-1]["summary"])

    # The class teacher of 9A reads the grid; the per-student view agrees.
    teacher = world.person("class_teacher")
    res = api.call(teacher, "GET", grid_path(school.ids["section_9a"]))
    assert res.status_code == 200, res.text
    assert any(r["student_id"] == str(sid) and r["ready"] for r in res.json()["rows"])
    one = api.call(teacher, "GET", f"/api/v1/students/{sid}/board-candidate")
    assert one.status_code == 200, one.text
    assert one.json()["ready"] is True
    assert one.json()["scheme"]["key"] in {"bseap-ssc", "cbse-secondary"}


def test_FR_STU_028_incomplete_choices_are_shown_not_corrected(world: Any, api: Any) -> None:
    school = world.a
    sid = candidate(school, "Synthetica Board Partial", "section_10a")
    admin = world.person("office_admin")
    body = {
        "scheme": "cbse-secondary",
        "rows": [
            {
                "student_id": str(sid),
                "first_language": "184",
                "second_language": "089",
                "board_subjects": ["086", "087"],
            }
        ],
    }
    res = api.call(admin, "PUT", grid_path(school.ids["section_10a"], None), json=body)
    assert res.status_code == 200, res.text
    row = next(r for r in res.json()["grid"]["rows"] if r["student_id"] == str(sid))
    assert row["values"]["board_subjects"] == "086,087"
    assert {(i["field"], i["reason"]) for i in row["issues"]} == {
        ("board_subjects", "group_missing"),
        ("photo_signature_ready", "photo"),
    }
    assert row["ready"] is False


def test_FR_STU_029_codes_outside_the_scheme_are_refused_and_nothing_is_kept(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    school = world.a
    good = candidate(school, "Synthetica Board Good")
    bad = candidate(school, "Synthetica Board Bad")
    before = len(W.audit_events(admin_engine, school.tenant_id, "student.board_candidate.saved"))
    body = {
        "scheme": "bseap-ssc",
        "rows": [
            {"student_id": str(good), "first_language": "TELUGU"},
            {"student_id": str(bad), "third_language": "FRENCH", "board_subjects": ["041"]},
        ],
    }
    res = api.call(
        world.person("office_admin"), "PUT", grid_path(school.ids["section_9a"], None), json=body
    )
    assert res.status_code == 422, res.text
    fields = {e["field"]: e["code"] for e in res.json()["errors"]}
    assert fields == {
        "rows.1.third_language": "not_in_scheme",
        "rows.1.board_subjects": "not_in_scheme",
    }
    assert values_of(admin_engine, good, "first_language") == []
    after = len(W.audit_events(admin_engine, school.tenant_id, "student.board_candidate.saved"))
    assert after == before

    # A student of another section, and a scheme of no board class, are refused too.
    other = candidate(school, "Synthetica Board Elsewhere", "section_9c")
    res = api.call(
        world.person("office_admin"),
        "PUT",
        grid_path(school.ids["section_9a"], None),
        json={
            "scheme": "bseap-ssc",
            "rows": [{"student_id": str(other), "first_language": "TELUGU"}],
        },
    )
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "not_in_section"
    res = api.call(world.person("office_admin"), "GET", grid_path(school.ids["section_9a"], "nope"))
    assert res.status_code == 422


def test_FR_STU_035_authz_denied_out_of_scope_and_other_school(world: Any, api: Any) -> None:
    school = world.a
    sid = candidate(school, "Synthetica Board Authz")
    body = {"scheme": "bseap-ssc", "rows": [{"student_id": str(sid), "first_language": "TELUGU"}]}
    path = grid_path(school.ids["section_9a"], None)
    # Readers without student.update_nonidentity cannot save (403).
    for role in ("exam_coordinator", "accountant", "class_teacher", "auditor_readonly"):
        res = api.call(world.person(role), "PUT", path, json=body)
        assert res.status_code == 403, (role, res.text)
    # The class teacher of 9A does not reach 9C (404, not 403: no existence leak).
    res = api.call(world.person("class_teacher"), "GET", grid_path(school.ids["section_9c"]))
    assert res.status_code == 404
    # Another school's section and student are 404.
    b_section = world.b.ids["section_9a"]
    res = api.call(world.person("office_admin"), "GET", grid_path(b_section))
    assert res.status_code == 404
    res = api.call(world.person("office_admin"), "PUT", grid_path(b_section, None), json=body)
    assert res.status_code == 404
    b_student = SW.create(world.b, name="Synthetica Board Other School", section_key="section_9a")
    res = api.call(
        world.person("office_admin"), "GET", f"/api/v1/students/{b_student}/board-candidate"
    )
    assert res.status_code == 404


def test_FR_STU_035_full_aadhaar_in_the_body_is_refused(world: Any, api: Any) -> None:
    school = world.a
    sid = candidate(school, "Synthetica Board Aadhaar")
    body12 = "56781234567"
    number = body12 + verhoeff_check_digit(body12)
    body = {"scheme": "bseap-ssc", "rows": [{"student_id": str(sid), "exam_medium": number}]}
    res = api.call(
        world.person("office_admin"), "PUT", grid_path(school.ids["section_9a"], None), json=body
    )
    assert res.status_code == 422
