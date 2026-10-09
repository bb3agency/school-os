"""Early warning, notes and timeline through the service (US-1704..US-1709; FR-EW-*; 08 §4
PRV-003..005; invariants 3, 4, 7, 14). Separate synthetic school from ``support.py``.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.redaction import verhoeff_check_digit
from app.insights import service as insights
from app.insights.schemas import ActionIn, AssignIn, CloseIn, EraseIn, NoteIn, SettingsIn

pytestmark = pytest.mark.db


def _support() -> Any:
    import sys

    return sys.modules["sos_test_insights_support"]


S = _support()


def _events(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT resource_id, summary, actor_type FROM audit.events "
                    "WHERE tenant_id = :t AND action = :a ORDER BY seq"
                ),
                {"t": tenant_id, "a": action},
            )
        ]


def _codes(err: pytest.ExceptionInfo[ValidationFailed]) -> set[str]:
    return {e["code"] for e in err.value.errors}


def _aadhaar() -> str:
    return "23456789012" + verhoeff_check_digit("23456789012")


def _open_flag(admin: Engine, school: Any, student_key: str, rule: str) -> dict[str, Any]:
    flags = [
        f
        for f in S.flags_of(admin, school.tenant_id, school.ids[student_key])
        if f["rule"] == rule and f["status"] != "closed"
    ]
    assert len(flags) == 1, flags
    found: dict[str, Any] = flags[0]
    return found


# --- raising flags (FR-EW-002..006) ---------------------------------------------------------------


def test_FR_EW_002_three_absences_raise_one_flag_for_the_class_teacher(
    school: Any, admin_engine: Engine
) -> None:
    days = S.school_days(4)
    S.record(
        school, "section_9a", {school.ids["a1"]: ["present", "absent", "absent", "absent"]}, days
    )
    raised = S.evaluate(school, [school.ids["a1"]])
    assert raised >= 1
    flag = _open_flag(admin_engine, school, "a1", "attendance_streak")
    assert flag["owner_membership_id"] == school.people["ct"].membership_id
    assert flag["indicator"] == "attendance"
    assert flag["due_on"] == flag["raised_on"] + dt.timedelta(days=7)
    assert flag["evidence"]["days"] == 3
    assert flag["basis"] == f"run:{days[1].isoformat()}"
    assert flag["rules_version"] == 1
    # FR-EW-006: the owner is told, with ids and codes only.
    sent = [
        n
        for n in S.notifications(admin_engine, school.tenant_id, "insights.flag_raised")
        if n["resource_id"] == flag["id"]
    ]
    assert [n["recipient_membership_id"] for n in sent] == [school.people["ct"].membership_id]
    assert sent[0]["params"] == {"flag_id": str(flag["id"]), "rule": "attendance_streak"}
    event = next(
        e
        for e in _events(admin_engine, school.tenant_id, "insights.flag_raised")
        if e["resource_id"] == flag["id"]
    )
    assert event["actor_type"] == "system"
    # FR-EW-003: evaluating again raises nothing new for the same run.
    S.evaluate(school, [school.ids["a1"]])
    assert (
        len(
            [
                f
                for f in S.flags_of(admin_engine, school.tenant_id, school.ids["a1"])
                if f["rule"] == "attendance_streak"
            ]
        )
        == 1
    )


def test_FR_EW_004_no_eligible_class_teacher_leaves_it_unassigned_and_tells_the_principal(
    school: Any, admin_engine: Engine
) -> None:
    days = S.school_days(3)
    S.record(school, "section_9c", {school.ids["c1"]: ["absent"] * 3}, days)
    S.evaluate(school, [school.ids["c1"]])
    flag = _open_flag(admin_engine, school, "c1", "attendance_streak")
    assert flag["owner_membership_id"] is None
    sent = [
        n["recipient_membership_id"]
        for n in S.notifications(admin_engine, school.tenant_id, "insights.flag_raised")
        if n["resource_id"] == flag["id"]
    ]
    assert sent == [school.people["principal"].membership_id]


def test_FR_EW_002_course_rules_from_marks(school: Any, admin_engine: Engine) -> None:
    first = S.exam(school, f"Synthetic Q1 {uuid.uuid4().hex[:5]}", S.school_days(30)[0])
    second = S.exam(school, f"Synthetic Q2 {uuid.uuid4().hex[:5]}", S.school_days(2)[0])
    S.marks(school, "section_9c", first, {school.ids["c1"]: [("Maths", 80, 100)]})
    S.marks(school, "section_9c", second, {school.ids["c1"]: [("Maths", 30, 100)]})
    S.evaluate(school, [school.ids["c1"]])
    low = _open_flag(admin_engine, school, "c1", "course_low")
    decline = _open_flag(admin_engine, school, "c1", "course_decline")
    assert low["basis"] == decline["basis"]
    assert decline["evidence"]["drop"] >= 15


def test_FR_EW_002_repeated_concern_notes_raise_a_behaviour_flag(
    school: Any, admin_engine: Engine
) -> None:
    for _ in range(3):
        S.note(school, "a2", "Synthetic concern: upset in class.", category="concern")
    S.evaluate(school, [school.ids["a2"]])
    flag = _open_flag(admin_engine, school, "a2", "behaviour_concerns")
    assert flag["evidence"]["concerns"] >= 3
    assert "text" not in flag["evidence"]


# --- acting on flags (FR-EW-007..009) -------------------------------------------------------------


def test_FR_EW_007_actions_then_close_with_a_reason(school: Any, admin_engine: Engine) -> None:
    flag = S.manual_flag(school, "a1", who=S.ct_ctx(school))
    assert flag.rule == "manual"
    assert flag.status == "open"
    assert flag.actioned is False
    assert [a.kind for a in flag.actions] == ["raised"]
    ct = S.ct_ctx(school)
    with tenant_session(school.tenant_id, ct.user_id) as db:
        acted = insights.add_action(
            db, ct, flag.id, ActionIn(kind="called_parent", note="Synthetic: spoke to mother.")
        )
    assert acted.status == "in_progress"
    assert acted.actioned is True
    assert acted.actions[-1].note == "Synthetic: spoke to mother."
    with tenant_session(school.tenant_id, ct.user_id) as db, pytest.raises(PreconditionFailed):
        insights.close_flag(db, ct, flag.id, CloseIn(reason="improved"), acted.version - 1)
    with tenant_session(school.tenant_id, ct.user_id) as db:
        closed = insights.close_flag(db, ct, flag.id, CloseIn(reason="improved"), acted.version)
    assert closed.status == "closed"
    assert closed.close_reason == "improved"
    with tenant_session(school.tenant_id, ct.user_id) as db, pytest.raises(Conflict) as err:
        insights.add_action(db, ct, flag.id, ActionIn(kind="other"))
    assert err.value.code == "flag_closed"
    event = _events(admin_engine, school.tenant_id, "insights.flag_action_added")[-1]
    assert event["summary"] == {"kind": "called_parent", "has_note": True, "first_action": True}
    # The note is encrypted at rest.
    with admin_engine.connect() as c:
        blob: Any = c.execute(
            text(
                "SELECT note_ciphertext FROM sis.flag_actions WHERE flag_id = :f AND kind = "
                "'called_parent'"
            ),
            {"f": flag.id},
        ).scalar_one()
    assert b"spoke to mother" not in bytes(blob)


def test_FR_EW_007_future_actions_and_aadhaar_are_refused(school: Any) -> None:
    flag = S.manual_flag(school, "a1")
    ct = S.ct_ctx(school)
    future = ActionIn(kind="other", acted_on=insights.today_ist() + dt.timedelta(days=3))
    with tenant_session(school.tenant_id, ct.user_id) as db, pytest.raises(ValidationFailed) as err:
        insights.add_action(db, ct, flag.id, future)
    assert "future_date" in _codes(err)
    with tenant_session(school.tenant_id, ct.user_id) as db, pytest.raises(ValidationFailed) as err:
        insights.add_action(db, ct, flag.id, ActionIn(kind="other", note=f"Card {_aadhaar()}"))
    assert _codes(err) == {"aadhaar_full_number_rejected"}


def test_FR_EW_009_mine_and_all_lists(school: Any) -> None:
    S.manual_flag(school, "a2", who=S.ct_ctx(school))
    ct = S.ct_ctx(school)
    with tenant_session(school.tenant_id, ct.user_id) as db:
        mine = insights.list_flags(
            db,
            ct,
            view="mine",
            status=None,
            indicator=None,
            section_id=None,
            student_id=None,
            due=None,
            limit=200,
            cursor=None,
        )
        everything = insights.list_flags(
            db,
            ct,
            view="all",
            status=None,
            indicator=None,
            section_id=None,
            student_id=None,
            due=None,
            limit=200,
            cursor=None,
        )
    assert mine.data
    assert all(f.owner and f.owner.membership_id == ct.membership_id for f in mine.data)
    # A class teacher never sees another section's students' flags.
    assert {f.student.id for f in everything.data} <= {school.ids["a1"], school.ids["a2"]}
    assert all(f.student.section_label == "IX-A" for f in everything.data)


# --- scope and purpose limits (FR-EW-011, PRV-004) ------------------------------------------------


@pytest.mark.parametrize("role", ["owner", "office_admin", "exam", "teacher", "ct9c"])
def test_FR_EW_011_only_the_class_teacher_and_principal_see_insights(
    school: Any, role: str
) -> None:
    flag = S.manual_flag(school, "a1")
    if role == "ct9c":
        who = S.ct_ctx(school, "ct9c", "section_9c")
    elif role == "teacher":
        who = S.ctx(school, "teacher", class_ids=frozenset({school.ids["class_x"]}))
    elif role == "exam":
        who = S.ctx(school, "exam_coordinator", school.people["exam"])
    else:
        who = S.ctx(school, role)
    with tenant_session(school.tenant_id, who.user_id) as db:
        for call in (
            lambda: insights.get_flag(db, who, flag.id),
            lambda: insights.list_notes(db, who, school.ids["a1"]),
            lambda: insights.timeline(db, who, school.ids["a1"]),
        ):
            with pytest.raises(NotFound):
                call()


def test_FR_EW_011_other_schools_flags_are_not_found(school: Any, world: Any) -> None:
    flag = S.manual_flag(school, "a1")
    principal_b = S.ctx(world.b, "owner")
    with tenant_session(world.b.tenant_id, principal_b.user_id) as db, pytest.raises(NotFound):
        insights.get_flag(db, principal_b, flag.id)


# --- notes (FR-EW-010, invariant 4) ---------------------------------------------------------------


def test_FR_EW_010_notes_are_encrypted_and_readable_only_in_scope(
    school: Any, admin_engine: Engine
) -> None:
    text_ = "Synthetic observation: reads well aloud."
    created = S.note(school, "a1", text_, category="positive")
    with admin_engine.connect() as c:
        blob: Any = c.execute(
            text("SELECT body_ciphertext FROM sis.behaviour_notes WHERE id = :i"),
            {"i": created.id},
        ).scalar_one()
    assert text_.encode() not in bytes(blob)
    ct = S.ct_ctx(school)
    with tenant_session(school.tenant_id, ct.user_id) as db:
        notes = insights.list_notes(db, ct, school.ids["a1"])
    assert text_ in [n.text for n in notes]
    viewed = _events(admin_engine, school.tenant_id, "insights.viewed")[-1]
    assert viewed["summary"]["view"] == "notes"
    assert text_ not in json.dumps(viewed["summary"])


@pytest.mark.parametrize(
    ("body", "noted_on", "code"),
    [
        (
            "Synthetic card " + "23456789012" + verhoeff_check_digit("23456789012"),
            None,
            "aadhaar_full_number_rejected",
        ),
        ("Synthetic", 5, "future_date"),
        ("Synthetic", -120, "too_old"),
    ],
)
def test_FR_EW_010_bad_notes_are_refused(
    school: Any, body: str, noted_on: int | None, code: str
) -> None:
    ct = S.ct_ctx(school)
    day = None if noted_on is None else insights.today_ist() + dt.timedelta(days=noted_on)
    with tenant_session(school.tenant_id, ct.user_id) as db, pytest.raises(ValidationFailed) as err:
        insights.add_note(
            db, ct, school.ids["a1"], NoteIn(category="concern", text=body, noted_on=day)
        )
    assert code in _codes(err)


# --- timeline (FR-EW-013) -------------------------------------------------------------------------


def test_FR_EW_013_timeline_lists_every_kind_newest_first(
    school: Any, admin_engine: Engine
) -> None:
    S.record(school, "section_9a", {school.ids["a2"]: ["present", "late"]}, S.school_days(2))
    exam_id = S.exam(school, f"Synthetic T {uuid.uuid4().hex[:5]}", S.school_days(1)[0])
    S.marks(school, "section_9a", exam_id, {school.ids["a2"]: [("Telugu", 45, 50)]})
    S.note(school, "a2", "Synthetic: good project work.", category="positive")
    S.manual_flag(school, "a2")
    ct = S.ct_ctx(school)
    with tenant_session(school.tenant_id, ct.user_id) as db:
        out = insights.timeline(db, ct, school.ids["a2"])
    kinds = {i.kind for i in out.items}
    assert {"enrolment", "attendance_month", "exam", "note", "flag"} <= kinds
    assert "certificate" not in kinds  # class teachers do not hold certificate.read
    assert [i.on for i in out.items] == sorted((i.on for i in out.items), reverse=True)
    assert out.indicators.course.percent is not None
    assert out.student.section_label == "IX-A"
    viewed = _events(admin_engine, school.tenant_id, "insights.viewed")[-1]
    assert viewed["summary"]["view"] == "timeline"
    assert viewed["resource_id"] == school.ids["a2"]


def _timeline_with_attendance_and_marks(school: Any) -> None:
    S.record(school, "section_9a", {school.ids["a2"]: ["present", "absent"]}, S.school_days(2))
    exam_id = S.exam(school, f"Synthetic T {uuid.uuid4().hex[:5]}", S.school_days(1)[0])
    S.marks(school, "section_9a", exam_id, {school.ids["a2"]: [("Maths", 30, 50)]})


def test_AA_17_timeline_shows_attendance_and_exams_only_with_their_read_permissions(
    school: Any,
) -> None:
    """A custom role holding insights.read (and the student reads it needs) but neither
    attendance.read nor marks.read sees the timeline without attendance months or exams."""
    _timeline_with_attendance_and_marks(school)
    ct = S.ct_ctx(school)
    kinds: dict[frozenset[str], set[str]] = {}
    for dropped in (
        frozenset({"attendance.read", "marks.read"}),
        frozenset({"attendance.read"}),
        frozenset({"marks.read"}),
    ):
        custom = dataclasses.replace(ct, permissions=ct.permissions - dropped)
        assert custom.has("insights.read")
        with tenant_session(school.tenant_id, custom.user_id) as db:
            out = insights.timeline(db, custom, school.ids["a2"])
        kinds[dropped] = {i.kind for i in out.items}
    both = frozenset({"attendance.read", "marks.read"})
    assert "attendance_month" not in kinds[both]
    assert "exam" not in kinds[both]
    assert "enrolment" in kinds[both]
    assert "attendance_month" not in kinds[frozenset({"attendance.read"})]
    assert "exam" in kinds[frozenset({"attendance.read"})]
    assert "attendance_month" in kinds[frozenset({"marks.read"})]
    assert "exam" not in kinds[frozenset({"marks.read"})]


def test_AA_17_timeline_needs_attendance_and_marks_read_in_scope_for_the_student(
    school: Any,
) -> None:
    """insights.read school-wide, attendance.read and marks.read only for another section:
    the 9A student's timeline has no attendance months or exams."""
    _timeline_with_attendance_and_marks(school)
    principal = S.principal_ctx(school)
    custom = dataclasses.replace(
        principal,
        scopes=dataclasses.replace(
            principal.scopes, school=False, section_ids=frozenset({school.ids["section_9c"]})
        ),
        scoped_permissions=frozenset({"attendance.read", "marks.read"}),
    )
    with tenant_session(school.tenant_id, custom.user_id) as db:
        kinds = {i.kind for i in insights.timeline(db, custom, school.ids["a2"]).items}
    assert "enrolment" in kinds
    assert not kinds & {"attendance_month", "exam"}
    with tenant_session(school.tenant_id, principal.user_id) as db:
        kinds = {i.kind for i in insights.timeline(db, principal, school.ids["a2"]).items}
    assert {"attendance_month", "exam"} <= kinds


