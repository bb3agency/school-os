"""Data-principal access report: the Tally connector contribution (R3-1A; FR-DPDP-009,
ADR-0048 decision 6, PRV-031). Synthetic data only."""

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
from app.tally import service as tally

pytestmark = pytest.mark.db
T = sys.modules["sos_test_tally_support"]


def _classes() -> set[str]:
    path = Path(tally.__file__).parents[1] / "privacy" / "subjects.yaml"
    raw = yaml.safe_load(path.read_text("utf-8"))
    return set(raw["contributors"]["tally"]["classes"])


def _report(school: Any, subject: Subject) -> list[ReportSection]:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as s:
        return tally.subject_report(s, subject)


@pytest.fixture(scope="module")
def setup(world: Any, admin_engine: Engine) -> dict[str, Any]:
    school = T.fresh_school(admin_engine)
    owner = school.people["owner"].user_id
    ravi = T.SW.create(
        school, name="Synthetica Ravi Kumar", section_key="section_9a", admission_no="TS-1"
    )
    sita = T.SW.create(
        school, name="Synthetica Sita Devi", section_key="section_9c", admission_no="TS-2"
    )
    mine = T.seed_party(admin_engine, school.tenant_id, owner, ledger="Synthetic Ravi Ledger")
    theirs = T.seed_party(admin_engine, school.tenant_id, owner, ledger="Synthetic Sita Ledger")
    T.seed_party(admin_engine, school.tenant_id, owner, ledger="Synthetic Unlinked Ledger")
    T.seed_link(admin_engine, school.tenant_id, mine, ravi, owner)
    T.seed_link(admin_engine, school.tenant_id, theirs, sita, owner)
    return {"school": school, "ravi": ravi, "sita": sita}


def test_FR_DPDP_009_tally_report_lists_linked_ledgers_only(setup: dict[str, Any]) -> None:
    sections = _report(setup["school"], Subject("student", setup["ravi"]))

    assert len(sections) == 1
    section = sections[0]
    assert section.class_key == "tally_ledgers"
    assert {s.class_key for s in sections} <= _classes()
    assert section.rows == (
        (
            "Synthetic Ravi Ledger",
            "Sundry Debtors",
            "₹900.00",
            "28/09/2026",
            section.rows[0][4],
            "Yes",
        ),
    )
    flat = " ".join(" ".join(r) for r in section.rows)
    assert "Sita" not in flat
    assert "Unlinked" not in flat


def test_FR_DPDP_009_tally_report_empty_for_unrelated_or_other_kinds(
    setup: dict[str, Any],
) -> None:
    school = setup["school"]
    assert _report(school, Subject("student", uuid.uuid4())) == []
    assert _report(school, Subject("guardian", setup["ravi"])) == []
    assert _report(school, Subject("member", setup["ravi"])) == []


def test_FR_DPDP_012_tally_is_not_an_eraser() -> None:
    assert not hasattr(tally, "erase_for_subject")
