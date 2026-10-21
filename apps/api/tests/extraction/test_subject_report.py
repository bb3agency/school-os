"""Data-principal access report contribution of register-photo extraction (FR-DPDP-009,
PRV-031). Synthetic school A (tests/api/world.py); the fake provider runs synchronously."""

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
from app.extraction import service, subjects
from app.extraction.schemas import ItemConfirm

pytestmark = pytest.mark.db
X = sys.modules["sos_test_extraction_support"]

_YAML = Path(service.__file__).resolve().parents[1] / "privacy" / "subjects.yaml"


def _classes() -> set[str]:
    data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    return set(data["contributors"]["extraction"]["classes"])


def _report(school: Any, subject: Subject) -> list[Any]:
    with tenant_session(school.tenant_id) as s:
        return subjects.subject_report(s, subject)


def _confirmed_students(admin: Engine, world: Any, names: list[str]) -> list[uuid.UUID]:
    """One processed batch with a row per name, each confirmed as a new student."""
    a = world.a
    _, items = X.processed_batch(admin, a, [X.page_png([X.register_row(n) for n in names])])
    person = a.people["office_admin"]
    out: list[uuid.UUID] = []
    for item_id in items:
        fields = X.row_of(admin, "sis.extraction_items", item_id)["fields"]
        name = fields["full_name"]["value"]
        with tenant_session(a.tenant_id, person.user_id) as s:
            service.confirm_item(s, X.ctx(a), item_id, ItemConfirm(fields={"full_name": name}))
        student = X.row_of(admin, "sis.extraction_items", item_id)["student_id"]
        out.append(uuid.UUID(str(student)))
    return out


def test_FR_DPDP_009_extraction_report_counts_rows_without_images_or_text(
    world: Any, admin_engine: Engine
) -> None:
    tag = uuid.uuid4().hex[:6]
    first, second = _confirmed_students(
        admin_engine, world, [f"Synthetica Photo One {tag}", f"Synthetica Photo Two {tag}"]
    )

    sections = _report(world.a, Subject("student", first))

    assert len(sections) == 1
    sec = sections[0]
    assert sec.class_key in _classes()
    assert len(sec.rows) == 1
    assert sec.rows[0][1] == "Admission register"
    assert sec.rows[0][3] == "1"  # pages
    assert sec.rows[0][4] == "1"  # only this student's row, not the second student's
    dumped = repr(sections)
    assert tag not in dumped  # never what was read from the photo
    assert "t/" not in dumped  # never object keys
    assert "http" not in dumped  # never URLs
    assert len(_report(world.a, Subject("student", second))[0].rows) == 1


def test_FR_DPDP_009_extraction_report_empty_for_unrelated_or_other_kinds(world: Any) -> None:
    assert _report(world.a, Subject("student", uuid.uuid4())) == []
    member = world.a.people["office_admin"].membership_id
    assert _report(world.a, Subject("member", member)) == []
    assert _report(world.a, Subject("guardian", uuid.uuid4())) == []


def test_FR_DPDP_008_extraction_is_not_an_eraser() -> None:
    assert not hasattr(service, "erase_for_subject")


def test_FR_DPDP_008_extraction_service_exposes_subject_report() -> None:
    assert service.subject_report is subjects.subject_report
