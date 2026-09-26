"""Student search (FR-STU-010, US-302 AC1-AC2) in a dedicated synthetic school.

School S has sections 9A, 9B, 9C and 10A in the current year and a class teacher of 9B.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.students import service as students
from app.students.schemas import SearchFilters, StudentCreate, ValueIn
from app.tenancy import service as tenancy
from app.tenancy.schemas import SectionCreate

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
W = SW.W


@dataclass
class SearchSchool:
    school: Any
    ids: dict[str, uuid.UUID]
    admin: Any
    teacher_9b: Any


def _student(
    school: Any, ctx: Any, name: str, *, section: str, father: str, adm: str | None = None
) -> uuid.UUID:
    values = [
        ValueIn(attribute_key="full_name", source="admission_register", value=name),
        ValueIn(attribute_key="father_name", source="admission_register", value=father),
    ]
    if adm:
        values.append(ValueIn(attribute_key="admission_no", source="admission_register", value=adm))
    with tenant_session(school.tenant_id, ctx.user_id) as db:
        return students.create_student(
            db, ctx, StudentCreate(values=values, section_id=school.ids[section])
        ).id


@pytest.fixture(scope="module")
def s(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> SearchSchool:
    SW.configure_keyring()
    school = W.School(W.provision_school())
    owner = W.add_member(admin_engine, school.tenant_id, ["owner"])
    school.people["owner"] = owner
    W.build_structure(school, owner)
    with tenant_session(school.tenant_id, owner.user_id) as db:
        school.ids["section_9b"] = tenancy.create_section(
            db,
            SectionCreate(
                academic_year_id=school.ids["year"], class_id=school.ids["class_ix"], name="B"
            ),
        ).id
    ct = W.add_member(
        admin_engine,
        school.tenant_id,
        ["class_teacher"],
        scopes=[("section", school.ids["section_9b"])],
    )
    admin = SW.ctx_for(school.tenant_id, owner, "office_admin")
    teacher = SW.ctx_for(
        school.tenant_id, ct, "class_teacher", section_ids=frozenset({school.ids["section_9b"]})
    )
    ids = {
        "vs_9b": _student(
            school, admin, "VENKATA SAI K.", section="section_9b", father="Kommineni Ramana"
        ),
        "vs_9a": _student(
            school, admin, "Kommineni Venkatasai", section="section_9a", father="Kommineni Suresh"
        ),
        "ld_9b": _student(
            school, admin, "Gorantla Lakshmi Devi", section="section_9b", father="Gorantla Prasad"
        ),
        "vr_10a": _student(
            school,
            admin,
            "Venkata Ramana Duvvuri",
            section="section_10a",
            father="Duvvuri Srinivas",
        ),
        "adm_9c": _student(
            school,
            admin,
            "Addepalli Sita",
            section="section_9c",
            father="Addepalli Rao",
            adm="2019/0457",
        ),
        "adm2_9c": _student(
            school,
            admin,
            "Addepalli Gita",
            section="section_9c",
            father="Addepalli Rao",
            adm="2019/04571",
        ),
    }
    return SearchSchool(school, ids, admin, teacher)


def find(
    s: SearchSchool, query: str | None = None, *, ctx: Any = None, limit: int = 50, **filters: Any
) -> Any:
    ctx = ctx or s.admin
    with tenant_session(s.school.tenant_id, ctx.user_id) as db:
        return students.search(db, ctx, SearchFilters(query=query, **filters), limit=limit)


def ids_of(page: Any) -> list[uuid.UUID]:
    return [item.id for item in page.data]


def test_US_302_AC1_name_and_section_tokens(s: SearchSchool) -> None:
    page = find(s, "venkat sai 9b")
    assert ids_of(page)[0] == s.ids["vs_9b"]
    assert s.ids["vs_9a"] not in ids_of(page), "section token filters to IX-B"
    top = page.data[0]
    assert (top.display_name, top.class_section, top.match.field) == (
        "VENKATA SAI K.",
        "IX-B",
        "full_name",
    )
    assert ids_of(find(s, "VENKAT SAI IX-B")) == ids_of(page)


def test_US_302_AC1_partial_and_spacing_variants(s: SearchSchool) -> None:
    got = ids_of(find(s, "venkata sai"))
    assert {s.ids["vs_9b"], s.ids["vs_9a"]} <= set(got)
    assert s.ids["ld_9b"] not in got
    assert ids_of(find(s, "lakshmi"))[0] == s.ids["ld_9b"]


def test_US_302_AC1_telugu_query_finds_latin_record(s: SearchSchool) -> None:
    got = ids_of(find(s, "వెంకట సాయి"))
    assert s.ids["vs_9b"] in got
    assert s.ids["vs_9a"] in got
    assert ids_of(find(s, "లక్ష్మి దేవి"))[0] == s.ids["ld_9b"]


def test_FR_STU_010_parent_name_and_admission_number(s: SearchSchool) -> None:
    page = find(s, "kommineni ramana")
    first = page.data[0]
    assert first.id == s.ids["vs_9b"]
    assert first.match.field == "father_name"
    adm = find(s, "2019/0457")
    assert ids_of(adm)[:2] == [s.ids["adm_9c"], s.ids["adm2_9c"]], "exact match ranks first"
    assert adm.data[0].match.field == "admission_no"
    assert set(ids_of(find(s, "2019/04"))) == {s.ids["adm_9c"], s.ids["adm2_9c"]}
    assert ids_of(find(s, admission_no="2019/0457")) == [s.ids["adm_9c"]]


def test_FR_STU_010_class_and_section_filters(s: SearchSchool) -> None:
    nine_b = set(ids_of(find(s, "9b")))
    assert nine_b == {s.ids["vs_9b"], s.ids["ld_9b"]}
    assert set(ids_of(find(s, section_id=s.school.ids["section_9b"]))) == nine_b
    ix = set(ids_of(find(s, class_id=s.school.ids["class_ix"])))
    assert ix == nine_b | {s.ids["vs_9a"], s.ids["adm_9c"], s.ids["adm2_9c"]}
    assert set(ids_of(find(s, "9"))) == ix
    assert ids_of(find(s, "9d")) == []


def test_US_302_AC2_results_respect_scope(s: SearchSchool) -> None:
    got = set(ids_of(find(s, "venkata", ctx=s.teacher_9b)))
    assert got == {s.ids["vs_9b"]}
    assert set(ids_of(find(s, None, ctx=s.teacher_9b))) == {s.ids["vs_9b"], s.ids["ld_9b"]}
    assert ids_of(find(s, "10a", ctx=s.teacher_9b)) == []


def test_FR_STU_010_cursor_pagination_is_stable(s: SearchSchool) -> None:
    seen: list[uuid.UUID] = []
    cursor = None
    with tenant_session(s.school.tenant_id, s.admin.user_id) as db:
        for _ in range(10):
            page = students.search(db, s.admin, SearchFilters(), limit=2, cursor=cursor)
            seen.extend(i.id for i in page.data)
            cursor = page.next_cursor
            if cursor is None:
                break
    assert sorted(seen) == sorted(s.ids.values())
    assert len(seen) == len(set(seen))
    names = [i.display_name for i in find(s).data]
    assert names == sorted(names, key=lambda n: n.upper())


def test_other_school_students_never_appear(
    s: SearchSchool, world: Any, shared: dict[str, Any]
) -> None:
    got = set(ids_of(find(s, "synthetica")))
    assert not got & set(shared.values())
