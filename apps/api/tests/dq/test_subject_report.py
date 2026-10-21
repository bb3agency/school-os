"""Data-quality checks in the data principal's access report (R3-1A; FR-DPDP-009, PRV-031;
ADR-0048 decision 6). Counts only. Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.core.subjects import ReportSection, Subject
from app.dq import service as dq

pytestmark = pytest.mark.db
DS = sys.modules["sos_test_dq_support"]
_REGISTRY = Path(dq.__file__).resolve().parents[1] / "privacy" / "subjects.yaml"


@pytest.fixture(autouse=True)
def _keys(keyring: None) -> None:
    return None


def _classes() -> set[str]:
    data = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8"))
    return set(data["contributors"]["dq"]["classes"])


def _report(school: Any, subject: Subject) -> list[ReportSection]:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        return dq.subject_report(db, subject)


def test_FR_DPDP_009_dq_report_counts_the_students_findings_only(
    world: Any, admin_engine: Engine
) -> None:
    sid = DS.conflict_student(world.a)
    other = DS.conflict_student(world.a)
    DS.run(world.a, sid, other)
    mine = DS.findings(admin_engine, sid)
    assert any(f["rule_id"] == "DQ-003" for f in mine)

    sections = _report(world.a, Subject("student", sid))
    (section,) = sections
    assert {s.class_key for s in sections} <= _classes()
    assert section.class_key == "data_quality"
    assert section.columns == ("Check", "Severity", "Status", "Findings", "Last checked")
    assert ("DQ-003", "High", "Open") in {r[:3] for r in section.rows}
    assert sum(int(r[3]) for r in section.rows) == len(mine)
    active = sum(1 for f in mine if f["status"] in ("open", "reopened", "needs_confirmation"))
    assert section.note is not None
    assert section.note.startswith(f"Open findings: {active}, closed: {len(mine) - active}.")
    text = repr(sections)
    assert str(other) not in text
    assert "Kommineni" not in text  # no names or masked values, counts only


def test_FR_DPDP_009_dq_report_is_empty_for_unknown_or_other_kinds(world: Any) -> None:
    sid = DS.conflict_student(world.a)
    DS.run(world.a, sid)
    assert _report(world.a, Subject("student", uuid.uuid4())) == []
    assert _report(world.a, Subject("guardian", sid)) == []
    assert _report(world.a, Subject("member", world.person("office_admin").membership_id)) == []


def test_FR_DPDP_008_dq_is_not_an_eraser() -> None:
    assert not hasattr(dq, "erase_for_subject")
