"""Public functions other modules added for certificates (CLAUDE.md §4: modules talk through
``service.py``): students withdrawal and enrolment histories, DQ open blockers, generated
documents and the certificate letterhead setting (FR-CERT-002, FR-CERT-005, FR-CERT-010,
FR-CERT-013)."""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.documents import service as documents
from app.documents.schemas import UploadCreate
from app.dq import service as dq
from app.students import service as students
from app.tenancy import service as tenancy
from app.tenancy.schemas import CertificateLetterhead, TenantSettingsPatch

pytestmark = pytest.mark.db
C = sys.modules["sos_test_certificates_support"]


def test_FR_CERT_005_withdrawal_ends_active_enrolments_and_audits(
    school: Any, admin_engine: Engine
) -> None:
    sid = C.student(school)
    cert_id = uuid.uuid4()
    with tenant_session(school.tenant_id) as s:
        out = students.withdraw_for_transfer_certificate(
            s, sid, left_on=C.today(), certificate_id=cert_id
        )
    assert out.previous_status == "active"
    assert out.status == "left"
    assert len(out.enrollment_ids) == 1
    assert [e["status"] for e in C.enrolments(admin_engine, sid)] == ["transferred"]
    with admin_engine.connect() as c:
        summary: dict[str, Any] = c.execute(
            text(
                "SELECT summary FROM audit.events WHERE tenant_id = :t "
                "AND action = 'student.withdrawn' AND resource_id = :s"
            ),
            {"t": school.tenant_id, "s": sid},
        ).scalar_one()
    assert summary["certificate_id"] == str(cert_id)
    assert summary["from"] == "active"
    assert summary["to"] == "left"


def test_FR_CERT_005_withdrawal_refuses_a_leaving_date_before_the_enrolment(school: Any) -> None:
    sid = C.student(school)
    with pytest.raises(ValidationFailed) as exc, tenant_session(school.tenant_id) as s:
        students.withdraw_for_transfer_certificate(
            s, sid, left_on=C.today() - dt.timedelta(days=400), certificate_id=uuid.uuid4()
        )
    assert exc.value.errors[0]["code"] == "leaving_date_before_enrolment"


def test_FR_CERT_005_withdrawal_keeps_a_graduated_status(school: Any, admin_engine: Engine) -> None:
    sid = C.student(school, section_key=None)
    with admin_engine.begin() as c:
        c.execute(text("UPDATE sis.students SET status = 'graduated' WHERE id = :s"), {"s": sid})
    with tenant_session(school.tenant_id) as s:
        out = students.withdraw_for_transfer_certificate(
            s, sid, left_on=C.today(), certificate_id=uuid.uuid4()
        )
    assert out.status == "graduated"
    assert out.enrollment_ids == ()


def test_FR_CERT_005_enrolment_histories_oldest_first(school: Any) -> None:
    sid = C.student(school)
    with tenant_session(school.tenant_id) as s:
        out = students.enrolment_histories(s, [sid, uuid.uuid4()])
    assert [e.status for e in out[sid]] == ["active"]


def test_FR_CERT_002_open_blockers_are_base_rule_blockers_on_the_given_fields(
    school: Any, admin_engine: Engine
) -> None:
    sid = C.student(school)
    run_id, finding_ids = uuid.uuid4(), [uuid.uuid4() for _ in range(4)]
    rows = [
        (finding_ids[0], "DQ-002", "dob", "blocker", None, "open"),
        (finding_ids[1], "DQ-003", "gender", "high", None, "open"),
        (finding_ids[2], "DQ-005", "dob", "blocker", "cisce-registration-2026", "open"),
        (finding_ids[3], "DQ-002", "dob", "blocker", None, "waived"),
    ]
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO sis.dq_runs (id, tenant_id, trigger, status, started_by, "
                "requested_by_membership) VALUES (:i, :t, 'manual', 'queued', :u, :m)"
            ),
            {
                "i": run_id,
                "t": school.tenant_id,
                "u": school.people["office_admin"].user_id,
                "m": school.people["office_admin"].membership_id,
            },
        )
        for fid, rule, attr, severity, profile, status in rows:
            c.execute(
                text(
                    "INSERT INTO sis.dq_findings (id, tenant_id, fingerprint, student_id, "
                    "rule_id, rule_version, severity, status, explanation_code, route_codes, "
                    "conflict_hash, first_seen_run_id, last_seen_run_id, attribute_key, "
                    "profile_key, waived_reason, waived_at, waived_by) VALUES (:i, :t, :fp, :s, "
                    ":r, 1, :sev, :st, :r, ARRAY['udise'], :h, :run, :run, :a, :p, :wr, :wa, :wb)"
                ),
                {
                    "i": fid,
                    "t": school.tenant_id,
                    "fp": uuid.uuid4().hex * 2,
                    "s": sid,
                    "r": rule,
                    "sev": severity,
                    "st": status,
                    "h": uuid.uuid4().hex * 2,
                    "run": run_id,
                    "a": attr,
                    "p": profile,
                    "wr": "Synthetic waiver reason" if status == "waived" else None,
                    "wa": dt.datetime.now(dt.UTC) if status == "waived" else None,
                    "wb": school.people["principal"].membership_id if status == "waived" else None,
                },
            )
    with tenant_session(school.tenant_id) as s:
        found = dq.open_blockers(s, sid, ["dob", "full_name"])
        none = dq.open_blockers(s, sid, ["full_name"])
    assert [b.finding_id for b in found] == [finding_ids[0]]
    assert none == []


