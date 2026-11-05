"""The breach affected-people picker's count (D59; ADR-0050; FR-DPDP-033): students picked by
class, section or one by one, their guardians and staff, counted once each; nothing stored,
no names in the answer or the logs. Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db
P = sys.modules["sos_test_consent_support"]
W = sys.modules["sos_test_api_world"]
BASE = "/api/v1/privacy/breaches"


def _path(breach_id: str) -> str:
    return f"{BASE}/{breach_id}/affected:count"


def _section_students(admin: Engine, world: Any, key: str) -> set[uuid.UUID]:
    with admin.connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT student_id FROM sis.enrollments WHERE tenant_id = :t "
                    "AND section_id = :s AND academic_year_id = :y AND status = 'active'"
                ),
                {"t": world.a.tenant_id, "s": world.a.ids[key], "y": world.a.ids["year"]},
            ).scalars()
        )


def _guardians(admin: Engine, student_ids: set[uuid.UUID]) -> int:
    with admin.connect() as c:
        value: int = c.execute(
            text(
                "SELECT count(DISTINCT guardian_id) FROM sis.student_guardians "
                "WHERE student_id = ANY(:ids)"
            ),
            {"ids": list(student_ids)},
        ).scalar_one()
    return value


def test_FR_DPDP_033_D59_count_needs_manage_and_other_school_is_404(world: Any, api: Any) -> None:
    out = P.create_breach(api, world.person("owner"))
    body = {"all_staff": True}
    for role in ("office_admin", "auditor_readonly", "class_teacher", "accountant"):
        res = api.call(world.person(role), "POST", _path(out["id"]), json=body)
        assert res.status_code == 403, (role, res.text)
    # A read for the form: no step-up (the PATCH that saves the counts asks for it).
    ok = api.call(world.person("principal"), "POST", _path(out["id"]), json=body, auth_age_s=301)
    assert ok.status_code == 200, ok.text
    b_owner = world.b.people["owner"]
    assert api.call(b_owner, "POST", _path(out["id"]), json=body).status_code == 404
    random = api.call(world.person("owner"), "POST", _path(str(uuid.uuid4())), json=body)
    assert random.status_code == 404


def test_FR_DPDP_033_D59_students_counted_once_with_their_guardians(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    ids = P.SW.ensure_students(world)
    owner = world.person("owner")
    out = P.create_breach(api, owner)
    in_9a = _section_students(admin_engine, world, "section_9a")
    assert ids["s9a"] in in_9a
    section = api.call(
        owner, "POST", _path(out["id"]), json={"section_ids": [str(world.a.ids["section_9a"])]}
    )
    assert section.status_code == 200, section.text
    assert section.json() == {
        "students": len(in_9a),
        "guardians": _guardians(admin_engine, in_9a),
        "staff": 0,
    }
    # The same student by section, by class and one by one (twice) still counts once.
    twice = api.call(
        owner,
        "POST",
        _path(out["id"]),
        json={
            "section_ids": [str(world.a.ids["section_9a"])],
            "class_ids": [str(world.a.ids["class_ix"])],
            "student_ids": [str(ids["s9a"]), str(ids["s9a"])],
        },
    )
    assert twice.status_code == 200, twice.text
    in_ix = in_9a | _section_students(admin_engine, world, "section_9c")
    assert twice.json()["students"] == len(in_ix)
    assert twice.json()["guardians"] == _guardians(admin_engine, in_ix)
    single = api.call(owner, "POST", _path(out["id"]), json={"student_ids": [str(ids["s9a"])]})
    assert single.json()["students"] == 1
    assert single.json()["guardians"] == _guardians(admin_engine, {ids["s9a"]}) >= 1
    everyone = api.call(owner, "POST", _path(out["id"]), json={"all_students": True}).json()
    assert everyone["students"] >= len(in_ix)


def test_FR_DPDP_033_D59_staff_by_role_and_one_by_one(world: Any, api: Any) -> None:
    owner = world.person("owner")
    out = P.create_breach(api, owner)
    target = world.person("target").membership_id
    teachers = api.call(owner, "POST", _path(out["id"]), json={"staff_role_keys": ["teacher"]})
    assert teachers.status_code == 200, teachers.text
    by_role = teachers.json()["staff"]
    assert by_role >= 2  # the class-scoped teacher and the target member
    both = api.call(
        owner,
        "POST",
        _path(out["id"]),
        json={"staff_role_keys": ["teacher"], "membership_ids": [str(target)]},
    )
    assert both.json()["staff"] == by_role
    one = api.call(owner, "POST", _path(out["id"]), json={"membership_ids": [str(target)]})
    assert one.json() == {"students": 0, "guardians": 0, "staff": 1}
    everyone = api.call(owner, "POST", _path(out["id"]), json={"all_staff": True}).json()
    assert everyone["staff"] >= by_role + 1


def test_FR_DPDP_033_D59_other_school_ids_and_unknown_keys_are_refused(
    world: Any, api: Any
) -> None:
    ids = P.SW.ensure_students(world)
    owner = world.person("owner")
    out = P.create_breach(api, owner)
    cases = {
        "student_ids": [str(ids["b_sb"])],
        "section_ids": [str(world.b.ids["section_9a"])],
        "class_ids": [str(world.b.ids["class_ix"])],
        "membership_ids": [str(world.b.people["owner"].membership_id)],
        "staff_role_keys": ["no_such_role"],
    }
    for field, value in cases.items():
        res = api.call(owner, "POST", _path(out["id"]), json={field: value})
        assert res.status_code == 422, (field, res.text)
        assert field in res.text
    too_many = {"student_ids": [str(uuid.uuid4()) for _ in range(5001)]}
    assert api.call(owner, "POST", _path(out["id"]), json=too_many).status_code == 422


def test_FR_DPDP_033_D59_count_stores_nothing_and_closed_breach_is_409(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    ids = P.SW.ensure_students(world)
    owner = world.person("owner")
    out = P.create_breach(api, owner)
    before = len(W.audit_events(admin_engine, world.a.tenant_id))
    res = api.call(owner, "POST", _path(out["id"]), json={"student_ids": [str(ids["s9a"])]})
    assert res.status_code == 200
    assert len(W.audit_events(admin_engine, world.a.tenant_id)) == before
    assert api.call(owner, "GET", f"{BASE}/{out['id']}/affected").json()["student_ids"] == []
    detail = api.call(owner, "GET", f"{BASE}/{out['id']}").json()
    assert detail["version"] == out["version"]
    assert detail["approx_children"] == out["approx_children"]
    closed = api.call(
        owner,
        "PATCH",
        f"{BASE}/{out['id']}",
        json={"status": "closed", "close_reason": "Recorded by mistake in a drill."},
        headers={"If-Match": f'W/"{out["version"]}"'},
    )
    assert closed.status_code == 200, closed.text
    after = api.call(owner, "POST", _path(out["id"]), json={"all_staff": True})
    assert after.status_code == 409


def test_SEC_008_D59_count_answers_and_logs_carry_no_names(
    world: Any, api: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = P.SW.ensure_students(world)
    owner = world.person("owner")
    out = P.create_breach(api, owner)
    capsys.readouterr()
    res = api.call(
        owner,
        "POST",
        _path(out["id"]),
        json={
            "section_ids": [str(world.a.ids["section_9a"])],
            "student_ids": [str(ids["s9c"])],
            "all_staff": True,
        },
    )
    assert res.status_code == 200
    assert set(res.json()) == {"students", "guardians", "staff"}
    seen = res.text + "".join(capsys.readouterr())
    for name in ("Synthetica", "Venkata", "Lakshmi", "Ramana"):
        assert name not in seen
