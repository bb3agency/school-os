"""Real enrolment start dates and attendance before them (FT-09, audit YA-06; lead decision D84;
FR-ATT-010..FR-ATT-016; invariant 6: the admission register itself is never changed).
Synthetic school from ``tests/insights/support.py``, built for this module.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine

from app.academics import service as academics
from app.academics.schemas import AttendanceEntryIn, AttendanceWrite, SheetIn
from app.core.db import tenant_session
from app.core.errors import ValidationFailed
from app.students import service as students
from app.students.schemas import EnrollmentIn, StudentCreate, ValueIn

pytestmark = pytest.mark.db

YEAR = SimpleNamespace(starts_on=dt.date(2026, 6, 1), ends_on=dt.date(2027, 3, 31))


def _support() -> Any:
    import sys

    return sys.modules["sos_test_insights_support"]


S = _support()
SW = S.SW


@pytest.fixture(scope="module")
def own(world: Any, admin_engine: Engine) -> Any:
    return S.insights_school(admin_engine)


@pytest.mark.parametrize(
    ("admitted", "onboarding", "expected"),
    [
        (dt.date(2026, 11, 2), False, (dt.date(2026, 11, 2), True)),
        (dt.date(2026, 11, 2), True, (dt.date(2026, 11, 2), True)),
        (dt.date(2026, 6, 1), True, (dt.date(2026, 6, 1), True)),
        (dt.date(2019, 6, 12), True, (dt.date(2026, 6, 1), False)),
        (dt.date(2019, 6, 12), False, (dt.date(2026, 6, 1), False)),
        (None, True, (dt.date(2026, 6, 1), False)),
        (None, False, (None, False)),
    ],
)
def test_FR_ATT_010_initial_start_rule(
    admitted: dt.date | None, onboarding: bool, expected: tuple[dt.date | None, bool]
) -> None:
    assert students.initial_start(YEAR, admitted, onboarding=onboarding) == expected


def _create(
    school: Any, key: str, *, admitted: dt.date | None, onboarding: bool, section: str
) -> uuid.UUID:
    SW.configure_keyring()
    values = SW.identity_values(
        f"Synthetica {key} Rao", "2012-03-14", "Synthetic Father", "Synthetic Mother"
    )
    values.append(
        ValueIn(attribute_key="admission_no", source="admission_register", value=f"SYN-{key}")
    )
    if admitted is not None:
        values.append(
            ValueIn(
                attribute_key="admission_date",
                source="admission_register",
                value=admitted.isoformat(),
            )
        )
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        out = students.create_student(
            db,
            SW.admin_ctx(school),
            StudentCreate(values=values, section_id=school.ids[section]),
            onboarding=onboarding,
        )
    return out.id


def _enrolment(school: Any, student_id: uuid.UUID) -> Any:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        rows = students.list_enrollments(db, SW.admin_ctx(school), student_id)
    return next(e for e in rows if e.status == "active")


def _mark(school: Any, section: str, student_id: uuid.UUID, day: dt.date) -> Any:
    actor = S.principal_ctx(school)
    body = AttendanceWrite(
        entries=[AttendanceEntryIn(student_id=student_id, on_date=day, status="present")]
    )
    with tenant_session(school.tenant_id, actor.user_id) as db:
        return academics.record_attendance(db, actor, school.ids[section], body)


def test_FR_ATT_011_an_admission_date_inside_the_year_is_an_explicit_start(own: Any) -> None:
    days = S.school_days(12)
    admitted = days[5]
    sid = _create(own, "ADM1", admitted=admitted, onboarding=False, section="section_10a")
    enrolment = _enrolment(own, sid)
    assert (enrolment.started_on, enrolment.started_on_explicit) == (admitted, True)
    with pytest.raises(ValidationFailed) as err:
        _mark(own, "section_10a", sid, days[0])
    assert [e["code"] for e in err.value.errors] == ["before_enrolment"]
    assert _mark(own, "section_10a", sid, admitted).written == 1


def test_FR_ATT_014_implicit_starts_are_not_checked(own: Any) -> None:
    early = S.school_days(30)[0]
    by_hand = _create(own, "HAND", admitted=None, onboarding=False, section="section_10a")
    enrolment = _enrolment(own, by_hand)
    assert (enrolment.started_on, enrolment.started_on_explicit) == (
        academics.today_ist(),
        False,
    )
    assert _mark(own, "section_10a", by_hand, early).written == 1

    onboarded = _create(own, "ONB", admitted=None, onboarding=True, section="section_10a")
    enrolment = _enrolment(own, onboarded)
    assert (enrolment.started_on, enrolment.started_on_explicit) == (dt.date(2026, 6, 1), False)

    old = _create(own, "OLD", admitted=dt.date(2019, 6, 12), onboarding=True, section="section_10a")
    enrolment = _enrolment(own, old)
    assert (enrolment.started_on, enrolment.started_on_explicit) == (dt.date(2026, 6, 1), False)
    assert _mark(own, "section_10a", old, early).written == 1


def test_FR_ATT_011_a_move_keeps_its_date_and_is_checked(own: Any) -> None:
    days = S.school_days(10)
    sid = _create(own, "MOVE", admitted=None, onboarding=True, section="section_10a")
    moved_on = days[4]
    with tenant_session(own.tenant_id, own.people["owner"].user_id) as db:
        out = students.enrol(
            db,
            SW.admin_ctx(own),
            sid,
            EnrollmentIn(section_id=own.ids["section_9c"], started_on=moved_on),
        )
    assert (out.started_on, out.started_on_explicit) == (moved_on, True)
    with pytest.raises(ValidationFailed) as err:
        _mark(own, "section_9c", sid, days[0])
    assert [e["code"] for e in err.value.errors] == ["before_enrolment"]
    assert _mark(own, "section_9c", sid, days[-1]).written == 1


def test_FR_ATT_013_sheet_lists_dates_before_the_start_per_row(
    own: Any, admin_engine: Engine
) -> None:
    days = S.school_days(8)
    sid = _create(own, "SHEET", admitted=days[3], onboarding=False, section="section_10a")
    assert sid
    data = (
        "Adm No," + ",".join(d.strftime("%d/%m/%Y") for d in (days[0], days[5])) + "\n"
        "SYN-SHEET,P,P\n"
    ).encode()
    doc = S.sheet_document(admin_engine, own, data, section_key="section_10a", uploader="principal")
    out = academics.preview_attendance_sheet(
        S.principal_ctx(own), own.ids["section_10a"], SheetIn(document_id=doc)
    )
    assert [(i.row, i.column, i.code) for i in out.issues] == [(2, 2, "before_enrolment")]
    assert [(e.student_id, e.on_date) for e in out.entries] == [(sid, days[5])]
