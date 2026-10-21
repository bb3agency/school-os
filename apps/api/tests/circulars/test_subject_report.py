"""Data-principal access report contribution of circulars and tasks (FR-DPDP-009, PRV-031).

Synthetic school A (tests/api/world.py).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine

from app.circulars import service, subjects
from app.core.db import tenant_session
from app.core.subjects import Subject

pytestmark = pytest.mark.db
C = sys.modules["sos_test_circulars_support"]

_YAML = Path(service.__file__).resolve().parents[1] / "privacy" / "subjects.yaml"


def _classes() -> set[str]:
    data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    return set(data["contributors"]["circulars"]["classes"])


def _report(school: Any, subject: Subject) -> list[Any]:
    with tenant_session(school.tenant_id) as s:
        return subjects.subject_report(s, subject)


def test_FR_DPDP_009_circulars_report_lists_the_members_tasks_only(
    world: Any, admin_engine: Engine
) -> None:
    a = world.a
    mine = C.W.add_member(admin_engine, a.tenant_id, ["teacher"])
    other = C.W.add_member(admin_engine, a.tenant_id, ["teacher"])
    C.task(a, owner=mine, title="Collect the synthetic consent slips")
    C.task(a, owner=mine, title="Check the synthetic register")
    C.task(a, owner=other, title="Someone else's synthetic task")

    sections = _report(a, Subject("member", mine.membership_id))

    assert len(sections) == 1
    sec = sections[0]
    assert sec.class_key in _classes()
    titles = {row[0] for row in sec.rows}
    assert titles == {"Collect the synthetic consent slips", "Check the synthetic register"}
    assert all(row[4] == "Open" for row in sec.rows)
    assert sec.note is None


def test_FR_DPDP_009_circulars_report_empty_for_unrelated_or_other_kinds(
    world: Any, admin_engine: Engine
) -> None:
    a = world.a
    idle = C.W.add_member(admin_engine, a.tenant_id, ["teacher"])
    assert _report(a, Subject("member", idle.membership_id)) == []
    assert _report(a, Subject("student", uuid.uuid4())) == []
    assert _report(a, Subject("guardian", uuid.uuid4())) == []


def test_FR_DPDP_008_circulars_is_not_an_eraser() -> None:
    assert not hasattr(service, "erase_for_subject")


def test_FR_DPDP_008_circulars_service_exposes_subject_report() -> None:
    assert service.subject_report is subjects.subject_report
