"""Archive and unarchive academic years, classes and sections (US-202, FR-TEN-010; 0023_api_gaps).

Archived rows disappear from the lists (unless ``include_archived=true``) but stay readable by
id and keep every reference; the current year and anything with an active enrolment cannot be
archived. Synthetic data only.
"""

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
    """A fresh school with an owner, an office admin (structure manager) and office staff."""
    tid = W.provision_school()
    s = W.School(tid)
    s.people["owner"] = W.add_member(admin_engine, tid, ["owner"])
    s.people["office_admin"] = W.add_member(admin_engine, tid, ["office_admin"])
    s.people["office_staff"] = W.add_member(admin_engine, tid, ["office_staff"])
    W.build_structure(s, s.people["owner"])
    return s


def _etag(api: Any, who: Any, path: str) -> str:
    res = api.call(who, "GET", path)
    assert res.status_code == 200, res.text
    etag: str = res.headers["ETag"]
    return etag


def _post(api: Any, who: Any, path: str, etag: str | None) -> Any:
    headers = {"If-Match": etag} if etag is not None else {}
    return api.call(who, "POST", path, headers=headers)


def _enrol(admin: Engine, school: Any, section_key: str, *, year_key: str = "year") -> uuid.UUID:
    """A synthetic student with an active enrolment in ``section_key`` (admin insert)."""
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
                "y": school.ids[year_key],
            },
        )
    return enrolment


def _end(admin: Engine, enrolment: uuid.UUID) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE sis.enrollments SET status = 'transferred' WHERE id = :e"),
            {"e": enrolment},
        )


LISTS = {
    "academic-years": ("old_year", "/api/v1/academic-years", "academic_year"),
    "classes": ("class_x", "/api/v1/classes", "class"),
    "sections": ("section_9c", "/api/v1/sections", "section"),
}


@pytest.mark.parametrize("kind", sorted(LISTS))
def test_US_202_archive_hides_from_lists_and_unarchive_restores(
    school: Any, api: Any, admin_engine: Engine, kind: str
) -> None:
    key, base, resource = LISTS[kind]
    admin = school.people["office_admin"]
    rid = school.ids[key]
    one = f"{base}/{rid}"
    etag = _etag(api, admin, one)

    res = _post(api, admin, f"{one}/archive", etag)
    assert res.status_code == 200, res.text
    assert res.json()["archived_at"] is not None
    assert res.headers["ETag"] != etag
    listed = api.call(admin, "GET", base, params={"limit": 200}).json()["data"]
    assert str(rid) not in {i["id"] for i in listed}
    everything = api.call(admin, "GET", base, params={"limit": 200, "include_archived": True})
    assert str(rid) in {i["id"] for i in everything.json()["data"]}
    assert api.call(admin, "GET", one).json()["archived_at"] is not None, "still readable by id"

    # Archiving again is a no-op (no second audit event).
    again = _post(api, admin, f"{one}/archive", res.headers["ETag"])
    assert again.status_code == 200
    assert again.headers["ETag"] == res.headers["ETag"]

    back = _post(api, admin, f"{one}/unarchive", res.headers["ETag"])
    assert back.status_code == 200, back.text
    assert back.json()["archived_at"] is None
    listed = api.call(admin, "GET", base, params={"limit": 200}).json()["data"]
    assert str(rid) in {i["id"] for i in listed}

    events = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id)
        if e["resource_id"] == rid and e["action"].endswith("archived")
    ]
    assert [e["action"] for e in events] == [f"{resource}.archived", f"{resource}.unarchived"]
    assert all(e["summary"] == {} for e in events)
    assert all(e["actor_id"] == admin.user_id for e in events)