# --- the principal (FR-EW-014, FR-EW-015) ---------------------------------------------------------


def test_FR_EW_014_reassign_only_to_eligible_staff(school: Any, admin_engine: Engine) -> None:
    flag = S.manual_flag(school, "a1")
    principal = S.principal_ctx(school)
    with tenant_session(school.tenant_id, principal.user_id) as db:
        owners = {o.membership_id for o in insights.owners(db, principal, flag.id)}
    assert school.people["ct"].membership_id in owners
    assert school.people["principal"].membership_id in owners
    assert school.people["ct9c"].membership_id not in owners
    assert school.people["owner"].membership_id not in owners
    with (
        tenant_session(school.tenant_id, principal.user_id) as db,
        pytest.raises(ValidationFailed) as err,
    ):
        insights.assign_flag(
            db,
            principal,
            flag.id,
            AssignIn(owner_membership_id=school.people["ct9c"].membership_id),
            flag.version,
        )
    assert _codes(err) == {"owner_not_eligible"}
    with tenant_session(school.tenant_id, principal.user_id) as db:
        moved = insights.assign_flag(
            db,
            principal,
            flag.id,
            AssignIn(owner_membership_id=school.people["principal"].membership_id),
            flag.version,
        )
    assert moved.owner is not None
    assert moved.owner.membership_id == school.people["principal"].membership_id


