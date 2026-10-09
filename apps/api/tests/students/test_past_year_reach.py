"""Past-year students are out of a scoped holder's reach (owner decision 2026-10-09; SEC-015,
US-302, invariant 3). Synthetic data only.

Section- and class-scoped grants (class teacher, teacher, custom scoped roles) reach a student
only through an active enrolment in one of their sections or classes in the CURRENT academic
year. A student whose only matching enrolment is in an earlier year answers 404 and is left out
of lists and search (whatever year is asked for). School-wide grants are unchanged and reach
every year. Another school's student is 404 for everyone.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.students import service as students
from app.students.schemas import EnrollmentIn
from app.tenancy import service as tenancy
from app.tenancy.schemas import ClassCreate

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
W = SW.W
BASE = "/api/v1/students"
SEARCH = "/api/v1/students/search"


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


def _custom_scoped_role(admin: Engine, tenant_id: uuid.UUID, *permissions: str) -> str:
    """A custom role (custom roles are always scoped) with ``permissions``."""
    key = f"scoped_custom_{uuid.uuid4().hex[:8]}"
    role_id = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te) "
                "VALUES (:r, :t, :k, 'Synthetic scoped role', 'కృత్రిమ పాత్ర')"
            ),
            {"r": role_id, "t": tenant_id, "k": key},
        )
        for perm in permissions:
            c.execute(
                text(
                    "INSERT INTO core.role_permissions (tenant_id, role_id, permission_key) "
                    "VALUES (:t, :r, :p)"
                ),
                {"t": tenant_id, "r": role_id, "p": perm},
            )
    return key


@pytest.fixture(scope="module")
def reach(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> dict[str, Any]:
    """A fresh school: last year (``from``) and this year (``to``, current), classes IX and X.

    - ``past_only``: in IX-A last year, not enrolled this year (left, or not yet re-enrolled);
    - ``promoted``: in IX-A last year and X-A this year.

    People: ``past_section`` (class teacher of last year's IX-A only), ``past_class`` (teacher
    scoped to class IX, which has nobody this year), ``current`` (class teacher of this year's
    X-A), ``past_editor`` (custom scoped role with ``student.update_nonidentity`` on last
    year's IX-A), the school-wide ``office_admin`` and ``principal``.
    """
    SW.configure_keyring()
    s = W.School(W.provision_school())
    for role in ("owner", "office_admin", "principal"):
        s.people[role] = W.add_member(admin_engine, s.tenant_id, [role])
    with tenant_session(s.tenant_id, s.people["owner"].user_id) as db:
        for code, order in (("IX", 120), ("X", 130)):
            out = tenancy.create_class(
                db,
                ClassCreate(
                    code=code, display_en=f"Class {code}", display_te=code, sort_order=order
                ),
            )
            s.ids[f"class_{code}"] = out.id
    pair = P.year_pair(s)
    past_only = P.enrolled(s, pair.section("from", "IX"), name="Synthetica Pastonly Student")
    promoted = P.enrolled(s, pair.section("from", "IX"), name="Synthetica Promoted Student")
    with tenant_session(s.tenant_id, s.people["owner"].user_id) as db:
        students.enrol(
            db, SW.admin_ctx(s), promoted, EnrollmentIn(section_id=pair.section("to", "X"))
        )
        year = tenancy.get_academic_year(db, pair.to_year)
        tenancy.set_current_academic_year(db, pair.to_year, expected_version=year.version)
    past_ix = pair.section("from", "IX")
    editor_role = _custom_scoped_role(
        admin_engine, s.tenant_id, "student.read_basic", "student.update_nonidentity"
    )
    people = {
        "past_section": W.add_member(
            admin_engine, s.tenant_id, ["class_teacher"], scopes=[("section", past_ix)]
        ),
        "past_class": W.add_member(
            admin_engine, s.tenant_id, ["teacher"], scopes=[("class", s.ids["class_IX"])]
        ),
        "current": W.add_member(
            admin_engine,
            s.tenant_id,
            ["class_teacher"],
            scopes=[("section", pair.section("to", "X"))],
        ),
        "past_editor": W.add_member(
            admin_engine, s.tenant_id, [editor_role], scopes=[("section", past_ix)]
        ),
    }
    return {"school": s, "pair": pair, "past_only": past_only, "promoted": promoted, **people}


def _ids(res: Any) -> set[str]:
    assert res.status_code == 200, res.text
    return {item["id"] for item in res.json()["data"]}


def _listed(api: Any, who: Any, year: uuid.UUID | None) -> set[str]:
    """The students ``who`` gets from the list and from search (both must agree)."""
    params: dict[str, Any] = {"limit": 200}
    body: dict[str, Any] = {"limit": 200, "query": "synthetica"}
    if year is not None:
        params["academic_year_id"] = str(year)
        body["academic_year_id"] = str(year)
    listed = _ids(api.call(who, "GET", BASE, params=params))
    searched = _ids(api.call(who, "POST", SEARCH, json=body))
    assert listed == searched
    return listed


def test_SEC_015_BOLA_a_scoped_grant_does_not_reach_a_past_year_student(
    reach: dict[str, Any], api: Any, world: Any
) -> None:
    """Allowed (school-wide) roles read the past-year student; scoped roles whose only match is
    last year's section or class get 404; another school gets 404."""
    sid = reach["past_only"]
    for role in ("office_admin", "principal"):
        res = api.call(reach["school"].people[role], "GET", f"{BASE}/{sid}")
        assert res.status_code == 200, (role, res.text)
    for who in ("past_section", "past_class", "current", "past_editor"):
        for path in (f"{BASE}/{sid}", f"{BASE}/{sid}/enrollments"):
            res = api.call(reach[who], "GET", path)
            assert res.status_code == 404, (who, path, res.text)
    for path in (f"{BASE}/{sid}", f"{BASE}/{sid}/enrollments"):
        assert api.call(world.b.people["owner"], "GET", path).status_code == 404


def test_SEC_015_BOLA_past_year_lists_and_search_leave_out_students_outside_current_reach(
    reach: dict[str, Any], api: Any
) -> None:
    """Whatever year is asked for, a scoped holder sees only students they reach through this
    year's enrolments (the chosen year only gives the class and section shown); school-wide
    holders see every student of that year."""
    past, current = reach["pair"].from_year, reach["pair"].to_year
    both = {str(reach["past_only"]), str(reach["promoted"])}
    admin = reach["school"].people["office_admin"]
    assert _listed(api, admin, past) == both
    for who in ("past_section", "past_class"):
        for year in (past, current, None):
            assert _listed(api, reach[who], year) == set(), (who, year)
    teacher = reach["current"]
    assert _listed(api, teacher, current) == {str(reach["promoted"])}
    assert _listed(api, teacher, past) == {str(reach["promoted"])}
    shown = api.call(teacher, "POST", SEARCH, json={"academic_year_id": str(past), "limit": 200})
    assert {i["class_section"] for i in shown.json()["data"]} == {"IX-A"}
    res = api.call(teacher, "GET", f"{BASE}/{reach['promoted']}")
    assert res.status_code == 200, res.text


def test_SEC_015_BOLA_past_year_enrolment_edits_need_current_reach(
    reach: dict[str, Any], api: Any
) -> None:
    """A scoped editor whose grant names last year's section cannot correct or close that
    year's enrolment of a student who is no longer in their reach (404); a school-wide editor
    can."""
    sid = reach["past_only"]
    admin = reach["school"].people["office_admin"]
    (enrolment,) = api.call(admin, "GET", f"{BASE}/{sid}/enrollments").json()
    path = f"{BASE}/{sid}/enrollments/{enrolment['id']}"
    headers = {"If-Match": f'W/"{enrolment["version"]}"'}
    denied = api.call(reach["past_editor"], "PATCH", path, json={"roll_no": "7"}, headers=headers)
    assert denied.status_code == 404, denied.text
    allowed = api.call(admin, "PATCH", path, json={"roll_no": "7"}, headers=headers)
    assert allowed.status_code == 200, allowed.text
