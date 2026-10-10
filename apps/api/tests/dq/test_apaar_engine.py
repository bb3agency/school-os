"""APAAR rules on the real database (ADR-0037; FR-DQ-021, FR-DQ-022, DQ-009 v2, PRV-020).

Synthetic data only: APAAR-like IDs from :func:`app.devtools.fake_ids.synthetic_apaar_id`.
"""

from __future__ import annotations

import datetime as dt
import random
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.errors import NotFound
from app.devtools.fake_ids import synthetic_apaar_id
from app.dq import service as dq
from app.dq.schemas import FindingFilters
from app.students import service as students
from app.students.schemas import ValueIn

pytestmark = pytest.mark.db
DS = sys.modules["sos_test_dq_support"]
RNG = random.Random(uuid.uuid4().int)


@pytest.fixture(autouse=True)
def _keys(keyring: None) -> None:
    return None


def apaar(value: str, source: str = "udise_plus") -> ValueIn:
    return ValueIn(attribute_key="apaar_id", source=source, value=value)


def dumped(admin: Engine, *student_ids: uuid.UUID) -> str:
    with admin.connect() as c:
        out: str = c.execute(
            text(
                "SELECT coalesce(string_agg(details::text || explanation_params::text, ' '), '') "
                "FROM sis.dq_findings WHERE student_id = ANY(:s)"
            ),
            {"s": list(student_ids)},
        ).scalar_one()
    return out


def _set_status(admin: Engine, student_id: uuid.UUID, status: str) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE sis.students SET status = :st WHERE id = :i"),
            {"st": status, "i": student_id},
        )


def test_FR_DQ_021_one_apaar_id_on_two_students_across_sections(
    world: Any, admin_engine: Engine
) -> None:
    shared = synthetic_apaar_id(RNG)
    in_9a = DS.student(world.a, extra=[apaar(shared)], admission_no=f"A{uuid.uuid4().hex[:8]}")
    # FR-STU-018 refuses a second live record with the same APAAR ID, so the pair is made the
    # one way it still arises: a former student re-admitted after the number was reused.
    _set_status(admin_engine, in_9a, "left")
    in_9c = DS.student(
        world.a,
        section_key="section_9c",
        extra=[apaar(shared, "parent_form")],
        admission_no=f"A{uuid.uuid4().hex[:8]}",
    )
    _set_status(admin_engine, in_9a, "active")
    DS.run(world.a, in_9a)
    DS.run(world.a, in_9c)
    mine, theirs = DS.one(admin_engine, in_9a, "DQ-021"), DS.one(admin_engine, in_9c, "DQ-021")
    assert (mine["severity"], mine["status"]) == ("blocker", "open")
    assert mine["details"]["other_student_id"] == str(in_9c)
    assert theirs["details"]["other_student_id"] == str(in_9a)
    assert mine["related_student_id"] is None
    assert mine["explanation_code"] == "DQ-021-DUPLICATE"
    assert mine["route_codes"] == ["ROUTE-APAAR"]
    assert shared not in dumped(admin_engine, in_9a, in_9c), "the APAAR ID is never stored"
    admin_view = DS.call(world.a, dq.get_finding, mine["id"])
    assert admin_view.explanation.en.startswith("Same APAAR ID as A")
    assert shared not in admin_view.model_dump_json()
    teacher = DS.ctx(world.a, "class_teacher")
    (seen,) = DS.call(
        world.a,
        dq.list_findings,
        FindingFilters(rule_id=["DQ-021"], student_id=in_9a),
        as_ctx=teacher,
    ).data
    assert seen.explanation.en == "Same APAAR ID as ••••. One of them is wrong."
    with pytest.raises(NotFound):
        DS.call(world.a, dq.get_finding, theirs["id"], as_ctx=teacher)

    # The office records the right ID for one of them: both findings clear when checked.
    DS.call(
        world.a, students.record_value, in_9c, "apaar_id", "parent_form", synthetic_apaar_id(RNG)
    )
    DS.run(world.a, in_9a, in_9c)
    assert DS.one(admin_engine, in_9a, "DQ-021")["status"] == "resolved"
    assert DS.one(admin_engine, in_9c, "DQ-021")["status"] == "resolved"


