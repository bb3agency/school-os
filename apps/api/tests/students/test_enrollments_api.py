"""Enrolment list/correct/close and guardian unlink over HTTP (docs/09 Students; FR-TEN-010,
FR-STU-008, SEC-015; invariant 7). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
W = SW.W
BASE = "/api/v1/students"


def _if_match(version: int) -> dict[str, str]:
    return {"If-Match": f'W/"{version}"'}


def _student(world: Any, section_key: str = "section_9a") -> uuid.UUID:
    sid: uuid.UUID = SW.create(world.a, name="Synthetica Enrolment Case", section_key=section_key)
    return sid


def _only_enrolment(api: Any, world: Any, sid: uuid.UUID) -> dict[str, Any]:
    res = api.call(world.person("office_admin"), "GET", f"{BASE}/{sid}/enrollments")
    assert res.status_code == 200, res.text
    (enrolment,) = res.json()
    return dict(enrolment)


def test_FR_TEN_010_list_enrolments_is_scoped(world: Any, api: Any) -> None:
    in_9a, in_9c = _student(world), _student(world, "section_9c")
    enrolment = _only_enrolment(api, world, in_9a)
    assert enrolment["section_id"] == str(world.a.ids["section_9a"])
    assert enrolment["status"] == "active"
    assert enrolment["academic_year_id"] == str(world.a.ids["year"])
    teacher = world.person("class_teacher")  # scoped to 9A
    assert api.call(teacher, "GET", f"{BASE}/{in_9a}/enrollments").status_code == 200
    assert api.call(teacher, "GET", f"{BASE}/{in_9c}/enrollments").status_code == 404
    b_owner = world.b.people["owner"]
    assert api.call(b_owner, "GET", f"{BASE}/{in_9a}/enrollments").status_code == 404


def _scoped_editor(
    world: Any,
    admin_engine: Engine,
    permissions: tuple[str, ...] = ("student.read_basic", "student.update_nonidentity"),
) -> Any:
    """A member of school A with a custom role (always scoped) limited to section 9A."""
    tid = world.a.tenant_id
    key = f"scoped_editor_{uuid.uuid4().hex[:8]}"
    role_id = uuid.uuid4()
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te) "
                "VALUES (:r, :t, :k, 'Synthetic scoped editor', 'కృత్రిమ సంపాదకుడు')"
            ),
            {"r": role_id, "t": tid, "k": key},
        )
        for perm in permissions:
            c.execute(
                text(
                    "INSERT INTO core.role_permissions (tenant_id, role_id, permission_key) "
                    "VALUES (:t, :r, :p)"
                ),
                {"t": tid, "r": role_id, "p": perm},
            )
    return W.add_member(admin_engine, tid, [key], scopes=[("section", world.a.ids["section_9a"])])


def test_SEC_015_scoped_editor_cannot_link_a_guardian_of_a_student_outside_scope(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """Linking an existing guardian (siblings, FR-STU-008) needs that guardian to belong to a
    student the caller reaches; before, a 9A editor with read_sensitive could link a 9C
    student's guardian to a 9A student and then reveal its C3 phone and address."""
    editor = _scoped_editor(
        world,
        admin_engine,
        ("student.read_basic", "student.update_nonidentity", "student.read_sensitive"),
    )
    in_9a, in_9c, sibling = _student(world), _student(world, "section_9c"), _student(world)
    foreign = SW.add_guardian(world.a, in_9c, full_name="Synthetica Other Parent")
    res = api.call(
        editor,
        "POST",
        f"{BASE}/{in_9a}/guardians",
        json={"relationship": "mother", "guardian_id": str(foreign)},
    )
    assert res.status_code == 422, res.text
    assert res.json()["errors"][0]["code"] == "not_found"
    reachable = SW.add_guardian(world.a, sibling, full_name="Synthetica Reachable Parent")
    res = api.call(
        editor,
        "POST",
        f"{BASE}/{in_9a}/guardians",
        json={"relationship": "father", "guardian_id": str(reachable)},
    )
    assert res.status_code == 201, res.text