def test_FR_EW_014_settings_stay_within_bounds(school: Any, admin_engine: Engine) -> None:
    principal = S.principal_ctx(school)
    with tenant_session(school.tenant_id, principal.user_id) as db:
        current = insights.get_settings(db, principal)
    rules = {r.key: r for r in current.rules}
    assert rules["attendance_streak"].can_disable is False
    bad = [
        ({"attendance_streak": {"threshold": 9}}, "threshold_out_of_bounds"),
        ({"attendance_streak": {"enabled": False}}, "rule_required"),
        ({"no_such_rule": {"threshold": 3}}, "unknown_rule"),
    ]
    for body, code in bad:
        with (
            tenant_session(school.tenant_id, principal.user_id) as db,
            pytest.raises(ValidationFailed) as err,
        ):
            insights.update_settings(
                db, principal, SettingsIn.model_validate({"rules": body}), current.version
            )
        assert code in _codes(err)
    with tenant_session(school.tenant_id, principal.user_id) as db:
        saved = insights.update_settings(
            db,
            principal,
            SettingsIn.model_validate(
                {"rules": {"course_low": {"threshold": 40, "enabled": True}}}
            ),
            current.version,
        )
    assert {r.key: r.threshold for r in saved.rules}["course_low"] == 40
    assert saved.version == current.version + 1
    event = _events(admin_engine, school.tenant_id, "insights.settings_updated")[-1]
    assert event["summary"]["changes"] == [
        {"rule": "course_low", "field": "threshold", "from": 35, "to": 40}
    ]
    with (
        tenant_session(school.tenant_id, principal.user_id) as db,
        pytest.raises(PreconditionFailed),
    ):
        insights.update_settings(db, principal, SettingsIn(rules={}), current.version)
    # Back to the default: only differences are stored.
    with tenant_session(school.tenant_id, principal.user_id) as db:
        back = insights.update_settings(
            db,
            principal,
            SettingsIn.model_validate({"rules": {"course_low": {"threshold": 35}}}),
            saved.version,
        )
    assert {r.key: r.threshold for r in back.rules}["course_low"] == 35


