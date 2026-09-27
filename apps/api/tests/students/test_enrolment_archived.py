"""Enrolment writes never target archived structure (FR-TEN-010, FR-TEN-011, US-202; 0023).

Enrol, the section of a new student, PATCH enrolment (section move), promotion commit and
promotion undo answer 409 ``structure_archived`` when the section, its class or its academic
year is archived. An enrolment and an archive running at the same time are serialised by row
locks (the enrolment holds the section, class and year FOR SHARE; the archive's UPDATE and its
guard trigger run after it): exactly one of them wins, never both. A school of its own; every
test builds fresh years (promotion_support.py). Synthetic data only.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.errors import Conflict
from app.students import service as students
from app.students.schemas import EnrollmentIn, PromotionCommitIn, StudentCreate, ValueIn
from app.tenancy import service as tenancy
from app.tenancy.schemas import ClassCreate, SectionCreate

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
W = SW.W
BASE = "/api/v1/students"
_codes = itertools.count(1)


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
    """A fresh synthetic school: owner and office admin; classes IX and X."""
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
    return s


def _db(school: Any) -> Any:
    return tenant_session(school.tenant_id, school.people["owner"].user_id)


def _add_section(school: Any, year_id: uuid.UUID, class_id: uuid.UUID, name: str) -> uuid.UUID:
    with _db(school) as db:
        out = tenancy.create_section(
            db, SectionCreate(academic_year_id=year_id, class_id=class_id, name=name)
        )
    return out.id


def _class_of(school: Any, section_id: uuid.UUID) -> uuid.UUID:
    with _db(school) as db:
        class_id: uuid.UUID = tenancy.get_section(db, section_id).class_id
    return class_id


def _archive(school: Any, kind: str, row_id: uuid.UUID) -> None:
    with _db(school) as db:
        if kind == "section":
            version = tenancy.get_section(db, row_id).version
            tenancy.archive_section(db, row_id, archived=True, expected_version=version)
        elif kind == "class":
            version = tenancy.get_class(db, row_id).version
            tenancy.archive_class(db, row_id, archived=True, expected_version=version)
        else:
            version = tenancy.get_academic_year(db, row_id).version
            tenancy.archive_academic_year(db, row_id, archived=True, expected_version=version)


def _enrolments(admin: Engine, student_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT section_id, status FROM sis.enrollments WHERE student_id = :s "
                "ORDER BY created_at, id"
            ),
            {"s": student_id},
        ).all()
    return [(r[0], r[1]) for r in rows]


def _enrol(school: Any, student_id: uuid.UUID, section_id: uuid.UUID) -> Any:
    SW.configure_keyring()
    with _db(school) as db:
        return students.enrol(
            db, SW.admin_ctx(school), student_id, EnrollmentIn(section_id=section_id)
        )


def test_FR_TEN_010_enrol_into_archived_section_is_409(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    pair = P.year_pair(school)
    home = pair.section("from", "IX")
    sid = P.enrolled(school, home, name="Synthetica Archived Section")
    spare = _add_section(school, pair.from_year, _class_of(school, home), "B")
    _archive(school, "section", spare)
    res = api.call(
        school.people["office_admin"],
        "POST",
        f"{BASE}/{sid}/enrollments",
        json={"section_id": str(spare)},
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "structure_archived"
    assert _enrolments(admin_engine, sid) == [(home, "active")], "nothing was written"


def test_FR_TEN_010_enrol_into_section_of_archived_class_is_409(
    school: Any, admin_engine: Engine
) -> None:
    pair = P.year_pair(school)
    home = pair.section("from", "IX")
    sid = P.enrolled(school, home, name="Synthetica Archived Class")
    n = next(_codes)
    with _db(school) as db:
        klass = tenancy.create_class(
            db,
            ClassCreate(code=f"Z{n}", display_en=f"Class Z{n}", display_te="Z", sort_order=900 + n),
        )
    target = _add_section(school, pair.from_year, klass.id, "A")
    _archive(school, "class", klass.id)
    with pytest.raises(Conflict) as exc:
        _enrol(school, sid, target)
    assert exc.value.code == "structure_archived"
    assert _enrolments(admin_engine, sid) == [(home, "active")]


def test_FR_TEN_010_enrol_into_section_of_archived_year_is_409(
    school: Any, admin_engine: Engine
) -> None:
    pair = P.year_pair(school)
    home = pair.section("from", "IX")
    sid = P.enrolled(school, home, name="Synthetica Archived Year")
    _archive(school, "academic_year", pair.to_year)
    with pytest.raises(Conflict) as exc:
        _enrol(school, sid, pair.section("to", "IX"))
    assert exc.value.code == "structure_archived"
    assert _enrolments(admin_engine, sid) == [(home, "active")]


def test_FR_TEN_010_new_student_in_archived_section_is_409(school: Any, api: Any) -> None:
    pair = P.year_pair(school)
    target = pair.section("from", "X")
    _archive(school, "section", target)
    res = api.call(
        school.people["office_admin"],
        "POST",
        BASE,
        json={
            "values": [
                {
                    "attribute_key": "full_name",
                    "source": "admission_register",
                    "value": "Synthetica Never Created",
                }
            ],
            "section_id": str(target),
        },
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "structure_archived"
    SW.configure_keyring()
    values = [ValueIn(attribute_key="full_name", source="admission_register", value="Synth")]
    with pytest.raises(Conflict), _db(school) as db:
        students.create_student(
            db, SW.admin_ctx(school), StudentCreate(values=values, section_id=target)
        )


def test_FR_TEN_010_patch_enrolment_into_archived_section_is_409(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    pair = P.year_pair(school)
    home = pair.section("from", "IX")
    sid = P.enrolled(school, home, name="Synthetica Archived Move")
    sibling = _add_section(school, pair.from_year, _class_of(school, home), "C")
    _archive(school, "section", sibling)
    admin = school.people["office_admin"]
    (enrolment,) = api.call(admin, "GET", f"{BASE}/{sid}/enrollments").json()
    res = api.call(
        admin,
        "PATCH",
        f"{BASE}/{sid}/enrollments/{enrolment['id']}",
        json={"section_id": str(sibling)},
        headers={"If-Match": f'W/"{enrolment["version"]}"'},
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "structure_archived"
    assert _enrolments(admin_engine, sid) == [(home, "active")]


def test_FR_TEN_011_promotion_into_archived_year_is_409(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    pair = P.year_pair(school)
    home = pair.section("from", "IX")
    sid = P.enrolled(school, home, name="Synthetica Archived Target Year")
    _archive(school, "academic_year", pair.to_year)
    res = api.call(
        school.people["office_admin"],
        "POST",
        f"/api/v1/academic-years/{pair.from_year}/promotions:commit",
        json=P.body(pair),
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "structure_archived"
    assert _enrolments(admin_engine, sid) == [(home, "active")]


def test_FR_TEN_011_undo_into_archived_year_is_409(school: Any, admin_engine: Engine) -> None:
    """After a commit the source year has no active enrolments and can be archived; undoing
    would reopen enrolments there, so it is refused until the year is unarchived."""
    pair = P.year_pair(school)
    home = pair.section("from", "IX")
    sid = P.enrolled(school, home, name="Synthetica Archived Source Year")
    with _db(school) as db:
        students.commit_promotion(
            db,
            SW.admin_ctx(school),
            pair.from_year,
            PromotionCommitIn(to_academic_year_id=pair.to_year),
        )
    before = _enrolments(admin_engine, sid)
    _archive(school, "academic_year", pair.from_year)
    with pytest.raises(Conflict) as exc, _db(school) as db:
        students.undo_promotion(db, SW.admin_ctx(school), pair.from_year)
    assert exc.value.code == "structure_archived"
    assert _enrolments(admin_engine, sid) == before


# --- concurrency: one of enrolment and archive always wins ------------------------------------


def _wait_until_blocked(
    admin: Engine,
    query_marker: str,
    thread: threading.Thread,
    errors: list[BaseException],
    timeout_s: float = 10.0,
) -> None:
    """Wait until the second transaction waits on a row lock held by the first."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        assert thread.is_alive(), f"the second transaction did not wait: {errors!r}"
        with admin.connect() as c:
            waiting: int = c.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND datname = current_database() "
                    "AND query ILIKE :marker"
                ),
                {"marker": f"%{query_marker}%"},
            ).scalar_one()
        if waiting:
            return
        time.sleep(0.05)
    raise AssertionError("the second transaction never waited for the first")


