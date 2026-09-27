"""``student.values.changed`` outbox events for incremental data-quality checks (FR-DQ-002,
FR-OPS-004): one event per transaction, IDs and attribute keys only, at most 100 students each."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.students import service as students
from app.students.schemas import EnrollmentIn, StudentCreate, ValueIn

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
EVENT = "student.values.changed"


def _events(admin: Engine, tenant_id: uuid.UUID, student_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT payload FROM ops.outbox WHERE tenant_id = :t AND event_type = :e "
                "AND payload -> 'student_ids' ? :s ORDER BY created_at"
            ),
            {"t": tenant_id, "e": EVENT, "s": str(student_id)},
        )
        return [dict(r[0]) for r in rows]


def _new(world: Any) -> uuid.UUID:
    sid: uuid.UUID = SW.create(world.a, name="Synthetica Outbox Student", section_key="section_9a")
    return sid


def test_FR_DQ_002_create_emits_one_event_with_keys_and_enrolment(
    world: Any, admin_engine: Engine
) -> None:
    sid = _new(world)
    (event,) = _events(admin_engine, world.a.tenant_id, sid)
    assert event["student_ids"] == [str(sid)]
    assert set(event["attribute_keys"]) == {
        "full_name",
        "dob",
        "gender",
        "father_name",
        "mother_name",
        "enrollment",
    }


def test_FR_DQ_002_value_writes_emit_and_identical_resends_do_not(
    world: Any, admin_engine: Engine
) -> None:
    sid = _new(world)
    ctx = SW.admin_ctx(world.a)
    with tenant_session(world.a.tenant_id, ctx.user_id) as db:
        students.record_value(db, ctx, sid, "mother_tongue", "parent_form", "Telugu")
        students.record_value(db, ctx, sid, "full_name", "udise_plus", "Synthetica Outbox")
    with tenant_session(world.a.tenant_id, ctx.user_id) as db:
        students.record_value(db, ctx, sid, "mother_tongue", "parent_form", "Telugu")  # no-op
    events = _events(admin_engine, world.a.tenant_id, sid)
    assert len(events) == 2
    assert events[1] == {
        "student_ids": [str(sid)],
        "attribute_keys": ["full_name", "mother_tongue"],
    }


def test_FR_DQ_002_import_batch_values_and_rollbacks_emit_nothing(
    world: Any, admin_engine: Engine
) -> None:
    sid = _new(world)
    ctx = SW.admin_ctx(world.a)
    with tenant_session(world.a.tenant_id, ctx.user_id) as db:
        students.record_value(
            db, ctx, sid, "mother_tongue", "udise_plus", "Hindi", import_batch_id=uuid.uuid4()
        )

    def failing() -> None:
        with tenant_session(world.a.tenant_id, ctx.user_id) as db:
            students.record_value(db, ctx, sid, "nationality", "parent_form", "Indian")
            raise RuntimeError("synthetic failure")

    with pytest.raises(RuntimeError):
        failing()
    assert len(_events(admin_engine, world.a.tenant_id, sid)) == 1  # the create only


def test_FR_DQ_002_enrolment_and_many_students_are_coalesced(
    world: Any, admin_engine: Engine
) -> None:
    ctx = SW.admin_ctx(world.a)
    with tenant_session(world.a.tenant_id, ctx.user_id) as db:
        ids = [
            students.create_student(
                db,
                ctx,
                StudentCreate(
                    values=[
                        ValueIn(
                            attribute_key="full_name",
                            source="admission_register",
                            value=f"Synthetica Bulk {i}",
                        )
                    ]
                ),
            ).id
            for i in range(105)
        ]
        students.enrol(db, ctx, ids[0], EnrollmentIn(section_id=world.a.ids["section_9c"]))
    with admin_engine.connect() as c:
        payloads = [
            dict(r[0])
            for r in c.execute(
                text(
                    "SELECT payload FROM ops.outbox WHERE tenant_id = :t AND event_type = :e "
                    "AND payload -> 'student_ids' ?| :ids"
                ),
                {"t": world.a.tenant_id, "e": EVENT, "ids": [str(i) for i in ids]},
            )
        ]
    assert sorted(len(p["student_ids"]) for p in payloads) == [5, 100]
    assert {s for p in payloads for s in p["student_ids"]} == {str(i) for i in ids}
    assert all(
        "enrollment" in p["attribute_keys"] for p in payloads if str(ids[0]) in p["student_ids"]
    )
