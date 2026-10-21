"""Data-principal access report and erasure: the insights contribution (R3-1A; FR-DPDP-009,
FR-DPDP-012..014, ADR-0048 decisions 6 and 8, PRV-031, PRV-032; invariants 7, 14).
Synthetic school from ``tests/insights/support.py``."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.subjects import ErasureOutcome, ReportSection, Subject
from app.insights import service as insights
from app.insights.schemas import ActionIn
from app.students import service as students
from app.students.schemas import EnrollmentIn

pytestmark = pytest.mark.db
S = sys.modules["sos_test_insights_support"]
NOTE_TEXT = "Synthetic note: settled in well after the holidays."
ACTION_NOTE = "Synthetic action note by the teacher."


def _classes() -> set[str]:
    path = Path(insights.__file__).parents[1] / "privacy" / "subjects.yaml"
    raw = yaml.safe_load(path.read_text("utf-8"))
    return set(raw["contributors"]["insights"]["classes"])


def _fresh(school: Any) -> str:
    """A new synthetic student enrolled in 9A, registered under a new key in ``school.ids``."""
    key = f"dp_{uuid.uuid4().hex[:8]}"
    sid = S.SW.create(
        school,
        name=f"Synthetica Insight {key}",
        section_key=None,
        admission_no=f"DP/{key}",
    )
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        students.enrol(
            db, S.SW.admin_ctx(school), sid, EnrollmentIn(section_id=school.ids["section_9a"])
        )
    school.ids[key] = sid
    return key


def _seed(school: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """A student with two notes and a manual flag with one action. Returns (student, flag)."""
    key = _fresh(school)
    S.note(school, key, NOTE_TEXT, category="positive")
    S.note(school, key, "Synthetic note: forgot the lab record.", category="observation")
    flag = S.manual_flag(school, key)
    actor = S.principal_ctx(school)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        insights.add_action(
            db, actor, flag.id, ActionIn(kind="talked_with_student", note=ACTION_NOTE)
        )
    sid: uuid.UUID = school.ids[key]
    return sid, flag.id


def _report(school: Any, subject: Subject) -> list[ReportSection]:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        return insights.subject_report(db, subject)


def _erase(school: Any, subject: Subject, keys: set[str], *, dry_run: bool) -> list[ErasureOutcome]:
    with tenant_session(school.tenant_id, school.people["principal"].user_id) as db:
        return insights.erase_for_subject(db, subject, keys, dry_run=dry_run)


def _count(admin: Engine, sql: str, **params: Any) -> int:
    with admin.connect() as c:
        return int(c.execute(text(sql), params).scalar_one())


def _held(admin: Engine, student: uuid.UUID) -> tuple[int, int, int]:
    """(notes, flags, actions) of the student."""
    return (
        _count(admin, "SELECT count(*) FROM sis.behaviour_notes WHERE student_id = :s", s=student),
        _count(admin, "SELECT count(*) FROM sis.insight_flags WHERE student_id = :s", s=student),
        _count(
            admin,
            "SELECT count(*) FROM sis.flag_actions a JOIN sis.insight_flags f "
            "ON f.tenant_id = a.tenant_id AND f.id = a.flag_id WHERE f.student_id = :s",
            s=student,
        ),
    )


def test_FR_DPDP_009_insights_report_has_notes_and_flags(school: Any) -> None:
    student, _ = _seed(school)
    other, _ = _seed(school)

    sections = _report(school, Subject("student", student))

    assert {s.class_key for s in sections} == _classes()
    by_key = {s.class_key: s for s in sections}
    notes = by_key["behaviour_notes"]
    assert notes.columns == ("Date", "Category", "Note")
    assert len(notes.rows) == 2
    assert {r[1] for r in notes.rows} == {"Positive", "Observation"}
    assert NOTE_TEXT in {r[2] for r in notes.rows}
    flags = by_key["early_warning"]
    assert len(flags.rows) == 1
    row = dict(zip(flags.columns, flags.rows[0], strict=True))
    assert row["Indicator"] == "Behaviour"
    assert row["Status"] == "In progress"
    assert int(row["Actions recorded"]) >= 1
    rendered = repr(sections)
    assert ACTION_NOTE not in rendered  # staff free text never shown
    assert "Synthetic Teacher" not in rendered  # nor who wrote the note
    assert str(other) not in rendered


def test_FR_DPDP_009_insights_report_is_empty_for_unknown_or_other_kinds(school: Any) -> None:
    student, _ = _seed(school)
    assert _report(school, Subject("student", uuid.uuid4())) == []
    assert _report(school, Subject("guardian", student)) == []
    assert _report(school, Subject("member", student)) == []


def test_FR_DPDP_013_insights_dry_run_counts_and_changes_nothing(
    school: Any, admin_engine: Engine
) -> None:
    student, _ = _seed(school)
    before = _held(admin_engine, student)

    out = _erase(
        school, Subject("student", student), {"behaviour_notes", "early_warning"}, dry_run=True
    )

    counts = {o.class_key: o.count for o in out}
    assert counts == {"behaviour_notes": 2, "early_warning": before[1] + before[2]}
    assert _held(admin_engine, student) == before


def test_FR_DPDP_013_insights_erasure_removes_notes_flags_and_actions_and_audits(
    school: Any, admin_engine: Engine
) -> None:
    student, flag_id = _seed(school)
    other, _ = _seed(school)
    before_other = _held(admin_engine, other)
    settings_before = _count(
        admin_engine,
        "SELECT count(*) FROM sis.insight_settings WHERE tenant_id = :t",
        t=school.tenant_id,
    )
    expected = _held(admin_engine, student)

    out = _erase(
        school,
        Subject("student", student),
        {"behaviour_notes", "early_warning", "not_mine"},
        dry_run=False,
    )

    assert {o.class_key: o.count for o in out} == {
        "behaviour_notes": expected[0],
        "early_warning": expected[1] + expected[2],
    }
    assert _held(admin_engine, student) == (0, 0, 0)
    assert _held(admin_engine, other) == before_other
    assert (
        _count(
            admin_engine,
            "SELECT count(*) FROM sis.insight_settings WHERE tenant_id = :t",
            t=school.tenant_id,
        )
        == settings_before
    )
    assert (
        _count(
            admin_engine,
            "SELECT count(*) FROM audit.events WHERE tenant_id = :t "
            "AND action = 'insights.note_erased' AND resource_id = :s "
            "AND summary->>'reason' = 'privacy_request'",
            t=school.tenant_id,
            s=student,
        )
        == 2
    )
    assert (
        _count(
            admin_engine,
            "SELECT count(*) FROM audit.events WHERE tenant_id = :t "
            "AND action = 'insights.flag_erased' AND resource_id = :f",
            t=school.tenant_id,
            f=flag_id,
        )
        == 1
    )
    assert _report(school, Subject("student", student)) == []


def test_FR_DPDP_013_insights_erasure_only_handles_its_keys_and_students(school: Any) -> None:
    student, _ = _seed(school)
    assert _erase(school, Subject("student", student), {"documents"}, dry_run=True) == []
    assert _erase(school, Subject("guardian", student), {"behaviour_notes"}, dry_run=True) == []
    out = _erase(school, Subject("student", student), {"behaviour_notes"}, dry_run=True)
    assert [o.class_key for o in out] == ["behaviour_notes"]