def _in_thread(fn: Callable[[], None]) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            fn()
        except BaseException as exc:  # reported to the test thread
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, errors


def test_FR_TEN_010_enrolment_first_then_archive_waits_and_fails(
    school: Any, admin_engine: Engine
) -> None:
    pair = P.year_pair(school)
    home = pair.section("from", "IX")
    sid = P.enrolled(school, home, name="Synthetica Race Enrol First")
    spare = _add_section(school, pair.from_year, _class_of(school, home), "D")
    with _db(school) as db:
        version = tenancy.get_section(db, spare).version
    SW.configure_keyring()
    with _db(school) as db:
        students.enrol(db, SW.admin_ctx(school), sid, EnrollmentIn(section_id=spare))

        def archive() -> None:
            with _db(school) as other:
                tenancy.archive_section(other, spare, archived=True, expected_version=version)

        thread, errors = _in_thread(archive)
        _wait_until_blocked(admin_engine, "UPDATE core.sections", thread, errors)
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert len(errors) == 1, errors
    assert isinstance(errors[0], Conflict)
    assert errors[0].code == "structure_in_use"
    with _db(school) as db:
        assert tenancy.get_section(db, spare).archived_at is None
    assert _enrolments(admin_engine, sid) == [(home, "transferred"), (spare, "active")]


def test_FR_TEN_010_archive_first_then_enrolment_waits_and_fails(
    school: Any, admin_engine: Engine
) -> None:
    pair = P.year_pair(school)
    home = pair.section("from", "IX")
    sid = P.enrolled(school, home, name="Synthetica Race Archive First")
    spare = _add_section(school, pair.from_year, _class_of(school, home), "E")
    SW.configure_keyring()
    with _db(school) as db:
        version = tenancy.get_section(db, spare).version
        tenancy.archive_section(db, spare, archived=True, expected_version=version)

        def enrol() -> None:
            _enrol(school, sid, spare)

        thread, errors = _in_thread(enrol)
        # The share-lock query (its text is truncated in pg_stat_activity).
        _wait_until_blocked(
            admin_engine, "SELECT core.sections.id,%core.classes.id", thread, errors
        )
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert len(errors) == 1, errors
    assert isinstance(errors[0], Conflict)
    assert errors[0].code == "structure_archived"
    assert _enrolments(admin_engine, sid) == [(home, "active")]
