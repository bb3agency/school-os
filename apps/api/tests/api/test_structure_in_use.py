"""``in_use`` on academic years, classes and sections: whether a row has active enrolments, so
archiving it answers 409 ``structure_in_use`` (US-202, FR-TEN-010; invariants 1, 2 and 3).
Yes/no only, never counts or names. Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]


@pytest.fixture
def school(api: Any, admin_engine: Engine) -> Any:
    tid = W.provision_school()
    s = W.School(tid)
    s.people["owner"] = W.add_member(admin_engine, tid, ["owner"])
    s.people["office_admin"] = W.add_member(admin_engine, tid, ["office_admin"])
    s.people["office_staff"] = W.add_member(admin_engine, tid, ["office_staff"])
    W.build_structure(s, s.people["owner"])
    return s


def _enrol(admin: Engine, school: Any, section_key: str) -> uuid.UUID:
    student, enrolment = uuid.uuid4(), uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text("INSERT INTO sis.students (id, tenant_id) VALUES (:s, :t)"),
            {"s": student, "t": school.tenant_id},
        )
        c.execute(
            text(
                "INSERT INTO sis.enrollments (id, tenant_id, student_id, section_id, "
                "academic_year_id) VALUES (:e, :t, :s, :sec, :y)"
            ),
            {
                "e": enrolment,
                "t": school.tenant_id,
                "s": student,
                "sec": school.ids[section_key],
                "y": school.ids["year"],
            },
        )
    return enrolment


def _in_use(api: Any, who: Any, base: str) -> dict[str, Any]:
    res = api.call(who, "GET", base, params={"limit": 200, "include_archived": True})
    assert res.status_code == 200, res.text
    return {i["id"]: i["in_use"] for i in res.json()["data"]}


def test_FR_TEN_010_structure_rows_report_in_use(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    staff = school.people["office_staff"]
    ids = school.ids
    years = _in_use(api, staff, "/api/v1/academic-years")
    classes = _in_use(api, staff, "/api/v1/classes")
    sections = _in_use(api, staff, "/api/v1/sections")
    assert years == {str(ids["year"]): False, str(ids["old_year"]): False}
    assert set(classes.values()) == {False}
    assert set(sections.values()) == {False}

    enrolment = _enrol(admin_engine, school, "section_9a")
    assert _in_use(api, staff, "/api/v1/academic-years") == {
        str(ids["year"]): True,
        str(ids["old_year"]): False,
    }
    classes = _in_use(api, staff, "/api/v1/classes")
    assert classes[str(ids["class_ix"])] is True
    assert classes[str(ids["class_x"])] is False
    sections = _in_use(api, staff, "/api/v1/sections")
    assert sections[str(ids["section_9a"])] is True
    assert sections[str(ids["section_9c"])] is False
    for path, expected in (
        (f"/api/v1/academic-years/{ids['year']}", True),
        (f"/api/v1/classes/{ids['class_ix']}", True),
        (f"/api/v1/sections/{ids['section_9a']}", True),
        (f"/api/v1/sections/{ids['section_10a']}", False),
    ):
        one = api.call(staff, "GET", path)
        assert one.status_code == 200, one.text
        assert one.json()["in_use"] is expected, path

    # An ended enrolment no longer counts.
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE sis.enrollments SET status = 'transferred' WHERE id = :e"),
            {"e": enrolment},
        )
    assert _in_use(api, staff, "/api/v1/sections")[str(ids["section_9a"])] is False


def test_FR_TEN_010_in_use_never_counts_other_schools(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    other = W.School(W.provision_school())
    other.people["owner"] = W.add_member(admin_engine, other.tenant_id, ["owner"])
    W.build_structure(other, other.people["owner"])
    _enrol(admin_engine, other, "section_9a")
    sections = _in_use(api, school.people["owner"], "/api/v1/sections")
    assert str(other.ids["section_9a"]) not in sections
    assert set(sections.values()) == {False}
    res = api.call(school.people["owner"], "GET", f"/api/v1/sections/{other.ids['section_9a']}")
    assert res.status_code == 404
