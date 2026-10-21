"""Data-principal access report contribution of exports (FR-DPDP-009, PRV-031).

Synthetic school (tests/exports/objects.py); exports run synchronously with the fake renderer.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.core.subjects import Subject
from app.exports import subjects

pytestmark = pytest.mark.db
EX = sys.modules["sos_test_exports_objects"]

_YAML = Path(subjects.__file__).resolve().parents[1] / "privacy" / "subjects.yaml"


def _classes() -> set[str]:
    data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    return set(data["contributors"]["exports"]["classes"])


def _report(school: Any, subject: Subject) -> list[Any]:
    with tenant_session(school.tenant_id) as s:
        return subjects.subject_report(s, subject)


def test_FR_DPDP_009_exports_report_for_student_and_requesting_member(
    school: Any, admin_engine: Engine
) -> None:
    requester = EX.W.add_member(admin_engine, school.tenant_id, ["principal"])
    included = EX.student(school, section_key="section_9c")
    export_id = EX.ready_export(school, "principal", requester, section_keys=("section_9c",))
    later = EX.student(school, section_key="section_9c")  # made after the export

    student_sections = _report(school, Subject("student", included))
    assert len(student_sections) == 1
    sec = student_sections[0]
    assert sec.class_key in _classes()
    assert len(sec.rows) == 1
    assert sec.rows[0][2] == "1"
    assert _report(school, Subject("student", later)) == []  # another student's exports

    member_sections = _report(school, Subject("member", requester.membership_id))
    assert len(member_sections) == 1
    msec = member_sections[0]
    assert msec.class_key in _classes()
    assert len(msec.rows) == 1
    assert msec.rows[0][5] == "Ready"
    dumped = repr(student_sections + member_sections)
    assert str(export_id) not in dumped
    assert str(included) not in dumped
    assert "exports/" not in dumped  # never object keys
    assert "http" not in dumped  # never URLs
    assert "Synthetica" not in dumped  # never students' names


def test_FR_DPDP_009_exports_report_empty_for_unrelated_or_other_kinds(
    school: Any, admin_engine: Engine
) -> None:
    idle = EX.W.add_member(admin_engine, school.tenant_id, ["principal"])
    assert _report(school, Subject("member", idle.membership_id)) == []
    assert _report(school, Subject("student", uuid.uuid4())) == []
    assert _report(school, Subject("guardian", uuid.uuid4())) == []


def test_FR_DPDP_008_exports_is_not_an_eraser() -> None:
    assert not hasattr(subjects, "erase_for_subject")
