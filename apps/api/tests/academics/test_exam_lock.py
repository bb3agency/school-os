"""Exam lock and marks history (FT-01, audit YA-04; lead decision D84; FR-MRK-008..FR-MRK-016;
invariants 1, 3, 7). Synthetic school from ``tests/insights/support.py``, built for this module
so the shared school's exams and marks stay untouched.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from app.academics import service as academics
from app.academics.schemas import ExamCreate, ExamUnlockIn, MarkIn, MarksWrite, SheetIn
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.redaction import verhoeff_check_digit

pytestmark = pytest.mark.db


def _support() -> Any:
    import sys

    return sys.modules["sos_test_insights_support"]


S = _support()


@pytest.fixture(scope="module")
def own(world: Any, admin_engine: Engine) -> Any:
    return S.insights_school(admin_engine)


def _exam(school: Any) -> uuid.UUID:
    actor = S.principal_ctx(school)
    day = S.school_days(1)[0]
    with tenant_session(school.tenant_id, actor.user_id) as db:
        out = academics.create_exam(
            db, actor, ExamCreate(name=f"Synthetic FA {uuid.uuid4().hex[:6]}", held_on=day)
        )
    return out.id


def _exam_ctx(school: Any) -> Any:
    return S.ctx(school, "exam_coordinator", school.people["exam"])


def _lock(school: Any, exam_id: uuid.UUID, who: Any | None = None) -> Any:
    actor = who or _exam_ctx(school)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        return academics.lock_exam(db, actor, exam_id)


def _unlock(school: Any, exam_id: uuid.UUID, reason: str = "Synthetic re-totalling") -> Any:
    actor = S.principal_ctx(school)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        return academics.unlock_exam(db, actor, exam_id, ExamUnlockIn(reason=reason))


def _write(
    school: Any, exam_id: uuid.UUID, marks: float, *, reason: str | None = None, key: str = "a1"
) -> Any:
    actor = S.principal_ctx(school)
    body = MarksWrite(
        entries=[
            MarkIn(student_id=school.ids[key], subject="Maths", max_marks=Decimal(50), marks=marks)
        ],
        reason=reason,
    )
    with tenant_session(school.tenant_id, actor.user_id) as db:
        return academics.record_marks(db, actor, school.ids["section_9a"], exam_id, body)


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


def _history(admin: Engine, exam_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text("SELECT * FROM sis.exam_mark_history WHERE exam_id = :e ORDER BY changed_at"),
                {"e": exam_id},
            )
        ]


def test_FR_MRK_008_lock_refuses_marks_writes_and_is_audited(
    own: Any, admin_engine: Engine
) -> None:
    exam_id = _exam(own)
    _write(own, exam_id, 30)
    out = _lock(own, exam_id)
    assert out.locked is True
    assert out.locked_at is not None
    event = _events(admin_engine, own.tenant_id, "exam.locked")[-1]
    assert event["resource_id"] == exam_id
    assert event["summary"]["relock"] is False
    with pytest.raises(Conflict) as err:
        _write(own, exam_id, 31)
    assert err.value.code == "exam_locked"
    with pytest.raises(Conflict) as again:
        _lock(own, exam_id)
    assert again.value.code == "exam_already_locked"
    actor = S.principal_ctx(own)
    with tenant_session(own.tenant_id, actor.user_id) as db:
        listed = {e.id: e for e in academics.list_exams(db, actor)}
    assert listed[exam_id].locked is True


def test_FR_MRK_009_a_locked_exam_refuses_a_sheet_before_reading_it(
    own: Any, admin_engine: Engine
) -> None:
    exam_id = _exam(own)
    _lock(own, exam_id)
    doc = S.sheet_document(
        admin_engine, own, b"Adm No,Maths\nMax marks,50\nSYN-A1,40\n", uploader="principal"
    )
    with pytest.raises(Conflict) as err:
        academics.preview_marks_sheet(
            S.principal_ctx(own), own.ids["section_9a"], exam_id, SheetIn(document_id=doc)
        )
    assert err.value.code == "exam_locked"
    assert S.document_exists(admin_engine, doc)  # never read, so not consumed


def test_FR_MRK_010_unlock_needs_a_lock_and_a_clean_reason(own: Any, admin_engine: Engine) -> None:
    exam_id = _exam(own)
    with pytest.raises(Conflict) as err:
        _unlock(own, exam_id)
    assert err.value.code == "exam_not_locked"
    _lock(own, exam_id)
    number = "23456789012" + verhoeff_check_digit("23456789012")
    with pytest.raises(ValidationFailed) as bad:
        _unlock(own, exam_id, reason=f"Synthetic {number}")
    assert {e["code"] for e in bad.value.errors} == {"aadhaar_full_number_rejected"}
    with pytest.raises(ValueError, match="at least 1 character"):
        ExamUnlockIn(reason="  ")
    out = _unlock(own, exam_id, reason="Synthetic re-totalling of paper 2")
    assert out.locked is False
    assert out.unlocked_at is not None
    assert out.unlock_reason == "Synthetic re-totalling of paper 2"
    event = _events(admin_engine, own.tenant_id, "exam.unlocked")[-1]
    assert event["resource_id"] == exam_id
    assert "re-totalling" not in str(event["summary"])  # free text stays out of the audit
    _write(own, exam_id, 44)  # corrections need no second person (D84)


def test_FR_MRK_012_every_change_after_the_first_entry_is_kept(
    own: Any, admin_engine: Engine
) -> None:
    exam_id = _exam(own)
    _write(own, exam_id, 20)
    assert _history(admin_engine, exam_id) == []  # a first entry has no history
    _write(own, exam_id, 20)  # unchanged: nothing kept
    assert _history(admin_engine, exam_id) == []
    _write(own, exam_id, 25, reason="Synthetic recount")
    before_lock = _history(admin_engine, exam_id)
    assert len(before_lock) == 1
    row = before_lock[0]
    assert (row["previous_marks"], row["new_marks"]) == (Decimal("20.00"), Decimal("25.00"))
    assert row["after_lock"] is False
    assert row["reason"] == "Synthetic recount"
    assert row["changed_by"] == own.people["principal"].user_id
    _lock(own, exam_id)
    _unlock(own, exam_id, reason="Synthetic paper re-evaluated")
    _write(own, exam_id, 27)
    rows = _history(admin_engine, exam_id)
    assert len(rows) == 2
    assert rows[-1]["after_lock"] is True
    assert rows[-1]["reason"] == "Synthetic paper re-evaluated"
    recorded = _events(admin_engine, own.tenant_id, "marks.recorded")[-1]["summary"]
    assert recorded["history"] == 1
    assert recorded["after_lock"] is True

    actor = S.principal_ctx(own)
    with tenant_session(own.tenant_id, actor.user_id) as db:
        grid = academics.section_marks(db, actor, own.ids["section_9a"], exam_id)
        history = academics.marks_history(db, actor, own.ids["section_9a"], exam_id)
    a1 = next(s for s in grid.students if s.student.student_id == own.ids["a1"])
    assert [m.changed_after_lock for m in a1.marks] == [True]
    assert [c.new_marks for c in history.changes] == [Decimal("27.00"), Decimal("25.00")]
    assert history.changes[0].after_lock is True
    assert history.changes[0].changed_by_name == "Synthetic Principal Suresh"
    assert history.changes[0].student.student_id == own.ids["a1"]


def test_FR_MRK_013_history_is_scoped_to_the_callers_section(own: Any) -> None:
    exam_id = _exam(own)
    _write(own, exam_id, 10)
    _write(own, exam_id, 12)
    ct9c = S.ct_ctx(own, "ct9c", "section_9c")
    with tenant_session(own.tenant_id, ct9c.user_id) as db, pytest.raises(NotFound):
        academics.marks_history(db, ct9c, own.ids["section_9a"], exam_id)
    ct = S.ct_ctx(own)
    with tenant_session(own.tenant_id, ct.user_id) as db:
        assert len(academics.marks_history(db, ct, own.ids["section_9a"], exam_id).changes) == 1


def test_FR_MRK_012_history_is_append_only_for_the_app(own: Any, admin_engine: Engine) -> None:
    exam_id = _exam(own)
    _write(own, exam_id, 5)
    _write(own, exam_id, 6)
    for sql in (
        "UPDATE sis.exam_mark_history SET reason = 'x' WHERE exam_id = :e",
        "DELETE FROM sis.exam_mark_history WHERE exam_id = :e",
    ):
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            tenant_session(own.tenant_id) as db,
        ):
            db.execute(text(sql), {"e": exam_id})
    assert len(_history(admin_engine, exam_id)) == 1


def test_invariant_1_history_rows_of_another_school_are_invisible(
    own: Any, school: Any, admin_engine: Engine
) -> None:
    exam_id = _exam(own)
    _write(own, exam_id, 7)
    _write(own, exam_id, 8)
    with tenant_session(school.tenant_id) as db:
        seen: int = db.execute(
            text("SELECT count(*) FROM sis.exam_mark_history WHERE exam_id = :e"), {"e": exam_id}
        ).scalar_one()
    assert seen == 0
    assert len(_history(admin_engine, exam_id)) == 1


def test_FR_MRK_014_relock_is_flagged_in_the_audit(own: Any, admin_engine: Engine) -> None:
    exam_id = _exam(own)
    _lock(own, exam_id)
    _unlock(own, exam_id)
    _lock(own, exam_id, who=S.principal_ctx(own))
    assert _events(admin_engine, own.tenant_id, "exam.locked")[-1]["summary"]["relock"] is True
    when = dt.datetime.now(dt.UTC)
    with admin_engine.connect() as c:
        locked_at: dt.datetime = c.execute(
            text("SELECT locked_at FROM sis.exams WHERE id = :e"), {"e": exam_id}
        ).scalar_one()
    assert locked_at <= when