def test_FR_EW_014_erasing_a_note_and_a_flag_keeps_only_the_reason(
    school: Any, admin_engine: Engine
) -> None:
    created = S.note(school, "a1", "Synthetic: entered for the wrong child.")
    flag = S.manual_flag(school, "a1")
    principal = S.principal_ctx(school)
    ct = S.ct_ctx(school)
    with tenant_session(school.tenant_id, ct.user_id) as db, pytest.raises(NotFound):
        # The class teacher may not erase (no insights.manage): nothing is found for them.
        insights.erase_note(db, ct, created.id, EraseIn(reason="entered_in_error"))
    with tenant_session(school.tenant_id, principal.user_id) as db:
        insights.erase_note(db, principal, created.id, EraseIn(reason="entered_in_error"))
        insights.erase_flag(db, principal, flag.id, EraseIn(reason="parent_request"))
    with admin_engine.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM sis.behaviour_notes WHERE id = :i"), {"i": created.id}
            ).scalar_one()
            == 0
        )
        assert (
            c.execute(
                text("SELECT count(*) FROM sis.flag_actions WHERE flag_id = :i"), {"i": flag.id}
            ).scalar_one()
            == 0
        )
    erased = _events(admin_engine, school.tenant_id, "insights.note_erased")[-1]
    assert erased["summary"]["reason"] == "entered_in_error"
    assert "wrong child" not in json.dumps(erased["summary"])


