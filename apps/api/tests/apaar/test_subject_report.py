"""APAAR consent register in the data principal's access report (R3-1A; FR-DPDP-009, PRV-031;
ADR-0048 decision 6). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine

from app.apaar import service as apaar
from app.core.db import tenant_session
from app.core.subjects import ReportSection, Subject

pytestmark = pytest.mark.db
A = sys.modules["sos_test_apaar_support"]
_REGISTRY = Path(apaar.__file__).resolve().parents[1] / "privacy" / "subjects.yaml"
NOTE = "Synthetic office note about the call"


def _classes() -> set[str]:
    data = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8"))
    return set(data["contributors"]["apaar"]["classes"])


def _report(school: Any, subject: Subject) -> list[ReportSection]:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        return apaar.subject_report(db, subject)


def test_FR_DPDP_009_apaar_report_lists_the_students_decisions_newest_first(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    staff = world.person("office_staff")
    sid = A.student(world.a)
    other = A.student(world.a)
    assert A.record(api, staff, sid, A.refused(note=NOTE)).status_code == 201
    doc = A.signed_form(admin_engine, world.a, staff)
    assert A.record(api, staff, sid, A.given(doc)).status_code == 201
    assert A.record(api, staff, other, A.refused()).status_code == 201

    sections = _report(world.a, Subject("student", sid))
    (section,) = sections
    assert section.class_key == "apaar_consents"
    assert {s.class_key for s in sections} <= _classes()
    assert section.columns[0] == "Decision"
    assert [r[0] for r in section.rows] == ["Given", "Refused"]
    assert section.rows[0][1] == "15/07/2026"
    assert section.rows[0][2] == "Mother"
    assert section.rows[0][3] == "Telugu"
    assert section.rows[1][2] == "Father"
    assert section.note is None
    text = repr(sections)
    assert NOTE not in text
    assert str(doc) not in text

    (other_section,) = _report(world.a, Subject("student", other))
    assert len(other_section.rows) == 1


def test_FR_DPDP_009_apaar_report_is_empty_for_unknown_or_other_kinds(world: Any) -> None:
    sid = A.student(world.a)
    assert _report(world.a, Subject("student", sid)) == []
    assert _report(world.a, Subject("student", uuid.uuid4())) == []
    assert _report(world.a, Subject("guardian", sid)) == []
    assert _report(world.a, Subject("member", world.person("office_staff").membership_id)) == []


def test_FR_DPDP_008_apaar_is_not_an_eraser() -> None:
    assert not hasattr(apaar, "erase_for_subject")
