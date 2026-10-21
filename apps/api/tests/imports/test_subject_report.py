"""Data-principal access report contribution of the imports module (FR-DPDP-009, PRV-031).

Synthetic school A (tests/api/world.py); the import pipeline runs synchronously.
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
from app.imports import service, subjects

pytestmark = pytest.mark.db
S = sys.modules["sos_test_imports_support"]

_YAML = Path(service.__file__).resolve().parents[1] / "privacy" / "subjects.yaml"


def _classes() -> set[str]:
    data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    return set(data["contributors"]["imports"]["classes"])


def _report(school: Any, subject: Subject) -> list[Any]:
    with tenant_session(school.tenant_id) as s:
        return subjects.subject_report(s, subject)


def test_FR_DPDP_009_imports_report_lists_batches_without_cell_values(
    world: Any, admin_engine: Engine
) -> None:
    rows, numbers = S.class_list(2, prefix="SRA")
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    first = S.student_by_adm(admin_engine, world.a.tenant_id, numbers[0])
    assert first is not None

    sections = _report(world.a, Subject("student", first))

    assert len(sections) == 1
    sec = sections[0]
    assert sec.class_key in _classes()
    assert len(sec.rows) == 1
    row = sec.rows[0]
    assert row[1] == "Admission register"
    assert row[2] == "Committed"
    assert row[-1] == "1"
    text_ = repr(sections)
    assert "Synthetica Import" not in text_  # never raw cell values
    assert numbers[0] not in text_
    assert str(batch_id) not in text_
    assert sec.note is None


def test_FR_DPDP_009_imports_report_excludes_other_students_batches(
    world: Any, admin_engine: Engine
) -> None:
    rows_a, numbers_a = S.class_list(1, prefix="SRB")
    rows_b, numbers_b = S.class_list(1, prefix="SRC")
    S.imported(admin_engine, world.a, S.xlsx_bytes(rows_a))
    S.imported(admin_engine, world.a, S.xlsx_bytes(rows_b))
    student_a = S.student_by_adm(admin_engine, world.a.tenant_id, numbers_a[0])
    student_b = S.student_by_adm(admin_engine, world.a.tenant_id, numbers_b[0])
    assert student_a is not None
    assert student_b is not None

    for student in (student_a, student_b):
        sections = _report(world.a, Subject("student", student))
        assert len(sections) == 1
        assert len(sections[0].rows) == 1  # only the batch that named this student


def test_FR_DPDP_009_imports_report_empty_for_unrelated_or_other_kinds(world: Any) -> None:
    assert _report(world.a, Subject("student", uuid.uuid4())) == []
    member = world.a.people["office_admin"].membership_id
    assert _report(world.a, Subject("member", member)) == []
    assert _report(world.a, Subject("guardian", uuid.uuid4())) == []


def test_FR_DPDP_008_imports_is_not_an_eraser() -> None:
    assert not hasattr(service, "erase_for_subject")


def test_FR_DPDP_008_imports_service_exposes_subject_report() -> None:
    assert service.subject_report is subjects.subject_report
