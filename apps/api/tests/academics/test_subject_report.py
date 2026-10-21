"""Data-principal access report: the attendance and marks contribution (R3-1A; FR-DPDP-009,
ADR-0048 decision 6, PRV-031). Synthetic school from ``tests/insights/support.py``."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.academics import service as academics
from app.core.db import tenant_session
from app.core.subjects import ReportSection, Subject
from app.students import service as students
from app.students.schemas import EnrollmentIn

pytestmark = pytest.mark.db
S = sys.modules["sos_test_insights_support"]


def _classes() -> set[str]:
    path = Path(academics.__file__).parents[1] / "privacy" / "subjects.yaml"
    raw = yaml.safe_load(path.read_text("utf-8"))
    return set(raw["contributors"]["academics"]["classes"])


def _fresh(school: Any, section_key: str = "section_9c") -> uuid.UUID:
    """A new synthetic student enrolled in ``section_key`` (no records yet)."""
    sid: uuid.UUID = S.SW.create(
        school,
        name=f"Synthetica Report {uuid.uuid4().hex[:6]}",
        section_key=None,
        admission_no=f"SR/{uuid.uuid4().hex[:8]}",
    )
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        students.enrol(
            db, S.SW.admin_ctx(school), sid, EnrollmentIn(section_id=school.ids[section_key])
        )
    return sid


def _report(school: Any, subject: Subject) -> list[ReportSection]:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        return academics.subject_report(db, subject)


def test_FR_DPDP_009_academics_report_has_monthly_attendance_and_marks(school: Any) -> None:
    student, other = _fresh(school), _fresh(school)
    days = S.school_days(4)
    S.record(
        school,
        "section_9c",
        {
            student: ["present", "absent", "late", "present"],
            other: ["absent", "absent", "absent", "absent"],
        },
        days,
    )
    exam_id = S.exam(school, f"Synthetic unit test {uuid.uuid4().hex[:6]}", days[-1])
    S.marks(
        school,
        "section_9c",
        exam_id,
        {
            student: [("Mathematics", 45.5, 50), ("Science", None, 50)],
            other: [("Mathematics", 12, 50)],
        },
    )

    sections = _report(school, Subject("student", student))

    assert {s.class_key for s in sections} <= _classes()
    by_title = {s.title: s for s in sections}
    attendance = by_title["Attendance by month"]
    assert attendance.columns == ("Month", "Present", "Late", "Absent", "On leave")
    totals = [sum(int(v) for v in row[1:]) for row in attendance.rows]
    assert sum(totals) == 4
    assert sum(int(row[1]) for row in attendance.rows) == 2
    assert sum(int(row[3]) for row in attendance.rows) == 1
    marks = by_title["Exam marks"]
    rows = {row[2]: row for row in marks.rows}
    assert set(rows) == {"Mathematics", "Science"}
    assert rows["Mathematics"][3:] == ("45.5", "50")
    assert rows["Science"][3] == "Absent"
    assert all(row[1].count("/") == 2 for row in marks.rows)  # DD/MM/YYYY


def test_FR_DPDP_009_academics_report_leaves_out_other_students(school: Any) -> None:
    student, other = _fresh(school), _fresh(school)
    days = S.school_days(2)
    S.record(school, "section_9c", {other: ["absent", "absent"]}, days)
    exam_id = S.exam(school, f"Synthetic other test {uuid.uuid4().hex[:6]}", days[-1])
    S.marks(school, "section_9c", exam_id, {other: [("English", 30, 40)]})

    assert _report(school, Subject("student", student)) == []
    mine = _report(school, Subject("student", other))
    assert sum(len(s.rows) for s in mine) >= 2


def test_FR_DPDP_009_academics_report_is_empty_for_unknown_or_other_kinds(school: Any) -> None:
    student = _fresh(school)
    S.record(school, "section_9c", {student: ["present"]}, S.school_days(1))

    assert _report(school, Subject("student", uuid.uuid4())) == []
    assert _report(school, Subject("guardian", student)) == []
    assert _report(school, Subject("member", student)) == []


def test_FR_DPDP_008_academics_is_not_an_eraser() -> None:
    assert not hasattr(academics, "erase_for_subject")
