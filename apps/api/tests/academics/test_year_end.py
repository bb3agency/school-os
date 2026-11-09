"""Year-end progression pack (FT-08; lead decision D85; FR-YRE-001..FR-YRE-020; invariants 1, 6,
7, 11). Synthetic school from ``tests/insights/support.py``, built for this module.
"""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from app.academics import service as academics
from app.academics import year_end
from app.academics.year_end_schemas import YearResultIn, YearResultsWrite
from app.certificates import service as certificates
from app.core.db import tenant_session
from app.core.errors import Forbidden, NotFound, PreconditionFailed, ValidationFailed
from app.core.redaction import verhoeff_check_digit
from app.students import service as students
from app.students.schemas import EnrollmentIn
from app.tenancy import service as tenancy
from app.tenancy.schemas import ClassCreate, SectionCreate

pytestmark = pytest.mark.db

S = sys.modules["sos_test_insights_support"]


# --- the proposal (pure) --------------------------------------------------------------------------


def _fig(percent: float | None, papers: int = 2, absent: int = 0) -> year_end.Figures:
    return year_end.Figures(
        percent=percent,
        papers=papers,
        absent_papers=absent,
        exams=1,
        days_attended=None,
        working_days=None,
    )


def test_FR_YRE_003_proposal_rule() -> None:
    cut = year_end.rules().pass_percent
    assert year_end.propose(_fig(cut), class_code="IX") == "passed"
    assert year_end.propose(_fig(cut - 0.1), class_code="IX") == "detained"
    assert year_end.propose(_fig(None, papers=2, absent=2), class_code="IX") == "not_appeared"
    assert year_end.propose(_fig(None, papers=0), class_code="IX") is None


def test_FR_YRE_006_no_detention_never_proposes_detained_and_stays_unverified() -> None:
    rules = year_end.rules()
    assert rules.no_detention.verified is False
    assert rules.pass_percent_verified is False
    assert "VIII" in rules.no_detention.class_codes
    assert year_end.propose(_fig(10.0), class_code="VIII") == "promoted"
    assert year_end.no_detention_class("IX") is False


# --- the school -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def own(world: Any, admin_engine: Engine) -> Any:
    s = S.insights_school(admin_engine)
    days = S.school_days(4)
    S.record(
        s,
        "section_9a",
        {
            s.ids["a1"]: ["present", "present", "late", "absent"],
            s.ids["a2"]: ["absent", "absent", "present", "leave"],
        },
        days,
    )
    exam_id = S.exam(s, "Synthetic SA1", days[0])
    S.marks(
        s,
        "section_9a",
        exam_id,
        {s.ids["a1"]: [("Maths", 40, 50)], s.ids["a2"]: [("Maths", 10, 50)]},
    )
    # A class in the no-detention list with one student (FR-YRE-006).
    owner = s.people["owner"]
    with tenant_session(s.tenant_id, owner.user_id) as db:
        viii = tenancy.create_class(
            db,
            ClassCreate(code="VIII", display_en="Class VIII", display_te="8వ తరగతి", sort_order=110),
        )
        sec = tenancy.create_section(
            db, SectionCreate(academic_year_id=s.ids["year"], class_id=viii.id, name="A")
        )
    s.ids["section_8a"] = sec.id
    sid = S.SW.create(s, name="Synthetica E1 Kumar", section_key=None, admission_no="SYN-E1")
    with tenant_session(s.tenant_id, owner.user_id) as db:
        students.enrol(db, S.SW.admin_ctx(s), sid, EnrollmentIn(section_id=sec.id, roll_no="1"))
    s.ids["e1"] = sid
    return s


def _principal(school: Any) -> Any:
    return S.principal_ctx(school)


def _read(school: Any, section_key: str = "section_9a", who: Any | None = None) -> Any:
    actor = who or _principal(school)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        return academics.year_results(db, actor, school.ids[section_key])


def _confirm(school: Any, entries: list[YearResultIn], section_key: str = "section_9a") -> Any:
    actor = _principal(school)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        return academics.confirm_results(
            db, actor, school.ids[section_key], YearResultsWrite(entries=entries)
        )


def _events(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT resource_id, summary FROM audit.events "
                    "WHERE tenant_id = :t AND action = :a ORDER BY seq"
                ),
                {"t": tenant_id, "a": action},
            )
        ]


def _row(admin: Engine, student_id: uuid.UUID) -> dict[str, Any]:
    with admin.connect() as c:
        return dict(
            c.execute(
                text("SELECT * FROM sis.year_results WHERE student_id = :s"), {"s": student_id}
            )
            .one()
            ._mapping
        )


