"""List and search a non-current academic year, and who committed/undid a promotion
(FR-STU-010, US-302, FR-TEN-011, US-202 AC2; invariants 2, 3 and 5). Synthetic data only.

``academic_year_id`` on ``GET /students`` and ``POST /students/search`` picks the year whose
enrolments are listed; scoped holders still reach only the students of their CURRENT-year
sections or classes (owner decision 2026-10-09; the full case is in test_past_year_reach.py).
Promotion runs carry the display names (never emails) of who committed and who undid them.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.tenancy import service as tenancy
from app.tenancy.schemas import ClassCreate

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
W = SW.W
GET = "/api/v1/students"
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


@pytest.fixture(scope="module")
def school(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    """A fresh synthetic school with classes VIII-X and no current year."""
    s = W.School(W.provision_school())
    for role in ("owner", "office_admin", "office_staff", "principal"):
        s.people[role] = W.add_member(admin_engine, s.tenant_id, [role])
    with tenant_session(s.tenant_id, s.people["owner"].user_id) as db:
        for code, order in (("VIII", 110), ("IX", 120), ("X", 130)):
            out = tenancy.create_class(
                db,
                ClassCreate(
                    code=code, display_en=f"Class {code}", display_te=code, sort_order=order
                ),
            )
            s.ids[f"class_{code}"] = out.id
    return s


def _ids(res: Any) -> set[str]:
    assert res.status_code == 200, res.text
    return {item["id"] for item in res.json()["data"]}


def _year_world(school: Any) -> tuple[Any, dict[str, uuid.UUID]]:
    pair = P.year_pair(school)
    ids = {
        "ix": P.enrolled(school, pair.section("from", "IX"), name="Synthetica Pastyear Nine"),
        "x": P.enrolled(school, pair.section("from", "X"), name="Synthetica Pastyear Ten"),
    }
    return pair, ids


def test_FR_STU_010_list_and_search_a_non_current_year(school: Any, api: Any) -> None:
    pair, ids = _year_world(school)
    owner = school.people["office_admin"]
    year = str(pair.from_year)

    def labels(res: Any) -> dict[str, str | None]:
        assert res.status_code == 200, res.text
        return {i["id"]: i["class_section"] for i in res.json()["data"]}

    # No current year: the students are listed without a class, as today.
    today = labels(api.call(owner, "GET", GET, params={"limit": 200}))
    assert {today[str(i)] for i in ids.values()} == {None}
    # Whole-school readers see the chosen year's class and section.
    for res in (
        api.call(owner, "GET", GET, params={"academic_year_id": year, "limit": 200}),
        api.call(owner, "POST", SEARCH, json={"academic_year_id": year, "limit": 200}),
    ):
        got = labels(res)
        assert got[str(ids["ix"])] == "IX-A"
        assert got[str(ids["x"])] == "X-A"
    found = api.call(
        owner, "POST", SEARCH, json={"academic_year_id": year, "query": "Pastyear Nine"}
    )
    assert labels(found)[str(ids["ix"])] == "IX-A"
    section = api.call(
        owner,
        "POST",
        SEARCH,
        json={"academic_year_id": year, "section_id": str(pair.section("from", "X"))},
    )
    assert _ids(section) == {str(ids["x"])}
    by_class = api.call(
        owner,
        "GET",
        GET,
        params={"academic_year_id": year, "class_id": str(school.ids["class_IX"])},
    )
    assert _ids(by_class) == {str(ids["ix"])}
    # The other year of the pair has nobody enrolled in IX.
    empty = api.call(
        owner,
        "POST",
        SEARCH,
        json={"academic_year_id": str(pair.to_year), "class_id": str(school.ids["class_IX"])},
    )
    assert _ids(empty) == set()


def test_US_302_year_filter_keeps_the_callers_scope(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    """Owner decision 2026-10-09: a grant on an earlier year's section, or on a class, does not
    reach a student through that year's enrolment. This school has no current year, so scoped
    holders reach nobody, whichever year they ask for (before: the past year's sections)."""
    pair, _ = _year_world(school)
    year = str(pair.from_year)
    class_teacher = W.add_member(
        admin_engine, school.tenant_id, ["teacher"], scopes=[("class", school.ids["class_IX"])]
    )
    section_teacher = W.add_member(
        admin_engine,
        school.tenant_id,
        ["class_teacher"],
        scopes=[("section", pair.section("from", "X"))],
    )
    other_section = W.add_member(
        admin_engine,
        school.tenant_id,
        ["class_teacher"],
        scopes=[("section", pair.section("to", "X"))],
    )
    expected: set[str] = set()
    for who in (class_teacher, section_teacher, other_section):
        listed = api.call(who, "GET", GET, params={"academic_year_id": year})
        assert _ids(listed) == expected
        searched = api.call(
            who, "POST", SEARCH, json={"academic_year_id": year, "query": "synthetica"}
        )
        assert _ids(searched) == expected


def test_SEC_001_year_filter_rejects_unknown_and_other_school_years(
    school: Any, world: Any, api: Any
) -> None:
    other_year = world.b.ids["year"]
    for year in (uuid.uuid4(), other_year):
        for res in (
            api.call(school.people["owner"], "GET", GET, params={"academic_year_id": str(year)}),
            api.call(school.people["owner"], "POST", SEARCH, json={"academic_year_id": str(year)}),
        ):
            assert res.status_code == 422, res.text
            assert res.json()["errors"][0]["field"] == "academic_year_id"
    bad = api.call(school.people["owner"], "POST", SEARCH, json={"academic_year_id": "x"})
    assert bad.status_code == 422


def test_FR_TEN_011_promotion_runs_name_who_committed_and_undid(school: Any, api: Any) -> None:
    pair = P.year_pair(school)
    P.enrolled(school, pair.section("from", "IX"))
    admin = school.people["office_admin"]
    base = f"/api/v1/academic-years/{pair.from_year}/promotions"
    res = api.call(admin, "POST", f"{base}:commit", json=P.body(pair))
    assert res.status_code == 201, res.text
    run = res.json()
    assert run["committed_by_name"] == admin.display_name
    assert run["undone_by_name"] is None
    undone = api.call(school.people["principal"], "POST", f"{base}:undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["undone_by_name"] == school.people["principal"].display_name
    listed = api.call(school.people["owner"], "GET", base)
    assert listed.status_code == 200
    (entry,) = [r for r in listed.json() if r["id"] == run["id"]]
    assert entry["committed_by_name"] == admin.display_name
    assert entry["undone_by_name"] == school.people["principal"].display_name
    assert "@" not in listed.text
