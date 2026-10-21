"""Data-principal access report and erasure: the WhatsApp messaging contribution (R3-1A;
FR-DPDP-009, FR-DPDP-012..014, ADR-0048 decisions 6 and 8, PRV-031, PRV-032; ADR-0043).
Synthetic school A of the shared world; guardians with synthetic mobiles (90xxxxxxxx)."""

from __future__ import annotations

import datetime as dt
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.core.ids import new_id
from app.core.subjects import ErasureOutcome, ReportSection, Subject
from app.messaging import repository as repo
from app.messaging import service
from app.students import service as students

pytestmark = pytest.mark.db
M = sys.modules["sos_test_messaging_support"]
TODAY = time.strftime("%Y-%m-%d")
CHILD = "Synthetica"


def _classes() -> set[str]:
    path = Path(service.__file__).parents[1] / "privacy" / "subjects.yaml"
    raw = yaml.safe_load(path.read_text("utf-8"))
    return set(raw["contributors"]["messaging"]["classes"])


def _queue(school: Any, student: uuid.UUID, guardian: uuid.UUID) -> uuid.UUID:
    with tenant_session(school.tenant_id) as db:
        queued = service.queue_message(
            db,
            purpose="absence_alert",
            guardian_id=guardian,
            student_id=student,
            source_type="attendance_day",
            source_id=uuid.uuid4(),
            dedupe_key=TODAY,
            variables={
                "school_name": "Synthetic Model School",
                "child_first_name": CHILD,
                "date": TODAY,
            },
        )
    assert queued is not None
    return queued.message_id


def _stop_from(school: Any, guardian: uuid.UUID) -> None:
    """A synthetic inbound STOP from the guardian's number (kind and time only)."""
    with tenant_session(school.tenant_id) as db:
        phone = students.guardian_phone_index(db, guardian)
        assert phone is not None
        repo.insert_inbound(
            db,
            id=new_id(),
            phone_bidx=phone.digest,
            phone_key_version=phone.key_version,
            kind="stop",
            received_at=dt.datetime.now(dt.UTC),
        )


