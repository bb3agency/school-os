"""Year-end promotions over HTTP (FR-TEN-011, US-202 AC2; owner decisions 2026-09-27).

A school of its own (classes VIII, IX, X; X is the last class) so the class order is fixed.
Every test builds a fresh pair of academic years (tests/students/promotion_support.py).
Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.tenancy import service as tenancy
from app.tenancy.schemas import ClassCreate, SectionCreate

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
W = SW.W


def _load_support() -> ModuleType:
    name = "sos_test_promotion_support"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("promotion_support.py")
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


P = _load_support()


@pytest.fixture(scope="module")
def school(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    """A fresh synthetic school: owner, office admin, office staff, teacher; classes VIII-X."""
    s = W.School(W.provision_school())
    for role in ("owner", "office_admin", "office_staff", "teacher", "principal"):
        s.people[role] = W.add_member(admin_engine, s.tenant_id, [role])
    with tenant_session(s.tenant_id, s.people["owner"].user_id) as db:
        for code, order in (("VIII", 110), ("IX", 120), ("X", 130)):
            tenancy.create_class(
                db,
                ClassCreate(
                    code=code, display_en=f"Class {code}", display_te=code, sort_order=order
                ),
            )
    return s


def _path(pair: Any, action: str) -> str:
    return f"/api/v1/academic-years/{pair.from_year}/promotions:{action}"


def _call(
    api: Any, school: Any, action: str, pair: Any, role: str = "office_admin", **kw: Any
) -> Any:
    return api.call(school.people[role], "POST", _path(pair, action), **kw)


def _enrolments(admin: Engine, student_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, section_id, academic_year_id, status, started_on, ended_on, version "
                "FROM sis.enrollments WHERE student_id = :s ORDER BY created_at, id"
            ),
            {"s": student_id},
        )
        return [dict(r._mapping) for r in rows]


def _status(admin: Engine, student_id: uuid.UUID) -> str:
    with admin.connect() as c:
        return str(
            c.execute(
                text("SELECT status FROM sis.students WHERE id = :s"), {"s": student_id}
            ).scalar_one()
        )


def _year_bounds(admin: Engine, year_id: uuid.UUID) -> tuple[dt.date, dt.date]:
    with admin.connect() as c:
        row = c.execute(
            text("SELECT starts_on, ends_on FROM core.academic_years WHERE id = :y"), {"y": year_id}
        ).one()
    return row.starts_on, row.ends_on


def _set_status(admin: Engine, student_id: uuid.UUID, status: str) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE sis.students SET status = :st WHERE id = :s"),
            {"st": status, "s": student_id},
        )


def _scenario(school: Any) -> tuple[Any, dict[str, uuid.UUID]]:
    """VIII-A: promoted; IX-A: promoted, held back, left; X-A: graduates."""
    pair = P.year_pair(school)
    ids = {
        "viii": P.enrolled(school, pair.section("from", "VIII"), name="Synthetica Eight"),
        "ix": P.enrolled(school, pair.section("from", "IX"), name="Synthetica Nine"),
        "held": P.enrolled(school, pair.section("from", "IX"), name="Synthetica Held"),
        "left": P.enrolled(school, pair.section("from", "IX"), name="Synthetica Left"),
        "x": P.enrolled(school, pair.section("from", "X"), name="Synthetica Ten"),
    }
    return pair, ids


def _target_count(admin: Engine, pair: Any) -> int:
    with admin.connect() as c:
        return int(
            c.execute(
                text("SELECT count(*) FROM sis.enrollments WHERE academic_year_id = :y"),
                {"y": pair.to_year},
            ).scalar_one()
        )


def test_FR_TEN_011_preview_plans_without_writing(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    pair, ids = _scenario(school)
    _set_status(admin_engine, ids["left"], "left")
    before = W.audit_events(admin_engine, school.tenant_id)
    body = P.body(pair, held_back_student_ids=[str(ids["held"])])
    res = _call(api, school, "preview", pair, json=body)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["counts"] == {"promoted": 2, "held_back": 1, "graduated": 1, "skipped": 1}
    assert out["problems"] == []
    assert out["can_commit"] is True
    assert len(out["plan_fingerprint"]) == 64
    by_student = {s["student_id"]: s for s in out["students"]}
    expect = {
        "viii": ("promoted", pair.section("to", "IX"), None),
        "ix": ("promoted", pair.section("to", "X"), None),
        "held": ("held_back", pair.section("to", "IX"), None),
        "left": ("skipped", None, "left"),
        "x": ("graduated", None, None),
    }
    for key, (outcome, to_section, reason) in expect.items():
        item = by_student[str(ids[key])]
        assert item["outcome"] == outcome, key
        assert item["to_section_id"] == (str(to_section) if to_section else None), key
        assert item["reason"] == reason, key
    labels = {(g["from_label"], g["outcome"], g["to_label"]): g["count"] for g in out["groups"]}
    assert labels[("IX-A", "promoted", "X-A")] == 1
    assert labels[("X-A", "graduated", None)] == 1
    # Nothing written: no target-year enrolment, no audit event, statuses unchanged.
    assert _target_count(admin_engine, pair) == 0
    assert W.audit_events(admin_engine, school.tenant_id) == before
    assert _status(admin_engine, ids["x"]) == "active"


def test_FR_TEN_011_commit_moves_enrolments_audits_and_replays(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    pair, ids = _scenario(school)
    _set_status(admin_engine, ids["left"], "left")
    body = P.body(pair, held_back_student_ids=[str(ids["held"])])
    preview = _call(api, school, "preview", pair, json=body).json()
    key = {"Idempotency-Key": f"promote-{uuid.uuid4().hex}"}
    commit_body = {**body, "plan_fingerprint": preview["plan_fingerprint"]}
    res = _call(api, school, "commit", pair, json=commit_body, headers=key)
    assert res.status_code == 201, res.text
    run = res.json()
    assert run["status"] == "committed"
    assert run["can_undo"] is True
    assert run["counts"] == preview["counts"]
    assert res.headers["Location"] == f"/api/v1/academic-years/{pair.from_year}/promotions"

    for name, (target, status) in {
        "viii": ("IX", "active"),
        "ix": ("X", "active"),
        "held": ("IX", "active"),
        "x": (None, "graduated"),
    }.items():
        old, *new = _enrolments(admin_engine, ids[name])
        assert old["status"] == "completed", name
        assert old["ended_on"] == _year_bounds(admin_engine, pair.from_year)[1], name
        if target is None:
            assert new == [], name
        else:
            (opened,) = new
            assert opened["section_id"] == pair.section("to", target), name
            assert opened["status"] == "active"
            assert opened["academic_year_id"] == pair.to_year
            assert opened["started_on"] == _year_bounds(admin_engine, pair.to_year)[0]
        assert _status(admin_engine, ids[name]) == status, name
    # Left: skipped, nothing changed.
    (still,) = _enrolments(admin_engine, ids["left"])
    assert still["status"] == "active"
    assert _status(admin_engine, ids["left"]) == "left"

    (event,) = W.audit_events(admin_engine, school.tenant_id, "promotion.committed")[-1:]
    assert event["resource_id"] == uuid.UUID(run["id"])
    assert event["summary"]["promoted_count"] == 2
    assert event["summary"]["graduated_count"] == 1
    assert event["summary"]["from_academic_year_id"] == str(pair.from_year)
    with admin_engine.connect() as c:
        queued: int = c.execute(
            text(
                "SELECT count(*) FROM ops.outbox WHERE tenant_id = :t "
                "AND event_type = 'student.values.changed' AND payload -> 'student_ids' ? :s "
                "AND payload -> 'attribute_keys' ? 'enrollment'"
            ),
            {"t": school.tenant_id, "s": str(ids["x"])},
        ).scalar_one()
    assert queued >= 1

    again = _call(api, school, "commit", pair, json=commit_body, headers=key)
    assert again.status_code == 201
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert again.json()["id"] == run["id"]
    fresh = _call(api, school, "commit", pair, json=body)
    assert fresh.status_code == 409
    assert fresh.json()["code"] == "promotion_already_committed"

    listed = api.call(
        school.people["principal"], "GET", f"/api/v1/academic-years/{pair.from_year}/promotions"
    )
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()] == [run["id"]]


def test_FR_TEN_011_undo_restores_everything_within_24_hours(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    pair, ids = _scenario(school)
    before = {k: _enrolments(admin_engine, v) for k, v in ids.items()}
    assert _call(api, school, "commit", pair, json=P.body(pair)).status_code == 201
    res = _call(api, school, "undo", pair)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "undone"
    assert res.json()["can_undo"] is False
    for key, sid in ids.items():
        (old,) = _enrolments(admin_engine, sid)
        assert old["id"] == before[key][0]["id"]
        assert old["status"] == "active"
        assert old["ended_on"] is None
        assert _status(admin_engine, sid) == "active"
    assert _target_count(admin_engine, pair) == 0
    (event,) = W.audit_events(admin_engine, school.tenant_id, "promotion.undone")[-1:]
    assert event["summary"]["graduations_reverted"] == 1
    assert event["summary"]["enrollments_reopened"] == 5
    again = _call(api, school, "undo", pair)
    assert again.status_code == 409
    assert again.json()["code"] == "no_promotion"
    # After an undo the year can be promoted again.
    assert _call(api, school, "commit", pair, json=P.body(pair)).status_code == 201


def test_FR_TEN_011_undo_refused_when_an_enrolment_changed(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    pair = P.year_pair(school)
    sid = P.enrolled(school, pair.section("from", "IX"))
    assert _call(api, school, "commit", pair, json=P.body(pair)).status_code == 201
    _old, new = _enrolments(admin_engine, sid)
    patched = api.call(
        school.people["office_admin"],
        "PATCH",
        f"/api/v1/students/{sid}/enrollments/{new['id']}",
        json={"roll_no": "12"},
        headers={"If-Match": f'W/"{new["version"]}"'},
    )
    assert patched.status_code == 200, patched.text
    res = _call(api, school, "undo", pair)
    assert res.status_code == 409
    assert res.json()["code"] == "promotion_has_dependents"
    assert [e["status"] for e in _enrolments(admin_engine, sid)] == ["completed", "active"]


def test_FR_TEN_011_undo_refused_after_24_hours(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    pair = P.committed(school)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE sis.promotion_runs SET committed_at = now() - interval '25 hours' "
                "WHERE from_academic_year_id = :y"
            ),
            {"y": pair.from_year},
        )
    res = _call(api, school, "undo", pair)
    assert res.status_code == 409
    assert res.json()["code"] == "promotion_undo_expired"
    listed = api.call(
        school.people["office_admin"], "GET", f"/api/v1/academic-years/{pair.from_year}/promotions"
    )
    assert listed.json()[0]["can_undo"] is False


def test_FR_TEN_011_missing_target_section_blocks_until_mapped(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    pair = P.year_pair(
        school,
        source={"IX": ("A", "B")},
        target={"IX": ("A",), "X": ("A",)},
    )
    sid = P.enrolled(school, pair.section("from", "IX", "B"))
    preview = _call(api, school, "preview", pair, json=P.body(pair)).json()
    assert preview["can_commit"] is False
    (problem,) = preview["problems"]
    assert problem["code"] == "no_target_section"
    assert problem["from_label"] == "IX-B"
    assert problem["count"] == 1
    refused = _call(api, school, "commit", pair, json=P.body(pair))
    assert refused.status_code == 422
    assert refused.json()["errors"][0]["code"] == "no_target_section"
    assert _target_count(admin_engine, pair) == 0
    mapped = P.body(
        pair,
        section_map=[
            {
                "from_section_id": str(pair.section("from", "IX", "B")),
                "to_section_id": str(pair.section("to", "X")),
            }
        ],
    )
    assert _call(api, school, "commit", pair, json=mapped).status_code == 201
    assert _enrolments(admin_engine, sid)[-1]["section_id"] == pair.section("to", "X")


def test_FR_TEN_011_commit_refuses_a_stale_preview(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    pair = P.year_pair(school)
    P.enrolled(school, pair.section("from", "IX"))
    fingerprint = _call(api, school, "preview", pair, json=P.body(pair)).json()["plan_fingerprint"]
    P.enrolled(school, pair.section("from", "IX"), name="Synthetica Latecomer")
    res = _call(api, school, "commit", pair, json=P.body(pair, plan_fingerprint=fingerprint))
    assert res.status_code == 409
    assert res.json()["code"] == "promotion_plan_changed"
    assert _target_count(admin_engine, pair) == 0


def test_FR_TEN_011_nothing_to_promote(school: Any, api: Any) -> None:
    pair = P.year_pair(school)
    res = _call(api, school, "commit", pair, json=P.body(pair))
    assert res.status_code == 409
    assert res.json()["code"] == "nothing_to_promote"


def test_FR_TEN_011_request_validation(school: Any, world: Any, api: Any) -> None:
    pair = P.year_pair(school)
    stranger = P.enrolled(school, P.year_pair(school).section("from", "IX"))
    cases = [
        ({"to_academic_year_id": str(pair.from_year)}, "to_academic_year_id", "same_year"),
        ({"to_academic_year_id": str(world.b.ids["year"])}, "to_academic_year_id", "not_found"),
        (
            P.body(pair, held_back_student_ids=[str(stranger)]),
            "held_back_student_ids.0",
            "not_in_year",
        ),
        (
            P.body(
                pair,
                section_map=[
                    {
                        "from_section_id": str(pair.section("to", "IX")),
                        "to_section_id": str(pair.section("to", "X")),
                    }
                ],
            ),
            "section_map.0.from_section_id",
            "not_found",
        ),
    ]
    for body, field_name, code in cases:
        res = _call(api, school, "preview", pair, json=body)
        assert res.status_code == 422, (code, res.text)
        assert res.json()["errors"][0] == {
            "field": field_name,
            "code": code,
            "message_key": f"errors.{code}",
        }
    backwards = api.call(
        school.people["office_admin"],
        "POST",
        f"/api/v1/academic-years/{pair.to_year}/promotions:preview",
        json={"to_academic_year_id": str(pair.from_year)},
    )
    assert backwards.status_code == 422
    assert backwards.json()["errors"][0]["code"] == "not_later"


@pytest.mark.parametrize("role", ["office_staff", "teacher"])
def test_US_202_AC3_promotions_need_structure_manage(
    school: Any, api: Any, admin_engine: Engine, role: str
) -> None:
    pair = P.year_pair(school)
    P.enrolled(school, pair.section("from", "IX"))
    for action in ("preview", "commit", "undo"):
        res = _call(api, school, action, pair, role=role, json=P.body(pair))
        assert res.status_code == 403, (action, res.text)
    listed = api.call(
        school.people[role], "GET", f"/api/v1/academic-years/{pair.from_year}/promotions"
    )
    assert listed.status_code == 403
    assert _target_count(admin_engine, pair) == 0


def test_SEC_001_other_schools_years_are_not_found(
    school: Any, world: Any, api: Any, admin_engine: Engine
) -> None:
    pair = P.year_pair(school)
    P.enrolled(school, pair.section("from", "IX"))
    b_owner = world.b.people["owner"]
    for action in ("preview", "commit", "undo"):
        res = api.call(b_owner, "POST", _path(pair, action), json=P.body(pair))
        assert res.status_code == 404, (action, res.text)
    random = api.call(
        school.people["owner"],
        "POST",
        f"/api/v1/academic-years/{uuid.uuid4()}/promotions:preview",
        json=P.body(pair),
    )
    assert random.status_code == 404
    assert _target_count(admin_engine, pair) == 0


def test_FR_TEN_011_archived_classes_and_sections_are_not_targets(
    admin_engine: Engine, app_engine: Engine, platform_engine: Engine, api: Any
) -> None:
    """Archived structure is never a promotion target (US-202, 0023_api_gaps): the last active
    class graduates even when an archived class sorts after it, and an archived target section
    is not used (the student cannot be placed until a section is mapped)."""
    s = W.School(W.provision_school())
    for role in ("owner", "office_admin"):
        s.people[role] = W.add_member(admin_engine, s.tenant_id, [role])
    with tenant_session(s.tenant_id, s.people["owner"].user_id) as db:
        for code, order in (("IX", 120), ("X", 130)):
            tenancy.create_class(
                db,
                ClassCreate(
                    code=code, display_en=f"Class {code}", display_te=code, sort_order=order
                ),
            )
    pair = P.year_pair(s)
    nine = P.enrolled(s, pair.section("from", "IX"), name="Synthetica Nine")
    ten = P.enrolled(s, pair.section("from", "X"), name="Synthetica Ten")
    with tenant_session(s.tenant_id, s.people["owner"].user_id) as db:
        eleven = tenancy.create_class(
            db, ClassCreate(code="XI", display_en="Class XI", display_te="XI", sort_order=140)
        )
        tenancy.create_section(
            db,
            SectionCreate(academic_year_id=pair.to_year, class_id=eleven.id, name="A"),
        )
        tenancy.archive_class(
            db, eleven.id, archived=True, expected_version=tenancy.get_class(db, eleven.id).version
        )
        target = pair.section("to", "X")
        section = next(x for x in tenancy.list_sections(db) if x.id == target)
        tenancy.archive_section(db, target, archived=True, expected_version=section.version)
    res = _call(api, s, "preview", pair, json=P.body(pair))
    assert res.status_code == 200, res.text
    by_student = {x["student_id"]: x for x in res.json()["students"]}
    assert by_student[str(ten)]["outcome"] == "graduated"
    assert by_student[str(ten)]["to_section_id"] is None
    assert by_student[str(nine)]["outcome"] == "promoted"
    assert by_student[str(nine)]["to_section_id"] is None
    assert by_student[str(nine)]["reason"] == "no_target_section"