def test_FR_EW_015_summary_counts_only(school: Any) -> None:
    principal = S.principal_ctx(school)
    with tenant_session(school.tenant_id, principal.user_id) as db:
        out = insights.summary(db, principal)
    assert out.raised >= 1
    assert out.raised == out.actioned_on_time + out.actioned_late + out.not_actioned
    assert set(out.model_dump()) == {
        "since",
        "raised",
        "actioned_on_time",
        "actioned_late",
        "not_actioned",
        "overdue",
        "open",
    }


# --- reminders and retention (FR-EW-006, FR-EW-017) -----------------------------------------------


def test_FR_EW_006_overdue_reminder_is_sent_once(school: Any, admin_engine: Engine) -> None:
    flag = S.manual_flag(school, "a2", who=S.ct_ctx(school))
    later = insights.today_ist() + dt.timedelta(days=30)
    with tenant_session(school.tenant_id) as db:
        first = insights.send_reminders(db, today=later)
    with tenant_session(school.tenant_id) as db:
        again = insights.send_reminders(db, today=later)
    assert first >= 1
    assert again == 0
    sent = [
        n
        for n in S.notifications(admin_engine, school.tenant_id, "insights.flag_overdue")
        if n["resource_id"] == flag.id
    ]
    assert [n["recipient_membership_id"] for n in sent] == [school.people["ct"].membership_id]