def test_FR_YRE_002_lists_proposals_with_marks_and_days(own: Any) -> None:
    out = _read(own)
    by_id = {r.student.student_id: r for r in out.students}
    a1, a2 = by_id[own.ids["a1"]], by_id[own.ids["a2"]]
    assert a1.proposal.result == "passed"
    assert a1.proposal.percent == 80.0
    assert a1.proposal.days_attended == 3  # present, present, late
    assert a1.proposal.working_days == 4
    assert a2.proposal.result == "detained"
    assert a2.proposal.days_attended == 1
    assert a1.confirmed is None
    assert out.no_detention_class is False
    assert out.rules.no_detention_verified is False
    assert out.counts.unconfirmed == out.counts.students == 2


def test_FR_YRE_005_detained_needs_a_reason_and_no_aadhaar(own: Any) -> None:
    with pytest.raises(ValidationFailed) as err:
        _confirm(own, [YearResultIn(student_id=own.ids["a2"], result="detained")])
    assert err.value.errors[0]["code"] == "reason_required"
    body = "23412341234"
    aadhaar = body + str(verhoeff_check_digit(body))
    with pytest.raises(ValidationFailed) as bad:
        _confirm(
            own,
            [YearResultIn(student_id=own.ids["a2"], result="detained", reason=f"Ref {aadhaar}")],
        )
    assert bad.value.errors[0]["code"] == "aadhaar_full_number_rejected"


def test_FR_YRE_005_only_students_finishing_the_year_in_the_section(own: Any) -> None:
    with pytest.raises(ValidationFailed) as err:
        _confirm(own, [YearResultIn(student_id=own.ids["c1"], result="passed")])
    assert err.value.errors[0]["code"] == "not_in_section"


def test_FR_YRE_004_confirm_records_who_when_proposal_and_is_audited(
    own: Any, admin_engine: Engine
) -> None:
    out = _confirm(
        own,
        [
            YearResultIn(student_id=own.ids["a1"], result="passed", expected_version=0),
            YearResultIn(
                student_id=own.ids["a2"], result="detained", reason="Synthetic: long absence"
            ),
        ],
    )
    assert out.written == 2
    assert out.warnings == []
    row = _row(admin_engine, own.ids["a2"])
    assert row["result"] == "detained"
    assert row["proposed_result"] == "detained"
    assert row["reason"] == "Synthetic: long absence"
    assert row["confirmed_by"] == own.people["principal"].user_id
    assert row["days_attended"] == 1
    event = _events(admin_engine, own.tenant_id, "year_result.confirmed")[-1]
    assert event["resource_id"] == own.ids["section_9a"]
    assert event["summary"]["counts"]["detained"] == 1
    assert event["summary"]["detained_student_ids"] == [str(own.ids["a2"])]
    assert "Synthetic" not in json.dumps(event["summary"])  # the reason is never audited
    listed = {r.student.student_id: r for r in _read(own).students}
    assert listed[own.ids["a1"]].confirmed is not None
    assert listed[own.ids["a1"]].confirmed.confirmed_by_name == "Synthetic Principal Suresh"
    # Saving the same again changes nothing; a stale version is refused.
    again = _confirm(own, [YearResultIn(student_id=own.ids["a1"], result="passed")])
    assert (again.written, again.unchanged) == (0, 1)
    with pytest.raises(PreconditionFailed) as stale:
        _confirm(
            own, [YearResultIn(student_id=own.ids["a1"], result="promoted", expected_version=0)]
        )
    assert stale.value.code == "year_result_changed"


def test_FR_YRE_007_the_app_cannot_delete_a_result(own: Any, admin_engine: Engine) -> None:
    _confirm(own, [YearResultIn(student_id=own.ids["a1"], result="passed")])
    who = own.people["principal"].user_id
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(own.tenant_id, who) as db,
    ):
        db.execute(text("DELETE FROM sis.year_results WHERE student_id = :s"), {"s": own.ids["a1"]})


def test_FR_YRE_006_detained_in_a_no_detention_class_only_warns(own: Any) -> None:
    listed = _read(own, "section_8a")
    assert listed.no_detention_class is True
    out = _confirm(
        own,
        [YearResultIn(student_id=own.ids["e1"], result="detained", reason="Synthetic decision")],
        section_key="section_8a",
    )
    assert out.written == 1
    assert [w.code for w in out.warnings] == ["no_detention_class"]
    after = {r.student.student_id: r for r in _read(own, "section_8a").students}
    assert after[own.ids["e1"]].warnings == ["no_detention_class"]


def test_FR_YRE_008_held_back_suggestions_for_the_promotion(own: Any) -> None:
    _confirm(
        own,
        [YearResultIn(student_id=own.ids["e1"], result="detained", reason="Synthetic decision")],
        section_key="section_8a",
    )
    actor = _principal(own)
    with tenant_session(own.tenant_id, actor.user_id) as db:
        out = academics.held_back(db, actor, own.ids["year"])
    assert own.ids["e1"] in out.held_back_student_ids
    assert own.ids["e1"] in out.no_detention_student_ids
    assert own.ids["a1"] not in out.held_back_student_ids
    assert out.unconfirmed >= 1  # c1, x1 have no result


