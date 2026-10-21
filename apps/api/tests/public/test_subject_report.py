"""Data-principal access report: the public handles contribution (R3-1A; FR-DPDP-009,
ADR-0048 decision 6, PRV-031). Synthetic data only.

The student's fee accounts, receipts and certificates come from the fees and certificates
services (covered by their own tests); here they are replaced by fixed synthetic ids."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.certificates import codes
from app.certificates import service as certificates
from app.core.db import tenant_session
from app.core.subjects import ReportSection, Subject
from app.fees import service as fees
from app.public import service

pytestmark = pytest.mark.db
P = sys.modules["sos_test_public_support"]


def _classes() -> set[str]:
    path = Path(service.__file__).parents[1] / "privacy" / "subjects.yaml"
    raw = yaml.safe_load(path.read_text("utf-8"))
    return set(raw["contributors"]["public"]["classes"])


def _report(school: Any, subject: Subject) -> list[ReportSection]:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as s:
        return service.subject_report(s, subject)


def _flat(sections: list[ReportSection]) -> str:
    return " ".join(" ".join(" ".join(r) for r in s.rows) for s in sections)


def _revoke(school: Any, handle_id: uuid.UUID) -> None:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as s:
        assert service.revoke(s, handle_id, reason="withdrawn")


def test_FR_DPDP_009_guardian_report_lists_their_parent_links_only(world: Any) -> None:
    school = world.a
    _, guardian = P.student_with_guardian(school)
    _, other_guardian = P.student_with_guardian(school)
    notice = P.issue(school, "parent_notice", guardian_id=guardian)
    receipt = P.issue(school, "parent_receipt", guardian_id=guardian)
    _revoke(school, receipt.handle_id)
    other = P.issue(school, "parent_dues", guardian_id=other_guardian)

    sections = _report(school, Subject("guardian", guardian))

    assert [s.class_key for s in sections] == ["public_links"]
    assert {s.class_key for s in sections} <= _classes()
    rows = sections[0].rows
    assert sorted((r[0], r[3]) for r in rows) == [
        ("Fee receipt link", "Withdrawn"),
        ("Notice link", "Active"),
    ]
    flat = _flat(sections)
    assert "Fee dues link" not in flat
    for issued in (notice, receipt, other):
        assert str(issued.handle_id) not in flat
        assert issued.key is not None
        assert issued.key not in flat


def test_FR_DPDP_009_student_report_has_fee_links_and_code_counts(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    school = world.a
    _, guardian = P.student_with_guardian(school)
    student, _ = P.student_with_guardian(school)
    account, receipt, other_account = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    certificate, other_certificate = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(
        fees,
        "fee_targets_of_student",
        lambda _s, sid: ([account], [receipt]) if sid == student else ([], []),
    )
    monkeypatch.setattr(
        certificates,
        "certificate_ids_of_student",
        lambda _s, sid: [certificate] if sid == student else [],
    )
    dues = P.issue(school, "parent_dues", guardian_id=guardian, target_id=account)
    P.issue(school, "parent_receipt", guardian_id=guardian, target_id=receipt)
    P.issue(school, "parent_dues", guardian_id=guardian, target_id=other_account)
    code = codes.new_code()
    P.issue(school, "cert_verify", target_id=certificate, key=code, target_type="certificate")
    old = P.issue(
        school,
        "cert_verify",
        target_id=certificate,
        key=codes.new_code(),
        target_type="certificate",
    )
    _revoke(school, old.handle_id)
    P.issue(
        school,
        "cert_verify",
        target_id=other_certificate,
        key=codes.new_code(),
        target_type="certificate",
    )

    sections = _report(school, Subject("student", student))

    assert {s.class_key for s in sections} == {"public_links"}
    links, check_codes = sections
    assert sorted(r[0] for r in links.rows) == ["Fee dues link", "Fee receipt link"]
    assert check_codes.rows == (("1", "1"),)
    flat = _flat(sections)
    assert code not in flat
    assert dues.key is not None
    assert dues.key not in flat


def test_FR_DPDP_009_public_report_empty_for_unrelated_or_members(world: Any) -> None:
    school = world.a
    student, guardian = P.student_with_guardian(school)
    P.issue(school, "parent_notice", guardian_id=guardian)
    assert _report(school, Subject("guardian", uuid.uuid4())) == []
    assert _report(school, Subject("student", student)) == []
    assert _report(school, Subject("member", guardian)) == []


def test_FR_DPDP_012_public_is_not_an_eraser() -> None:
    assert not hasattr(service, "erase_for_subject")