def test_FR_CERT_010_certificate_purpose_is_never_uploadable() -> None:
    with pytest.raises(ValidationError):
        UploadCreate(
            filename="certificate.pdf",
            content_type="application/pdf",
            size_bytes=10,
            purpose="certificate",
        )


def test_FR_CERT_010_generated_documents_are_pdfs_of_a_generated_purpose(school: Any) -> None:
    user = school.people["office_admin"].user_id
    with tenant_session(school.tenant_id) as s:
        with pytest.raises(ValueError, match="generated purpose"):
            documents.store_generated_document(
                s,
                purpose="evidence",
                title="x",
                content=C.D.pdf(),
                created_by=user,
                acl_roles=["owner"],
            )
        with pytest.raises(documents.UnsupportedFileType):
            documents.store_generated_document(
                s,
                purpose="certificate",
                title="x",
                content=C.D.png(),
                created_by=user,
                acl_roles=["owner"],
            )


def test_FR_CERT_010_download_and_archive_only_for_generated_documents(
    school: Any, admin_engine: Engine
) -> None:
    evidence = C.D.make_document(
        admin_engine,
        school.tenant_id,
        school.people["owner"].user_id,
        purpose="evidence",
        doc_type="evidence",
        sensitivity="C3",
        status="ready",
    )
    with tenant_session(school.tenant_id) as s:
        with pytest.raises(NotFound):
            documents.generated_download_url(s, evidence, filename="x.pdf", ttl_s=60)
        assert documents.archive_generated_document(s, evidence) is False
    cert = C.issue(school, C.student(school), "bonafide")
    C.render(school, cert)
    doc = C.row(admin_engine, cert.id)["document_id"]
    with tenant_session(school.tenant_id) as s, pytest.raises(Conflict) as exc:
        documents.generated_download_url(s, doc, filename="x.pdf", ttl_s=60)
    assert exc.value.code == "document_not_ready", "only after the malware scan"


def test_FR_CERT_013_letterhead_is_a_school_setting(school: Any, admin_engine: Engine) -> None:
    with tenant_session(school.tenant_id) as s:
        before = tenancy.get_tenant(s)
        assert before.settings.certificate_letterhead == CertificateLetterhead()
        out = tenancy.update_tenant_settings(
            s,
            TenantSettingsPatch(
                certificate_letterhead=CertificateLetterhead(
                    school_name_te="కృత్రిమ మోడల్ పాఠశాల",
                    address_en="1 Synthetic Road, Guntur",
                    address_te="1 సింథటిక్ రోడ్, గుంటూరు",
                    affiliation="Recognised by the Synthetic Board",
                    place="Guntur",
                )
            ),
            expected_version=before.version,
        )
    assert out.settings.certificate_letterhead.place == "Guntur"
    cert = C.issue(school, C.student(school), "bonafide")
    assert cert.content is not None
    assert cert.content.school_name_te == "కృత్రిమ మోడల్ పాఠశాల"
    assert cert.content.school_place == "Guntur"
    with pytest.raises(ValidationError):
        CertificateLetterhead(place="Line one\nLine two")