def test_FR_YRE_010_progression_sheet_lists_blanks(own: Any) -> None:
    actor = _principal(own)
    with tenant_session(own.tenant_id, actor.user_id) as db:
        out = academics.progression(db, actor, own.ids["year"])
    rows = {r.student_id: r for r in out.rows}
    assert rows[own.ids["a1"]].result == "passed"
    assert rows[own.ids["a1"]].class_code == "IX"
    assert rows[own.ids["a1"]].percent == 80.0
    assert "result" in rows[own.ids["c1"]].blanks
    assert "udise_pen" in rows[own.ids["a1"]].blanks
    assert out.blank_counts["result"] >= 1


def test_FR_YRE_013_progression_csv_is_audited_and_needs_results_read(
    own: Any, admin_engine: Engine
) -> None:
    actor = S.ctx(own, "office_admin")
    with tenant_session(own.tenant_id, actor.user_id) as db:
        file = academics.progression_csv(db, actor, own.ids["year"])
    assert file.body.startswith("﻿".encode())
    assert b"SYN-A1" in file.body
    event = _events(admin_engine, own.tenant_id, "year_end.progression_exported")[-1]
    assert event["resource_id"] == own.ids["year"]
    assert event["summary"]["rows"] == file.rows
    owner = S.ctx(own, "owner")  # export.portal? no year_result.read: refused either way
    with tenant_session(own.tenant_id, owner.user_id) as db, pytest.raises(Forbidden):
        academics.progression_csv(db, owner, own.ids["year"])


def test_FR_YRE_002_other_school_section_is_404(own: Any, world: Any) -> None:
    other = world.b.ids["section_9a"]
    actor = _principal(own)
    with tenant_session(own.tenant_id, actor.user_id) as db, pytest.raises(NotFound):
        academics.year_results(db, actor, other)


# --- TC suggestions (FR-YRE-014..FR-YRE-016) ------------------------------------------------------


def _tc_format(admin: Engine, tenant_id: uuid.UUID, key: str) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "UPDATE core.tenants SET settings = COALESCE(settings, '{}'::jsonb) "
                "|| CAST(:p AS jsonb) WHERE id = :t"
            ),
            {"p": json.dumps({"certificate_format": {"transfer": key}}), "t": tenant_id},
        )


def test_FR_YRE_014_tc_preview_suggests_promotion_and_days(own: Any, admin_engine: Engine) -> None:
    _confirm(own, [YearResultIn(student_id=own.ids["a1"], result="passed")])
    actor = _principal(own)
    _tc_format(admin_engine, own.tenant_id, "ap-tc-2027")
    try:
        with tenant_session(own.tenant_id, actor.user_id) as db:
            out = certificates.preview(db, actor, own.ids["a1"], "transfer")
    finally:
        _tc_format(admin_engine, own.tenant_id, "generic-v1")
    found = {s.key: (s.value, s.source) for s in out.input_suggestions}
    assert found["promotion"] == ("promoted", "year_result")
    assert found["working_days"] == ("4", "attendance_register")
    assert found["days_attended"] == ("3", "attendance_register")
    # Suggestions only: no line of the preview is filled with them.
    lines = {f.key: f.value for f in out.fields}
    assert lines.get("working_days") is None


def test_FR_YRE_015_generic_tc_gets_no_suggestions_for_inputs_it_lacks(own: Any) -> None:
    actor = _principal(own)
    with tenant_session(own.tenant_id, actor.user_id) as db:
        out = certificates.preview(db, actor, own.ids["a1"], "transfer")
        asked = {
            i.key for t in certificates.types_catalog(db) if t.key == "transfer" for i in t.inputs
        }
    assert {s.key for s in out.input_suggestions} <= asked


def test_FR_YRE_018_access_report_shows_annual_results(own: Any) -> None:
    from app.core.subjects import Subject

    _confirm(own, [YearResultIn(student_id=own.ids["a1"], result="passed")])
    with tenant_session(own.tenant_id) as db:
        sections = academics.subject_report(db, Subject(kind="student", id=own.ids["a1"]))
    titles = [s.title for s in sections]
    assert "Annual results" in titles


def test_FR_YRE_020_logs_carry_no_reason_or_names(
    own: Any, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    capsys.readouterr()
    secret = "Synthetic Logcheck Reason"
    _confirm(own, [YearResultIn(student_id=own.ids["a2"], result="detained", reason=secret)])
    _read(own)
    captured = capsys.readouterr()
    logs = captured.out + captured.err + caplog.text
    assert secret not in logs
    assert "Synthetica A2 Kumar" not in logs