def test_US_202_current_year_cannot_be_archived(school: Any, api: Any) -> None:
    admin = school.people["office_admin"]
    one = f"/api/v1/academic-years/{school.ids['year']}"
    res = _post(api, admin, f"{one}/archive", _etag(api, admin, one))
    assert res.status_code == 409
    assert res.json()["code"] == "academic_year_current"


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("/api/v1/sections", "section_9a"),
        ("/api/v1/classes", "class_ix"),
        ("/api/v1/academic-years", "old_year"),
    ],
)
def test_US_202_active_enrolment_blocks_archive(
    school: Any, api: Any, admin_engine: Engine, path: str, key: str
) -> None:
    admin = school.people["office_admin"]
    # Old-year enrolments need an old-year section of class IX.
    if key == "old_year":
        with W.tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
            sec = W.tenancy.create_section(
                db,
                W.SectionCreate(
                    academic_year_id=school.ids["old_year"],
                    class_id=school.ids["class_ix"],
                    name="Z",
                ),
            )
        school.ids["section_old"] = sec.id
        enrolment = _enrol(admin_engine, school, "section_old", year_key="old_year")
    else:
        enrolment = _enrol(admin_engine, school, "section_9a")
    one = f"{path}/{school.ids[key]}"
    before = len(W.audit_events(admin_engine, school.tenant_id))
    res = _post(api, admin, f"{one}/archive", _etag(api, admin, one))
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "structure_in_use"
    assert len(W.audit_events(admin_engine, school.tenant_id)) == before, "rolled back"
    assert api.call(admin, "GET", one).json()["archived_at"] is None

    _end(admin_engine, enrolment)
    res = _post(api, admin, f"{one}/archive", _etag(api, admin, one))
    assert res.status_code == 200, res.text


def test_US_202_archived_year_and_class_refuse_new_use(school: Any, api: Any) -> None:
    admin = school.people["office_admin"]
    year = f"/api/v1/academic-years/{school.ids['old_year']}"
    archived = _post(api, admin, f"{year}/archive", _etag(api, admin, year))
    assert archived.status_code == 200
    current = _post(api, admin, f"{year}/make-current", archived.headers["ETag"])
    assert current.status_code == 409
    assert current.json()["code"] == "structure_archived"

    klass = f"/api/v1/classes/{school.ids['class_x']}"
    assert _post(api, admin, f"{klass}/archive", _etag(api, admin, klass)).status_code == 200
    res = api.call(
        admin,
        "POST",
        "/api/v1/sections",
        json={
            "academic_year_id": str(school.ids["year"]),
            "class_id": str(school.ids["class_x"]),
            "name": "Q",
        },
    )
    assert res.status_code == 409
    assert res.json()["code"] == "structure_archived"


def test_US_202_AC3_archive_needs_structure_permission_and_if_match(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    admin, staff = school.people["office_admin"], school.people["office_staff"]
    one = f"/api/v1/classes/{school.ids['class_x']}"
    etag = _etag(api, admin, one)
    for action in ("archive", "unarchive"):
        assert _post(api, staff, f"{one}/{action}", etag).status_code == 403
    missing = _post(api, admin, f"{one}/archive", None)
    assert missing.status_code == 400
    assert missing.json()["code"] == "if_match_required"
    assert _post(api, admin, f"{one}/archive", 'W/"999"').status_code == 412
    assert api.call(admin, "GET", one).json()["archived_at"] is None


def test_SEC_001_archive_other_school_row_is_404(
    school: Any, world: Any, api: Any, admin_engine: Engine
) -> None:
    admin = school.people["office_admin"]
    before = W.audit_events(admin_engine, world.b.tenant_id)
    for path, key in (
        ("/api/v1/academic-years", "old_year"),
        ("/api/v1/classes", "class_x"),
        ("/api/v1/sections", "section_10a"),
    ):
        for action in ("archive", "unarchive"):
            res = _post(api, admin, f"{path}/{world.b.ids[key]}/{action}", 'W/"1"')
            assert res.status_code == 404, (path, action, res.text)
    assert W.audit_events(admin_engine, world.b.tenant_id) == before