def test_FR_EW_017_old_notes_and_long_closed_flags_are_purged(
    school: Any, admin_engine: Engine
) -> None:
    old = S.note(school, "x1", "Synthetic: old note.", who=S.principal_ctx(school))
    kept_open = S.manual_flag(school, "x1")
    closed = S.manual_flag(school, "x1")
    principal = S.principal_ctx(school)
    with tenant_session(school.tenant_id, principal.user_id) as db:
        insights.close_flag(db, principal, closed.id, CloseIn(reason="no_concern"), closed.version)
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE sis.behaviour_notes SET noted_on = noted_on - 400 WHERE id = :i"),
            {"i": old.id},
        )
        c.execute(
            text(
                "UPDATE sis.insight_flags SET closed_at = closed_at - interval '400 days' "
                "WHERE id = :i"
            ),
            {"i": closed.id},
        )
    with tenant_session(school.tenant_id) as db:
        purged = insights.purge_expired(db)
    assert purged["notes"] >= 1
    assert purged["flags"] >= 1
    with admin_engine.connect() as c:
        remaining: set[uuid.UUID] = set(
            c.execute(
                text("SELECT id FROM sis.insight_flags WHERE id IN (:a, :b)"),
                {"a": kept_open.id, "b": closed.id},
            ).scalars()
        )
    assert remaining == {kept_open.id}


def test_FR_EW_018_full_export_masks_restricted_text_unless_included(school: Any) -> None:
    S.note(school, "a1", "Synthetic export note.")
    with tenant_session(school.tenant_id) as db:
        masked = {t.name: t for t in insights.export_records(db, include_sensitive=False)}
        shown = {t.name: t for t in insights.export_records(db, include_sensitive=True)}
    notes_masked = masked["behaviour_notes"]
    col = notes_masked.columns.index("text")
    assert {r[col] for r in notes_masked.rows} == {"••••"}
    assert notes_masked.notes == ("c3_masked",)
    col = shown["behaviour_notes"].columns.index("text")
    assert "Synthetic export note." in {r[col] for r in shown["behaviour_notes"].rows}
    assert set(masked) == {"behaviour_notes", "insight_flags", "flag_actions", "insight_settings"}
    for table in masked.values():
        assert "tenant_id" not in table.columns
        assert not any(c.endswith("ciphertext") for c in table.columns)
