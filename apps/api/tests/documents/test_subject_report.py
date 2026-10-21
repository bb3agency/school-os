"""Documents in the data principal's access report and erasure plan (FR-DPDP-009, FR-DPDP-013,
PRV-031, PRV-032; R3-1A). Synthetic schools only."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.subjects import ErasureOutcome, ReportSection, Subject
from app.documents import service

from .conftest import S, W

pytestmark = pytest.mark.db

SUBJECTS_YAML = Path(__file__).resolve().parents[2] / "app" / "privacy" / "subjects.yaml"
CLASSES = {"documents"}


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


SW = _load(
    "sos_test_student_world", Path(__file__).resolve().parents[1] / "students" / "student_world.py"
)


def _classes() -> set[str]:
    data = yaml.safe_load(SUBJECTS_YAML.read_text(encoding="utf-8"))
    return set(data["contributors"]["documents"]["classes"])


def _titled(admin: Engine, school: Any, title: str, uploader: Any) -> uuid.UUID:
    doc: uuid.UUID = S.make_document(
        admin,
        school.tenant_id,
        uploader.user_id,
        purpose="evidence",
        doc_type="form",
    )
    with admin.begin() as c:
        c.execute(text("UPDATE kb.documents SET title = :t WHERE id = :d"), {"t": title, "d": doc})
    return doc


def _link(
    admin: Engine, school: Any, student_id: uuid.UUID, document_id: uuid.UUID, seq: int
) -> None:
    """Tie a document to a student the way a module does: a signed APAAR consent form."""
    owner = school.people["owner"]
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO sis.apaar_consents (id, tenant_id, student_id, seq, status, "
                "relationship, decided_on, form_language, evidence_document_id, recorded_by, "
                "recorded_by_membership) VALUES (gen_random_uuid(), :t, :s, :q, 'given', "
                "'father', :on, 'en', :d, :u, :m)"
            ),
            {
                "t": school.tenant_id,
                "s": student_id,
                "q": seq,
                "on": dt.date(2026, 7, 1),
                "d": document_id,
                "u": owner.user_id,
                "m": owner.membership_id,
            },
        )


@pytest.fixture(scope="module")
def school(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    school = W.School(W.provision_school())
    owner = W.add_member(admin_engine, school.tenant_id, ["owner"])
    school.people["owner"] = owner
    W.build_structure(school, owner)
    school.people["clerk"] = W.add_member(admin_engine, school.tenant_id, ["office_staff"])
    school.people["idle"] = W.add_member(admin_engine, school.tenant_id, ["teacher"])
    school.ids["mine"] = SW.create(school, name="Synthetic Student Mine", section_key=None)
    school.ids["theirs"] = SW.create(school, name="Synthetic Student Theirs", section_key=None)
    school.ids["none"] = SW.create(school, name="Synthetic Student None", section_key=None)
    clerk = school.people["clerk"]
    first = _titled(admin_engine, school, "Consent form mine one", clerk)
    second = _titled(admin_engine, school, "Consent form mine two", clerk)
    other = _titled(admin_engine, school, "Consent form theirs", clerk)
    _link(admin_engine, school, school.ids["mine"], first, 1)
    _link(admin_engine, school, school.ids["mine"], second, 2)
    _link(admin_engine, school, school.ids["theirs"], other, 1)
    school.ids["mine_doc"] = first
    return school


def _report(school: Any, subject: Subject) -> list[ReportSection]:
    with tenant_session(school.tenant_id) as s:
        return service.subject_report(s, subject)


def _erase(school: Any, subject: Subject, *, dry_run: bool) -> list[ErasureOutcome]:
    with tenant_session(school.tenant_id) as s:
        return service.erase_for_subject(s, subject, CLASSES, dry_run=dry_run)


def test_FR_DPDP_009_student_report_lists_linked_documents_only(school: Any) -> None:
    sections = _report(school, Subject("student", school.ids["mine"]))
    assert len(sections) == 1
    section = sections[0]
    assert section.class_key in _classes()
    assert section.columns == ("Title", "Type", "Uploaded on")
    assert sorted(r[0] for r in section.rows) == ["Consent form mine one", "Consent form mine two"]
    assert {r[1] for r in section.rows} == {"Form"}
    flat = " ".join(" ".join(r) for r in section.rows)
    assert "theirs" not in flat
    assert "t/" not in flat  # never a storage key


def test_FR_DPDP_009_member_report_counts_uploads(school: Any) -> None:
    sections = _report(school, Subject("member", school.people["clerk"].membership_id))
    assert len(sections) == 1
    assert sections[0].class_key in _classes()
    assert {r[0]: r[1] for r in sections[0].rows}["Documents uploaded"] == "3"
    assert "Consent form" not in " ".join(" ".join(r) for r in sections[0].rows)


def test_FR_DPDP_009_nothing_held_or_unsupported_kind_is_empty(school: Any) -> None:
    assert _report(school, Subject("student", school.ids["none"])) == []
    assert _report(school, Subject("student", uuid.uuid4())) == []
    assert _report(school, Subject("member", school.people["idle"].membership_id)) == []
    assert _report(school, Subject("member", uuid.uuid4())) == []
    assert _report(school, Subject("guardian", school.ids["mine"])) == []


def test_FR_DPDP_013_student_erasure_is_a_manual_review_and_deletes_nothing(
    school: Any, admin_engine: Engine
) -> None:
    for dry_run in (True, False):
        outcome = _erase(school, Subject("student", school.ids["mine"]), dry_run=dry_run)
        assert outcome == [
            ErasureOutcome(
                "documents",
                0,
                manual_steps=(
                    "Review the 2 documents linked to this student in Documents; delete those "
                    "not needed as evidence for official records.",
                ),
            )
        ]
    with admin_engine.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM kb.documents WHERE id = :d"),
                {"d": school.ids["mine_doc"]},
            ).scalar_one()
            == 1
        )


def test_FR_DPDP_013_no_linked_documents_members_and_other_classes(school: Any) -> None:
    assert _erase(school, Subject("student", school.ids["none"]), dry_run=True) == [
        ErasureOutcome("documents", 0)
    ]
    assert (
        _erase(school, Subject("member", school.people["clerk"].membership_id), dry_run=False) == []
    )
    with tenant_session(school.tenant_id) as s:
        assert (
            service.erase_for_subject(
                s, Subject("student", school.ids["mine"]), {"ask_history"}, dry_run=True
            )
            == []
        )
