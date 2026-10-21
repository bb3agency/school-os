"""Student records in the data principal's access report (R3-1A; FR-DPDP-009, PRV-031;
ADR-0048 decision 6). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.core.db import tenant_session
from app.core.report_format import AADHAAR_HELD
from app.core.subjects import ReportSection, Subject
from app.students import service as students
from app.students.schemas import ValueIn

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
_REGISTRY = Path(students.__file__).resolve().parents[1] / "privacy" / "subjects.yaml"
AAD = "aadhaar_as_printed"
AADHAAR_NAME = "Synthetica Aadhaar Printed Name"
HEALTH = "Synthetic pollen allergy note"
PHONE = "9876512345"
ADDRESS = "Synthetic lane 7, Guntur"
OTHER_PHONE = "9876598765"


def _classes() -> set[str]:
    data = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8"))
    return set(data["contributors"]["students"]["classes"])


def _report(school: Any, subject: Subject) -> list[ReportSection]:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        return students.subject_report(db, subject)


def _pairs(section: ReportSection) -> dict[str, str]:
    return {row[0]: row[1] for row in section.rows}


def _by_title(sections: list[ReportSection]) -> dict[str, ReportSection]:
    return {s.title: s for s in sections}


@pytest.fixture
def family(world: Any) -> dict[str, uuid.UUID]:
    """A student with C3 and Aadhaar as-printed values and a father; a sibling linked to the
    same father; an unrelated student with its own guardian."""
    extra = [
        ValueIn(attribute_key="health_notes", source="parent_form", value=HEALTH),
        ValueIn(attribute_key="aadhaar_last4", source=AAD, value="4821"),
        ValueIn(attribute_key="aadhaar_name_as_printed", source=AAD, value=AADHAAR_NAME),
    ]
    sid = SW.create(
        world.a,
        name=f"Synthetica Report Child {uuid.uuid4().hex[:6]}",
        section_key="section_9a",
        extra=extra,
        admission_no=f"RP/{uuid.uuid4().hex[:6]}",
    )
    sibling = SW.create(
        world.a,
        name=f"Synthetica Report Sibling {uuid.uuid4().hex[:6]}",
        section_key="section_9c",
        admission_no=f"RS/{uuid.uuid4().hex[:6]}",
        extra=[ValueIn(attribute_key="health_notes", source="parent_form", value="Sibling note")],
    )
    father = SW.add_guardian(
        world.a, sid, full_name="Synthetica Report Father", phone=PHONE, address=ADDRESS
    )
    SW.add_guardian(world.a, sibling, guardian_id=father)
    other = SW.create(
        world.a, name="Synthetica Unrelated Child", section_key="section_9a", admission_no=None
    )
    other_guardian = SW.add_guardian(
        world.a, other, full_name="Synthetica Unrelated Parent", phone=OTHER_PHONE
    )
    return {
        "student": sid,
        "sibling": sibling,
        "father": father,
        "other": other,
        "other_guardian": other_guardian,
    }


def test_FR_DPDP_009_students_report_has_identity_and_no_aadhaar(
    world: Any, family: dict[str, uuid.UUID]
) -> None:
    sections = _report(world.a, Subject("student", family["student"]))
    assert {s.class_key for s in sections} <= _classes()
    titles = _by_title(sections)
    assert set(titles) == {
        "Student record",
        "Recorded details and their sources",
        "Enrolments",
        "Parents and guardians on the record",
    }
    record = titles["Student record"]
    assert record.class_key == "student_identity"
    assert _pairs(record)["Status"] == "Active"
    assert _pairs(record)["Admission number"].startswith("RP/")

    values = titles["Recorded details and their sources"]
    by_detail = {r[0]: r for r in values.rows}
    assert by_detail["Health notes"][2] == HEALTH  # C3 of the subject's own record, in clear
    assert by_detail["Date of birth"][2] == "14/03/2012"
    assert by_detail["Date of birth"][1] == "Admission register"
    assert by_detail["Aadhaar (last 4 digits)"][2] == "Last 4 digits held"
    assert by_detail["Name as printed on Aadhaar"][2] == AADHAAR_HELD

    (enrolment,) = titles["Enrolments"].rows
    assert enrolment[3] == "Active"
    assert "A" in enrolment[1]

    guardians = titles["Parents and guardians on the record"]
    assert guardians.class_key == "guardians"
    assert guardians.rows == (("Synthetica Report Father", "Father", "No", "Yes", "Yes"),)

    text = repr(sections)
    for absent in ("4821", AADHAAR_NAME, PHONE, ADDRESS, "Sibling note", "Synthetica Unrelated"):
        assert absent not in text
    assert str(family["sibling"]) not in text


def test_FR_DPDP_009_students_report_for_a_guardian_lists_own_details_and_children(
    world: Any, family: dict[str, uuid.UUID]
) -> None:
    sections = _report(world.a, Subject("guardian", family["father"]))
    assert {s.class_key for s in sections} == {"guardians"}
    titles = _by_title(sections)
    details = _pairs(titles["Parent or guardian details"])
    assert details["Name"] == "Synthetica Report Father"
    assert details["Phone"] == PHONE
    assert details["Address"] == ADDRESS
    children = titles["Linked children"]
    assert children.columns == ("Relationship", "Admission number", "Main contact")
    assert sorted(r[1][:3] for r in children.rows) == ["RP/", "RS/"]
    text = repr(sections)
    for absent in (HEALTH, "Sibling note", "Synthetica Report Child", "Synthetica Report Sibling"):
        assert absent not in text
    assert OTHER_PHONE not in text


def test_FR_DPDP_009_students_report_is_empty_for_unknown_or_other_kinds(
    world: Any, family: dict[str, uuid.UUID]
) -> None:
    assert _report(world.a, Subject("student", uuid.uuid4())) == []
    assert _report(world.a, Subject("guardian", uuid.uuid4())) == []
    assert _report(world.a, Subject("guardian", family["student"])) == []
    assert _report(world.a, Subject("student", family["father"])) == []
    member = world.person("office_admin").membership_id
    assert _report(world.a, Subject("member", member)) == []


def test_FR_DPDP_009_students_report_stays_in_the_school(
    world: Any, family: dict[str, uuid.UUID]
) -> None:
    assert _report(world.b, Subject("student", family["student"])) == []
    assert _report(world.b, Subject("guardian", family["father"])) == []


def test_FR_DPDP_008_students_is_not_an_eraser() -> None:
    assert not hasattr(students, "erase_for_subject")