def test_SEC_015_scoped_editor_cannot_enrol_into_a_section_outside_scope(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """A scoped ``student.update_nonidentity`` holder (custom roles are always scoped) may move
    a student only between sections they reach, as ``PATCH .../enrollments/{id}`` already
    requires; before, ``POST .../enrollments`` checked only the student, so a 9A editor could
    move a 9A student into 9C (or any year)."""
    editor = _scoped_editor(world, admin_engine)
    sid = _student(world)
    res = api.call(
        editor,
        "POST",
        f"{BASE}/{sid}/enrollments",
        json={"section_id": str(world.a.ids["section_9c"])},
    )
    assert res.status_code == 422, res.text
    assert res.json()["errors"][0] == {
        "field": "section_id",
        "code": "not_found",
        "message_key": "errors.not_found",
    }
    assert _only_enrolment(api, world, sid)["section_id"] == str(world.a.ids["section_9a"])
    # Within scope the request passes the scope check (and meets the ordinary state rule).
    res = api.call(
        editor,
        "POST",
        f"{BASE}/{sid}/enrollments",
        json={"section_id": str(world.a.ids["section_9a"]), "roll_no": "41"},
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "already_enrolled"


def test_FR_TEN_010_patch_roll_number_and_section_with_if_match(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    sid = _student(world)
    enrolment = _only_enrolment(api, world, sid)
    path = f"{BASE}/{sid}/enrollments/{enrolment['id']}"
    admin = world.person("office_admin")
    missing = api.call(admin, "PATCH", path, json={"roll_no": "7"})
    assert missing.status_code == 400
    assert missing.json()["code"] == "if_match_required"
    res = api.call(
        admin,
        "PATCH",
        path,
        json={"roll_no": "7", "section_id": str(world.a.ids["section_9c"])},
        headers=_if_match(enrolment["version"]),
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["roll_no"] == "7"
    assert out["section_id"] == str(world.a.ids["section_9c"])
    assert out["id"] == enrolment["id"], "a correction, not a new enrolment"
    assert res.headers["ETag"] == f'W/"{out["version"]}"'
    stale = api.call(admin, "PATCH", path, json={"roll_no": "8"}, headers=_if_match(1))
    assert stale.status_code == 412
    assert stale.json()["code"] == "precondition_failed"
    other_class = api.call(
        admin,
        "PATCH",
        path,
        json={"section_id": str(world.a.ids["section_10a"])},
        headers=_if_match(out["version"]),
    )
    assert other_class.status_code == 422
    assert other_class.json()["errors"][0]["code"] == "different_class_or_year"
    events = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "enrollment.updated")
        if e["summary"]["student_id"] == str(sid)
    ]
    assert len(events) == 1
    assert events[0]["summary"]["fields"] == ["roll_no", "section_id"]
    assert set(events[0]["summary"]) == {"student_id", "fields", "from_section_id", "to_section_id"}


def test_FR_TEN_010_end_enrolment(world: Any, api: Any, admin_engine: Engine) -> None:
    sid = _student(world)
    enrolment = _only_enrolment(api, world, sid)
    path = f"{BASE}/{sid}/enrollments/{enrolment['id']}/end"
    staff = world.person("office_staff")
    early = api.call(
        staff,
        "POST",
        path,
        json={"ended_on": "2000-01-01"},
        headers=_if_match(enrolment["version"]),
    )
    assert early.status_code == 422
    assert early.json()["errors"][0]["code"] == "before_start"
    res = api.call(
        staff, "POST", path, json={"status": "completed"}, headers=_if_match(enrolment["version"])
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"
    assert res.json()["ended_on"] is not None
    again = api.call(staff, "POST", path, headers=_if_match(res.json()["version"]))
    assert again.status_code == 409
    assert again.json()["code"] == "enrollment_not_active"
    profile = api.call(staff, "GET", f"{BASE}/{sid}").json()
    assert profile["enrollment"] is None
    assert profile["status"] == "active", "closing an enrolment never changes the record status"
    (event,) = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "enrollment.ended")
        if e["summary"]["student_id"] == str(sid)
    ]
    assert event["summary"]["status"] == "completed"


def test_FR_TEN_010_enrolment_writes_need_update_permission(world: Any, api: Any) -> None:
    sid = _student(world, "section_10a")  # inside the subject teacher's class X
    enrolment = _only_enrolment(api, world, sid)
    path = f"{BASE}/{sid}/enrollments/{enrolment['id']}"
    teacher = world.person("teacher")
    assert api.call(teacher, "GET", f"{BASE}/{sid}/enrollments").status_code == 200
    denied = api.call(teacher, "PATCH", path, json={"roll_no": "3"}, headers=_if_match(1))
    assert denied.status_code == 403
    assert api.call(teacher, "POST", f"{path}/end", headers=_if_match(1)).status_code == 403
    # Another student's enrolment id under this student's path is not found.
    other = _only_enrolment(api, world, _student(world))
    wrong = api.call(
        world.person("office_admin"),
        "PATCH",
        f"{BASE}/{sid}/enrollments/{other['id']}",
        json={"roll_no": "3"},
        headers=_if_match(other["version"]),
    )
    assert wrong.status_code == 404


def _guardian_version(admin: Engine, guardian_id: uuid.UUID) -> int | None:
    with admin.connect() as c:
        value = c.execute(
            text("SELECT version FROM sis.guardians WHERE id = :g"), {"g": guardian_id}
        ).scalar_one_or_none()
    return int(value) if value is not None else None


def test_FR_STU_008_unlink_guardian(world: Any, api: Any, admin_engine: Engine) -> None:
    first, sibling = _student(world), _student(world)
    shared = SW.add_guardian(
        world.a, first, full_name="Synthetica Shared Parent", phone="9876512345"
    )
    own = SW.add_guardian(world.a, first, full_name="Synthetica Own Parent")
    admin = world.person("office_admin")
    linked = api.call(
        admin,
        "POST",
        f"{BASE}/{sibling}/guardians",
        json={"relationship": "father", "guardian_id": str(shared)},
    )
    assert linked.status_code == 201, linked.text
    path = f"{BASE}/{first}/guardians/{shared}"
    assert (
        api.call(world.person("teacher"), "DELETE", path, headers=_if_match(1)).status_code == 403
    )
    stale = api.call(admin, "DELETE", path, headers=_if_match(999))
    assert stale.status_code == 412
    version = _guardian_version(admin_engine, shared)
    assert version is not None
    res = api.call(admin, "DELETE", path, headers=_if_match(version))
    assert res.status_code == 204, res.text
    # Still the sibling's guardian; no longer the first student's.
    assert _guardian_version(admin_engine, shared) is not None
    first_ids = {g["id"] for g in api.call(admin, "GET", f"{BASE}/{first}/guardians").json()}
    sibling_ids = {g["id"] for g in api.call(admin, "GET", f"{BASE}/{sibling}/guardians").json()}
    assert str(shared) not in first_ids
    assert str(shared) in sibling_ids
    assert api.call(admin, "DELETE", path, headers=_if_match(version)).status_code == 404
    own_version = _guardian_version(admin_engine, own)
    assert own_version is not None
    gone = api.call(
        admin, "DELETE", f"{BASE}/{first}/guardians/{own}", headers=_if_match(own_version)
    )
    assert gone.status_code == 204
    assert _guardian_version(admin_engine, own) is None, "a guardian nobody is linked to is deleted"
    actions = [
        (e["action"], e["resource_id"])
        for e in W.audit_events(admin_engine, world.a.tenant_id)
        if e["action"] in ("guardian.unlinked", "guardian.deleted")
    ]
    assert ("guardian.unlinked", shared) in actions
    assert ("guardian.deleted", shared) not in actions
    assert ("guardian.unlinked", own) in actions
    assert ("guardian.deleted", own) in actions


def test_R_10_scoped_editor_cannot_change_an_enrolment_outside_scope(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """R-10 (API1, SEC-015): a 9A editor reaches a student now in 9A, but not that student's
    closed 9C enrolment: correcting its roll number or ending it is checked against the
    enrolment's own section, not only the student's current one."""
    admin = world.person("office_admin")
    sid = _student(world, "section_9c")
    old = _only_enrolment(api, world, sid)
    res = api.call(
        admin,
        "POST",
        f"{BASE}/{sid}/enrollments/{old['id']}/end",
        json={"status": "completed"},
        headers=_if_match(old["version"]),
    )
    assert res.status_code == 200, res.text
    old_version = res.json()["version"]
    res = api.call(
        admin,
        "POST",
        f"{BASE}/{sid}/enrollments",
        json={"section_id": str(world.a.ids["section_9a"])},
    )
    assert res.status_code == 201, res.text
    current = res.json()
    editor = _scoped_editor(world, admin_engine)
    old_path = f"{BASE}/{sid}/enrollments/{old['id']}"
    res = api.call(
        editor, "PATCH", old_path, json={"roll_no": "99"}, headers=_if_match(old_version)
    )
    assert res.status_code == 404, res.text
    # Within scope the editor still corrects the current enrolment.
    res = api.call(
        editor,
        "PATCH",
        f"{BASE}/{sid}/enrollments/{current['id']}",
        json={"roll_no": "12"},
        headers=_if_match(current["version"]),
    )
    assert res.status_code == 200, res.text