def test_DQ_009_and_FR_DQ_022_clear_once_the_apaar_id_is_verified(
    world: Any, admin_engine: Engine
) -> None:
    sid = DS.student(
        world.a,
        extra=[
            *DS.aadhaar(name="Yarlagadda Durga Bhavani", dob="2012-03-14"),
            ValueIn(attribute_key="full_name", source="udise_plus", value="Kommineni Venkata Sai"),
        ],
    )
    DS.run(world.a, sid, profile_key="udise-plus")
    assert DS.one(admin_engine, sid, "DQ-009")["status"] == "open"
    dq22 = DS.one(admin_engine, sid, "DQ-022")
    assert (dq22["status"], dq22["severity"], dq22["attribute_key"]) == (
        "open",
        "high",
        "full_name",
    )
    assert dq22["sources"] == ["aadhaar_as_printed", "udise_plus"]

    # An unverified APAAR ID does not count yet.
    number = synthetic_apaar_id(RNG)
    DS.call(world.a, students.record_value, sid, "apaar_id", "udise_plus", number)
    DS.run(world.a, sid, profile_key="udise-plus")
    assert DS.one(admin_engine, sid, "DQ-009")["status"] == "open"
    assert DS.one(admin_engine, sid, "DQ-022")["status"] == "open"

    # Verified: readiness no longer matters, both findings clear.
    DS.call(
        world.a,
        students.record_value,
        sid,
        "apaar_id",
        "parent_form",
        number,
        verification="verified",
    )
    DS.run(world.a, sid, profile_key="udise-plus")
    for rule in ("DQ-009", "DQ-022"):
        row = DS.one(admin_engine, sid, rule)
        assert (row["status"], row["resolution"]) == ("resolved", "auto_cleared"), rule
    assert number not in dumped(admin_engine, sid)


def test_FR_APC_006_refused_consent_is_never_pushed_to_apaar(
    world: Any, admin_engine: Engine
) -> None:
    """ADR-0039: once the parents refused APAAR consent, the APAAR readiness rules (DQ-009,
    DQ-022) leave the student alone; if they later give consent, the rules apply again."""
    from app.apaar import service as apaar_service
    from app.apaar.schemas import ConsentIn

    sid = DS.student(
        world.a,
        extra=[
            *DS.aadhaar(name="Yarlagadda Durga Bhavani", dob="2012-03-14"),
            ValueIn(attribute_key="full_name", source="udise_plus", value="Kommineni Venkata Sai"),
        ],
    )
    DS.run(world.a, sid, profile_key="udise-plus")
    assert DS.one(admin_engine, sid, "DQ-009")["status"] == "open"
    assert DS.one(admin_engine, sid, "DQ-022")["status"] == "open"
    refused = ConsentIn(status="refused", relationship="father", decided_on=dt.date(2026, 7, 1))
    DS.call(world.a, apaar_service.record_consent, sid, refused)
    DS.run(world.a, sid, profile_key="udise-plus")
    for rule in ("DQ-009", "DQ-022"):
        row = DS.one(admin_engine, sid, rule)
        assert (row["status"], row["resolution"]) == ("resolved", "auto_cleared"), rule


def test_FR_DQ_021_other_school_apaar_ids_never_pair(world: Any, admin_engine: Engine) -> None:
    """Tenant isolation: the duplicate check only sees the school's own students."""
    shared = synthetic_apaar_id(RNG)
    mine = DS.student(world.a, extra=[apaar(shared)])
    DS.student(world.b, extra=[apaar(shared)])
    DS.run(world.a, mine)
    assert DS.findings(admin_engine, mine, "DQ-021") == []