def _seed(world: Any, api: Any) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A student and guardian opted in to notices and attendance, with one message each way.
    Returns (student, guardian, message)."""
    student, guardian, _ = M.guardian(world.a)
    for category in ("notices", "attendance"):
        res = M.opt_in(api, world.person("office_staff"), guardian, category)
        assert res.status_code == 201, res.text
    message = _queue(world.a, student, guardian)
    return student, guardian, message


def _report(school: Any, subject: Subject) -> list[ReportSection]:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        return service.subject_report(db, subject)


def _erase(
    school: Any, subject: Subject, keys: set[str], *, dry_run: bool, user: bool = True
) -> list[ErasureOutcome]:
    who = school.people["principal"].user_id if user else None
    with tenant_session(school.tenant_id, who) as db:
        return service.erase_for_subject(db, subject, keys, dry_run=dry_run)


def _vars_held(admin: Engine, message_id: uuid.UUID) -> bool:
    found = M.rows(
        admin,
        "SELECT vars_ciphertext IS NOT NULL AS held FROM msg.messages WHERE id = :m",
        m=message_id,
    )
    return bool(found[0]["held"])


def _current(admin: Engine, guardian: uuid.UUID) -> dict[str, str]:
    rows = M.rows(
        admin,
        "SELECT DISTINCT ON (category) category, status FROM msg.optins "
        "WHERE guardian_id = :g ORDER BY category, seq DESC",
        g=guardian,
    )
    return {r["category"]: r["status"] for r in rows}


def test_FR_DPDP_009_messaging_guardian_report_has_optins_messages_and_reply_kinds(
    world: Any, api: Any
) -> None:
    student, guardian, _ = _seed(world, api)
    _, other_guardian, _ = _seed(world, api)
    _stop_from(world.a, guardian)

    sections = _report(world.a, Subject("guardian", guardian))

    assert {s.class_key for s in sections} == _classes()
    by_key = {s.class_key: s for s in sections}
    optins = by_key["message_optins"]
    assert optins.columns == ("Category", "Decision", "Decided on", "How")
    assert {(r[0], r[1], r[3]) for r in optins.rows} == {
        ("Notices", "Opted in", "In person at the school"),
        ("Attendance", "Opted in", "In person at the school"),
    }
    messages = by_key["messages"]
    assert len(messages.rows) == 1
    assert messages.rows[0][0] == "Absence alert"
    assert messages.rows[0][2] == "Queued"
    assert by_key["message_inbound"].rows == (("STOP", "1"),)
    rendered = repr(sections)
    assert CHILD not in rendered  # the variables are never shown
    assert str(other_guardian) not in rendered
    assert str(student) not in rendered


def test_FR_DPDP_009_messaging_student_report_lists_messages_about_the_student(
    world: Any, api: Any
) -> None:
    student, _, _ = _seed(world, api)
    other, _, _ = _seed(world, api)

    sections = _report(world.a, Subject("student", student))

    assert [s.class_key for s in sections] == ["messages"]
    assert len(sections[0].rows) == 1
    assert CHILD not in repr(sections)
    assert len(_report(world.a, Subject("student", other))[0].rows) == 1


def test_FR_DPDP_009_messaging_report_is_empty_for_unknown_or_members(world: Any) -> None:
    assert _report(world.a, Subject("guardian", uuid.uuid4())) == []
    assert _report(world.a, Subject("student", uuid.uuid4())) == []
    assert _report(world.a, Subject("member", world.person("principal").membership_id)) == []


def test_FR_DPDP_013_messaging_dry_run_counts_and_changes_nothing(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    _, guardian, message = _seed(world, api)

    out = _erase(
        world.a, Subject("guardian", guardian), {"messages", "message_optins"}, dry_run=True
    )

    assert {o.class_key: o.count for o in out} == {"message_optins": 2, "messages": 1}
    assert _vars_held(admin_engine, message)
    assert _current(admin_engine, guardian) == {"notices": "opted_in", "attendance": "opted_in"}


def test_FR_DPDP_013_messaging_guardian_erasure_opts_out_and_erases_variables(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    _, guardian, message = _seed(world, api)
    _, other_guardian, other_message = _seed(world, api)

    out = _erase(
        world.a,
        Subject("guardian", guardian),
        {"messages", "message_optins", "message_inbound", "documents"},
        dry_run=False,
    )

    assert {o.class_key: o.count for o in out} == {"message_optins": 2, "messages": 1}
    assert not _vars_held(admin_engine, message)
    assert _current(admin_engine, guardian) == {"notices": "opted_out", "attendance": "opted_out"}
    events = M.rows(
        admin_engine,
        "SELECT summary FROM audit.events WHERE tenant_id = :t AND resource_id = :g "
        "AND action = 'messaging.optin.recorded' AND summary->>'reason' = 'privacy_request'",
        t=world.a.tenant_id,
        g=guardian,
    )
    assert {e["summary"]["method"] for e in events} == {"staff_recorded_stop"}
    assert len(events) == 2
    erased = M.rows(
        admin_engine,
        "SELECT summary FROM audit.events WHERE tenant_id = :t AND resource_id = :g "
        "AND action = 'messaging.message_vars.erased'",
        t=world.a.tenant_id,
        g=guardian,
    )
    assert [e["summary"]["messages"] for e in erased] == [1]
    # The other family is untouched.
    assert _vars_held(admin_engine, other_message)
    assert _current(admin_engine, other_guardian) == {
        "notices": "opted_in",
        "attendance": "opted_in",
    }
    # A second run has nothing left to change.
    again = _erase(
        world.a, Subject("guardian", guardian), {"messages", "message_optins"}, dry_run=True
    )
    assert {o.class_key: o.count for o in again} == {"message_optins": 0, "messages": 0}


def test_FR_DPDP_013_messaging_student_erasure_erases_only_that_students_variables(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    student, guardian, message = _seed(world, api)
    _, _, other_message = _seed(world, api)

    out = _erase(
        world.a, Subject("student", student), {"messages", "message_optins"}, dry_run=False
    )

    assert [(o.class_key, o.count) for o in out] == [("messages", 1)]
    assert not _vars_held(admin_engine, message)
    assert _vars_held(admin_engine, other_message)
    assert _current(admin_engine, guardian) == {"notices": "opted_in", "attendance": "opted_in"}


def test_FR_DPDP_013_messaging_optout_without_staff_member_is_a_manual_step(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    _, guardian, _ = _seed(world, api)

    out = _erase(
        world.a, Subject("guardian", guardian), {"message_optins"}, dry_run=False, user=False
    )

    assert len(out) == 1
    assert out[0].count == 0
    assert out[0].manual_steps
    assert _current(admin_engine, guardian) == {"notices": "opted_in", "attendance": "opted_in"}
    assert _erase(world.a, Subject("member", guardian), {"messages"}, dry_run=True) == []
