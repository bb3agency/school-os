"""Change requests in the data principal's access report (R3-1A; FR-DPDP-009, PRV-031;
ADR-0048 decision 6). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine

from app.changes import service as changes
from app.changes.schemas import RejectIn
from app.core.db import tenant_session
from app.core.subjects import ReportSection, Subject
from app.students import service as students

pytestmark = pytest.mark.db
CR = sys.modules["sos_test_changes_objects"]
_REGISTRY = Path(changes.__file__).resolve().parents[1] / "privacy" / "subjects.yaml"
DECISION = "Synthetic reviewer note about the certificate"
PLACE_OLD = "Synthetic Ongole Hamlet"
PLACE_NEW = "Synthetic Chirala Town"


def _classes() -> set[str]:
    data = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8"))
    return set(data["contributors"]["changes"]["classes"])


def _report(school: Any, subject: Subject) -> list[ReportSection]:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        return changes.subject_report(db, subject)


def _reject(school: Any, request_id: uuid.UUID) -> None:
    principal = school.people["principal"]
    with tenant_session(school.tenant_id, principal.user_id) as s:
        changes.reject(
            s,
            CR.ctx(school, principal, "principal"),
            request_id,
            RejectIn(reason=DECISION),
            expected_version=1,
        )


def test_FR_DPDP_009_changes_report_lists_the_students_requests_without_free_text(
    school: Any, admin_engine: Engine, c3_identity: str
) -> None:
    clerk = school.people["office_admin"]
    sid = CR.student(school)
    other = CR.student(school)
    owner = school.people["owner"]
    with tenant_session(school.tenant_id, owner.user_id) as s:
        students.record_value(
            s,
            CR.ctx(school, owner, "office_admin"),
            sid,
            c3_identity,
            "admission_register",
            PLACE_OLD,
        )
    dob = CR.submit(admin_engine, school, clerk, "office_admin", student_id=sid)
    _reject(school, dob.id)
    CR.submit(
        admin_engine,
        school,
        clerk,
        "office_admin",
        student_id=sid,
        attribute_key=c3_identity,
        new_value=PLACE_NEW,
    )
    CR.submit(admin_engine, school, clerk, "office_admin", student_id=other, new_value="2012-04-01")

    sections = _report(school, Subject("student", sid))
    (section,) = sections
    assert {s.class_key for s in sections} <= _classes()
    assert section.class_key == "change_requests"
    assert section.columns == (
        "Detail",
        "Status",
        "Value before",
        "Value asked for",
        "Requested",
        "Decided",
    )
    newest, oldest = section.rows
    assert newest[:4] == ("Place of birth", "Pending", PLACE_OLD, PLACE_NEW)  # C3 in clear
    assert newest[5] == "—"
    assert oldest[:4] == ("Date of birth", "Rejected", "14/03/2012", "15/03/2012")
    assert oldest[5] != "—"
    text = repr(sections)
    assert DECISION not in text
    assert CR.REASON not in text
    assert "01/04/2012" not in text  # the other student's request
    assert str(dob.evidence_document_id) not in text


def test_FR_DPDP_009_changes_report_for_a_guardian_shows_only_their_own_name(
    school: Any, admin_engine: Engine
) -> None:
    clerk = school.people["office_admin"]
    sid = CR.student(school)
    father = CR.SW.add_guardian(school, sid, full_name="Synthetica Father Rao")
    CR.submit(
        admin_engine,
        school,
        clerk,
        "office_admin",
        student_id=sid,
        attribute_key="father_name",
        new_value="Synthetica Father Rao",
    )
    CR.submit(
        admin_engine,
        school,
        clerk,
        "office_admin",
        student_id=sid,
        attribute_key="mother_name",
        new_value="Synthetica Mother Devi",
    )
    CR.submit(admin_engine, school, clerk, "office_admin", student_id=sid)

    (section,) = _report(school, Subject("guardian", father))
    assert section.class_key == "change_requests"
    assert [r[0] for r in section.rows] == ["Father's name"]
    assert section.rows[0][3] == "Synthetica Father Rao"
    assert "Synthetica Mother Devi" not in repr(section)


def test_FR_DPDP_009_changes_report_is_empty_for_unknown_or_other_kinds(school: Any) -> None:
    sid = CR.student(school)
    assert _report(school, Subject("student", sid)) == []
    assert _report(school, Subject("student", uuid.uuid4())) == []
    assert _report(school, Subject("guardian", uuid.uuid4())) == []
    assert _report(school, Subject("member", school.people["owner"].membership_id)) == []


def test_FR_DPDP_008_changes_is_not_an_eraser() -> None:
    assert not hasattr(changes, "erase_for_subject")
