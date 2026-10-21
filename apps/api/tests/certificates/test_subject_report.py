"""Data-principal access report: the certificates contribution (R3-1A; FR-DPDP-009,
ADR-0048 decision 6, PRV-031). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine, text

from app.certificates import service as certificates
from app.core.config import get_settings
from app.core.db import tenant_session
from app.core.subjects import ReportSection, Subject

pytestmark = pytest.mark.db
C = sys.modules["sos_test_certificates_support"]


def _classes() -> set[str]:
    path = Path(certificates.__file__).parents[1] / "privacy" / "subjects.yaml"
    raw = yaml.safe_load(path.read_text("utf-8"))
    return set(raw["contributors"]["certificates"]["classes"])


@pytest.fixture
def verify_on(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings().model_copy(
        update={"verify_base_url": "https://verify.synthetic.test"}
    )
    monkeypatch.setattr(certificates, "get_settings", lambda: settings)


def _report(school: Any, subject: Subject) -> list[ReportSection]:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as s:
        return certificates.subject_report(s, subject)


def _codes(admin: Engine, certificate_id: uuid.UUID) -> list[str]:
    with admin.connect() as c:
        return [
            str(r[0])
            for r in c.execute(
                text("SELECT code FROM sis.certificate_verifications WHERE certificate_id = :c"),
                {"c": certificate_id},
            )
        ]


def test_FR_DPDP_009_certificates_report_lists_the_students_certificates(
    school: Any, admin_engine: Engine, verify_on: None
) -> None:
    student = C.student(school)
    other = C.student(school)
    bonafide = C.issue(school, student, "bonafide")
    study = C.issue(school, student, "study")
    C.cancel(school, study)
    other_cert = C.issue(school, other, "conduct")
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE sis.certificate_verifications SET check_count = 3, last_checked_at = now() "
                "WHERE certificate_id = :c"
            ),
            {"c": bonafide.id},
        )

    sections = _report(school, Subject("student", student))

    assert len(sections) == 1
    section = sections[0]
    assert section.class_key in _classes()
    assert section.class_key == "certificates"
    assert len(section.rows) == 2
    by_serial = {r[1]: r for r in section.rows}
    assert bonafide.serial in by_serial
    assert by_serial[bonafide.serial][4] == "Issued"
    assert by_serial[bonafide.serial][6] == "3"
    assert study.serial in by_serial
    assert by_serial[study.serial][4] == "Cancelled"
    # Newest first.
    assert section.rows[0][1] == study.serial
    flat = " ".join(" ".join(r) for r in section.rows)
    assert other_cert.serial not in flat
    for code in [*_codes(admin_engine, bonafide.id), *_codes(admin_engine, other_cert.id)]:
        assert code not in flat


def test_FR_DPDP_009_certificates_report_empty_for_unrelated_or_other_kinds(
    school: Any,
) -> None:
    student = C.student(school)
    C.issue(school, student, "bonafide")
    assert _report(school, Subject("student", uuid.uuid4())) == []
    assert _report(school, Subject("guardian", student)) == []
    assert _report(school, Subject("member", student)) == []
    assert _report(school, Subject("student", C.student(school))) == []


def test_FR_DPDP_012_certificates_is_not_an_eraser() -> None:
    assert not hasattr(certificates, "erase_for_subject")
