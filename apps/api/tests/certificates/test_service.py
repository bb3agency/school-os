"""Certificates through the real services (US-1101..US-1107; FR-CERT-002..012; BR-01, BR-04,
BR-12; invariants 3, 4, 6, 7). Synthetic data only."""

from __future__ import annotations

import concurrent.futures
import dataclasses
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.certificates import service as certificates
from app.certificates.schemas import ApproveIn, CertificateRequest, DuplicateRequest, ReasonIn
from app.core.db import tenant_session
from app.core.errors import Conflict, Forbidden, NotFound, StepUpRequired, ValidationFailed
from app.core.languages import contains_telugu
from app.core.pdf import FONT_FAMILY, FONT_URL
from app.core.redaction import verhoeff_check_digit
from app.students import service as students

pytestmark = pytest.mark.db
C = sys.modules["sos_test_certificates_support"]


def _dq() -> Any:
    name = "sos_test_dq_support"
    if name not in sys.modules:
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "dq" / "dq_support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _preview(school: Any, sid: uuid.UUID, certificate_type: str, role: str = "office_admin") -> Any:
    return C.call(school, school.people[role], role, certificates.preview, sid, certificate_type)


# --- US-1101: bonafide, study, conduct from the checked record ---------------------------------


def test_FR_CERT_006_bonafide_issued_at_once_with_the_next_serial(
    school: Any, admin_engine: Engine
) -> None:
    sid = C.student(school)
    first = C.issue(school, sid, "bonafide")
    second = C.issue(school, C.student(school), "bonafide")
    assert first.status == "issued"
    assert first.serial is not None
    assert first.serial.startswith("BC/2026-27/")
    n1, n2 = (int(c.serial.rsplit("/", 1)[1]) for c in (first, second))
    assert n2 == n1 + 1, "consecutive numbers per school, type and year"
    row = C.row(admin_engine, first.id)
    assert row["serial_no"] == n1
    assert row["pdf_status"] == "queued"
    assert row["issued_by"] == school.people["office_admin"].membership_id
    assert len(row["content_sha256"]) == 32
    assert first.content is not None
    assert first.content.student_name.startswith("Synthetica Certificate Student")
    assert C.actions(admin_engine, school.tenant_id, first.id) == [
        "certificate.requested",
        "certificate.issued",
    ]
    assert C.outbox(admin_engine, school.tenant_id, first.id) == ["certificate.render_requested"]


def test_FR_CERT_006_serials_run_per_type(school: Any) -> None:
    bonafide = C.issue(school, C.student(school), "bonafide")
    study = C.issue(school, C.student(school), "study")
    conduct = C.issue(school, C.student(school), "conduct")
    assert bonafide.serial
    assert bonafide.serial.startswith("BC/")
    assert study.serial
    assert study.serial.startswith("SC/")
    assert conduct.serial
    assert conduct.serial.startswith("CC/")


def test_FR_CERT_006_concurrent_issues_get_distinct_consecutive_numbers(school: Any) -> None:
    sids = [C.student(school) for _ in range(6)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda s: C.issue(school, s, "study"), sids))
    numbers = sorted(int(r.serial.rsplit("/", 1)[1]) for r in results)
    assert numbers == list(range(numbers[0], numbers[0] + 6)), "no gaps, no repeats"


def test_FR_CERT_006_a_rolled_back_issue_leaves_no_gap(school: Any) -> None:
    before = C.issue(school, C.student(school), "conduct")
    sid = C.student(school)
    person = school.people["office_admin"]

    def issue_then_fail() -> None:
        with tenant_session(school.tenant_id, person.user_id) as s:
            certificates.request_certificate(
                s,
                C.ctx(school, person, "office_admin"),
                sid,
                CertificateRequest(certificate_type="conduct", inputs={"conduct": "good"}),
            )
            raise RuntimeError("simulated failure after the number was taken")

    with pytest.raises(RuntimeError):
        issue_then_fail()
    after = C.issue(school, sid, "conduct")
    assert int(after.serial.rsplit("/", 1)[1]) == int(before.serial.rsplit("/", 1)[1]) + 1


def test_US_1101_preview_shows_values_sources_and_provisional_warnings(school: Any) -> None:
    sid = C.student(school)
    preview = _preview(school, sid, "bonafide")
    assert preview.can_issue
    assert preview.blockers == []
    by_key = {f.key: f for f in preview.fields}
    assert set(by_key) == {"full_name", "father_name", "dob", "admission_no"}
    assert by_key["dob"].value == "14/03/2012"
    assert by_key["full_name"].source == "admission_register"
    # SW.create records register identity values unverified: printed, flagged provisional.
    assert by_key["full_name"].provisional
    assert {w.attribute_key for w in preview.warnings} >= {"full_name", "dob"}
    assert preview.class_label == "Class IX A"
    assert preview.academic_year_label == "2026-27"


def test_FR_CERT_002_open_blocker_finding_on_a_printed_field_refuses(
    school: Any, admin_engine: Engine
) -> None:
    dq = _dq()
    sid = C.student(school, extra=dq.aadhaar(dob="2012-05-14"))
    dq.run(school, sid)  # DQ-002: register vs Aadhaar-as-printed date of birth (blocker)
    preview = _preview(school, sid, "bonafide")
    assert not preview.can_issue
    blocker = next(b for b in preview.blockers if b.code == "dq_blocker")
    assert blocker.rule_id == "DQ-002"
    assert blocker.attribute_key == "dob"
    assert blocker.finding_id is not None
    with pytest.raises(Conflict) as exc:
        C.issue(school, sid, "bonafide")
    assert exc.value.code == "certificate_blocked"
    with admin_engine.connect() as c:
        n: int = c.execute(
            text("SELECT count(*) FROM sis.certificates WHERE student_id = :s"), {"s": sid}
        ).scalar_one()
    assert n == 0, "nothing written when blocked"
    # A blocker on a field the certificate does not print does not stop it: conduct
    # certificates print no date of birth.
    assert C.issue(school, sid, "conduct").status == "issued"


def test_FR_CERT_002_blocker_found_by_a_clerk_without_dq_read(school: Any) -> None:
    dq = _dq()
    sid = C.student(school, extra=dq.aadhaar(dob="2012-05-14"))
    dq.run(school, sid)
    clerk = school.people["office_staff"]
    no_dq = dataclasses.replace(
        C.ctx(school, clerk, "office_staff"),
        permissions=C.ctx(school, clerk, "office_staff").permissions - {"dq.findings.read"},
    )
    with pytest.raises(Conflict) as exc:
        C.call(
            school,
            clerk,
            "office_staff",
            certificates.request_certificate,
            sid,
            CertificateRequest(certificate_type="bonafide", inputs={"purpose": "passport"}),
            as_ctx=no_dq,
        )
    assert exc.value.code == "certificate_blocked"


def test_FR_CERT_002_missing_required_value_blocks(school: Any) -> None:
    sid = C.student(school, admission_no=None)  # no admission number in the register
    preview = _preview(school, sid, "study")
    assert any(
        b.code == "missing_value" and b.attribute_key == "admission_no" for b in preview.blockers
    )


def test_FR_CERT_002_bonafide_needs_a_current_enrolment(school: Any) -> None:
    sid = C.student(school, section_key=None)
    preview = _preview(school, sid, "bonafide")
    assert [b.code for b in preview.blockers] == ["no_enrolment"]


def test_FR_CERT_009_inputs_refuse_full_aadhaar_and_bad_values(school: Any) -> None:
    body = "23456789012"
    aadhaar = body + verhoeff_check_digit(body)
    sid = C.student(school)
    with pytest.raises(ValidationFailed) as exc:
        C.issue(
            school,
            sid,
            "bonafide",
            inputs={"purpose": "other", "purpose_note": f"Aadhaar {aadhaar} update"},
        )
    assert exc.value.errors[0]["code"] == "aadhaar_full_number_rejected"
    with pytest.raises(ValidationFailed) as exc:
        C.issue(school, sid, "bonafide", inputs={"purpose": "lottery"})
    assert exc.value.errors[0]["code"] == "invalid_choice"
    with pytest.raises(ValidationFailed) as exc:
        C.issue(school, sid, "bonafide", inputs={"purpose": "passport", "house": "red"})
    assert exc.value.errors[0]["code"] == "unknown_input"
    future = C.today().replace(year=C.today().year + 1).isoformat()
    with pytest.raises(ValidationFailed) as exc:
        C.issue(school, sid, "transfer", inputs=C.tc_inputs(leaving_date=future))
    assert exc.value.errors[0]["code"] == "date_in_future"


def test_FR_CERT_009_certificate_never_prints_aadhaar_or_restricted_fields(school: Any) -> None:
    dq = _dq()
    sid = C.student(school, extra=dq.aadhaar(last4="1234", name="Synthetica Aadhaar Name"))
    cert = C.issue(school, sid, "bonafide")
    assert cert.content is not None
    keys = {f.key for f in cert.content.fields}
    assert not keys & {"aadhaar_last4", "aadhaar_name_as_printed", "caste", "category", "religion"}
    page = C.call(
        school,
        school.people["office_admin"],
        "office_admin",
        certificates.print_page,
        cert.id,
    )
    assert "1234" not in page
    assert "Synthetica Aadhaar Name" not in page


# --- US-1102: transfer certificate, maker-checker, withdrawal ------------------------------------


def test_US_1102_tc_waits_for_approval_and_notifies_approvers(
    school: Any, admin_engine: Engine
) -> None:
    tc = C.pending_tc(school)
    assert tc.status == "pending"
    assert tc.serial is None
    assert tc.requires_approval
    row = C.row(admin_engine, tc.id)
    assert row["serial"] is None
    assert row["content"] is None
    notified = {m for key, m in C.notifications(admin_engine, school.tenant_id, tc.id)}
    assert school.people["principal"].membership_id in notified
    assert school.people["owner"].membership_id in notified
    assert school.people["office_admin"].membership_id not in notified


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_CERT_005_approval_issues_tc_ends_enrolment_and_student_leaves(
    school: Any, admin_engine: Engine
) -> None:
    sid = C.student(school)
    assert [e["status"] for e in C.enrolments(admin_engine, sid)] == ["active"]
    tc = C.pending_tc(school, sid)
    issued = C.approve(school, tc)
    assert issued.status == "issued"
    assert issued.serial
    assert issued.serial.startswith("TC/2026-27/")
    assert issued.decided_by == school.people["principal"].membership_id
    enrolments = C.enrolments(admin_engine, sid)
    assert [e["status"] for e in enrolments] == ["transferred"]
    assert enrolments[0]["ended_on"] == C.today()
    assert C.student_status(admin_engine, sid) == "left"
    acts = C.actions(admin_engine, school.tenant_id, tc.id)
    assert acts[:1] == ["certificate.requested"]
    assert "certificate.issued" in acts
    assert "student.withdrawn" in acts, "the students module audits the withdrawal"
    assert ("certificate.approved", school.people["office_admin"].membership_id) in C.notifications(
        admin_engine, school.tenant_id, tc.id
    )
    assert issued.content is not None
    details = {d.key: d.value for d in issued.content.details}
    assert details["dob_in_words"] == "Fourteenth March Two Thousand Twelve"
    assert details["class_at_leaving"] == "Class IX A (2026-27) / 9వ తరగతి A (2026-27)"
    assert len(issued.content.blanks) >= 1, "TODO(official format) lines printed blank"


def test_FR_CERT_004_requester_cannot_approve_even_with_both_roles(
    school: Any, admin_engine: Engine
) -> None:
    dual = school.people["dual"]
    both = C.ctx_roles(school, dual, "office_admin", "principal")
    tc = C.call(
        school,
        dual,
        "office_admin",
        certificates.request_certificate,
        C.student(school),
        CertificateRequest(certificate_type="transfer", inputs=C.tc_inputs()),
        as_ctx=both,
    )
    with pytest.raises(Forbidden) as exc:
        C.call(
            school,
            dual,
            "principal",
            certificates.approve,
            tc.id,
            ApproveIn(draft_sha256="0" * 64),
            expected_version=tc.version,
            as_ctx=both,
        )
    assert exc.value.code == "self_approval_forbidden"
    with pytest.raises(Forbidden):
        C.call(
            school,
            dual,
            "principal",
            certificates.reject,
            tc.id,
            ReasonIn(reason="Synthetic self rejection attempt"),
            expected_version=tc.version,
            as_ctx=both,
        )
    # The database refuses it too (certificates_maker_checker).
    with pytest.raises(DBAPIError, match="certificates_maker_checker"), admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE sis.certificates SET decided_by = requested_by, decided_at = now() "
                "WHERE id = :i"
            ),
            {"i": tc.id},
        )


def test_FR_CERT_004_approval_needs_recent_mfa(school: Any) -> None:
    tc = C.pending_tc(school)
    principal = school.people["principal"]
    stale = dataclasses.replace(C.ctx(school, principal, "principal"), auth_time=None)
    with pytest.raises(StepUpRequired):
        C.call(
            school,
            principal,
            "principal",
            certificates.approve,
            tc.id,
            ApproveIn(draft_sha256="0" * 64),
            expected_version=tc.version,
            as_ctx=stale,
        )


def test_FR_CERT_004_reject_needs_a_reason_and_notifies(school: Any, admin_engine: Engine) -> None:
    tc = C.pending_tc(school)
    principal = school.people["principal"]
    with pytest.raises(ValidationFailed):
        C.call(
            school,
            principal,
            "principal",
            certificates.reject,
            tc.id,
            ReasonIn(reason="short"),
            expected_version=tc.version,
        )
    out = C.call(
        school,
        principal,
        "principal",
        certificates.reject,
        tc.id,
        ReasonIn(reason="The leaving date does not match the application"),
        expected_version=tc.version,
    )
    assert out.status == "rejected"
    assert ("certificate.rejected", school.people["office_admin"].membership_id) in C.notifications(
        admin_engine, school.tenant_id, tc.id
    )
    with pytest.raises(Conflict) as exc:
        C.approve(school, out)
    assert exc.value.code == "certificate_not_pending"


def test_FR_CERT_004_requester_withdraws_own_request_only(school: Any) -> None:
    tc = C.pending_tc(school)
    staff = school.people["office_staff"]
    with pytest.raises(Forbidden) as exc:
        C.call(
            school,
            staff,
            "office_staff",
            certificates.withdraw,
            tc.id,
            expected_version=tc.version,
        )
    assert exc.value.code == "not_requester"
    out = C.call(
        school,
        school.people["office_admin"],
        "office_admin",
        certificates.withdraw,
        tc.id,
        expected_version=tc.version,
    )
    assert out.status == "withdrawn"


def test_US_1102_AC4_one_live_tc_per_student(school: Any) -> None:
    sid = C.student(school)
    C.pending_tc(school, sid)
    with pytest.raises(Conflict) as exc:
        C.pending_tc(school, sid)
    assert exc.value.code == "transfer_certificate_exists"


def test_FR_CERT_002_blockers_checked_again_at_approval(school: Any) -> None:
    dq = _dq()
    sid = C.student(school, extra=dq.aadhaar(dob="2012-03-14"))
    tc = C.pending_tc(school, sid)
    person = school.people["office_admin"]
    with tenant_session(school.tenant_id, person.user_id) as s:  # a conflicting value arrives
        students.record_value(
            s,
            C.SW.admin_ctx(school),
            sid,
            "aadhaar_dob_as_printed",
            "aadhaar_as_printed",
            "2012-07-01",
        )
    dq.run(school, sid)
    with pytest.raises(Conflict) as exc:
        C.approve(school, tc)
    assert exc.value.code == "certificate_blocked"


# --- US-1104, US-1105: duplicates and cancellation -----------------------------------------------


def test_FR_CERT_007_duplicate_copies_the_original_and_is_marked(
    school: Any, admin_engine: Engine
) -> None:
    original = C.issue(school, C.student(school), "bonafide")
    dup = C.duplicate(school, original.id)
    assert dup.status == "issued"
    assert dup.original_certificate_id == original.id
    assert dup.duplicate_no == 1
    assert dup.serial == original.serial, "prints the original serial"
    assert dup.content == original.content, "a copy, not a new certificate"
    assert C.row(admin_engine, dup.id)["serial"] is None, "no serial of its own"
    second = C.duplicate(school, dup.id)  # a duplicate of a duplicate copies the original
    assert second.original_certificate_id == original.id
    assert second.duplicate_no == 2
    assert "certificate.duplicate_requested" in C.actions(admin_engine, school.tenant_id, dup.id)
    page = C.call(
        school, school.people["office_admin"], "office_admin", certificates.print_page, dup.id
    )
    assert "DUPLICATE (copy 1)" in page
    assert original.serial in page


def test_FR_CERT_007_tc_duplicate_needs_approval(school: Any) -> None:
    tc = C.issued_tc(school)
    dup = C.duplicate(school, tc.id)
    assert dup.status == "pending"
    issued = C.approve(school, dup)
    assert issued.status == "issued"
    assert issued.duplicate_no == 1


def test_FR_CERT_008_cancel_keeps_number_and_needs_reason(
    school: Any, admin_engine: Engine
) -> None:
    cert = C.issue(school, C.student(school), "study")
    cancelled = C.cancel(school, cert)
    assert cancelled.status == "cancelled"
    assert cancelled.serial == cert.serial
    assert C.row(admin_engine, cert.id)["serial_no"] == C.row(admin_engine, cert.id)["serial_no"]
    with pytest.raises(Conflict) as exc:
        C.duplicate(school, cert.id)
    assert exc.value.code == "certificate_not_issued"
    # the next certificate never reuses the cancelled number
    nxt = C.issue(school, C.student(school), "study")
    assert nxt.serial != cert.serial
    audit = C.audit_rows(admin_engine, school.tenant_id, cert.id)
    assert audit[-1]["action"] == "certificate.cancelled"


def test_FR_CERT_008_cancelled_tc_does_not_readmit(school: Any, admin_engine: Engine) -> None:
    sid = C.student(school)
    tc = C.issued_tc(school, sid)
    C.cancel(school, tc)
    assert C.student_status(admin_engine, sid) == "left"
    assert [e["status"] for e in C.enrolments(admin_engine, sid)] == ["transferred"]


def test_FR_REG_005_issued_register_entry_is_frozen_for_the_app(
    school: Any, app_engine: Engine
) -> None:
    cert = C.issue(school, C.student(school), "bonafide")
    with (
        pytest.raises(DBAPIError, match="certificates_frozen"),
        tenant_session(school.tenant_id) as s,
    ):
        s.execute(text("UPDATE sis.certificates SET serial_no = 999 WHERE id = :i"), {"i": cert.id})
    with (
        pytest.raises(DBAPIError, match="permission denied"),
        tenant_session(school.tenant_id) as s,
    ):
        s.execute(text("DELETE FROM sis.certificates WHERE id = :i"), {"i": cert.id})
    with (
        pytest.raises(DBAPIError, match="permission denied"),
        tenant_session(school.tenant_id) as s,
    ):
        s.execute(
            text("UPDATE sis.certificates SET student_id = student_id WHERE id = :i"),
            {"i": cert.id},
        )


def test_FR_CERT_006_counter_never_goes_down(school: Any, admin_engine: Engine) -> None:
    C.issue(school, C.student(school), "bonafide")
    with (
        pytest.raises(DBAPIError, match="certificate_counters_never_down"),
        tenant_session(school.tenant_id) as s,
    ):
        s.execute(
            text(
                "UPDATE sis.certificate_counters SET last_no = 0 "
                "WHERE certificate_type = 'bonafide'"
            )
        )


# --- scope, reads --------------------------------------------------------------------------------


def test_SEC_015_scoped_clerk_reaches_only_own_section(school: Any) -> None:
    inside = C.student(school, section_key="section_9a")
    outside = C.student(school, section_key="section_10a")
    ok = C.call(
        school,
        school.people["scoped_clerk"],
        "office_staff",
        certificates.request_certificate,
        inside,
        CertificateRequest(certificate_type="bonafide", inputs={"purpose": "bus_pass"}),
        as_ctx=_scoped_ctx(school),
    )
    assert ok.status == "issued"
    with pytest.raises(NotFound):
        C.call(
            school,
            school.people["scoped_clerk"],
            "office_staff",
            certificates.request_certificate,
            outside,
            CertificateRequest(certificate_type="bonafide", inputs={"purpose": "bus_pass"}),
            as_ctx=_scoped_ctx(school),
        )
    other = C.issue(school, outside, "bonafide")
    clerk = school.people["scoped_clerk"]
    listed = C.call(
        school,
        clerk,
        "office_staff",
        certificates.list_certificates,
        limit=200,
        as_ctx=_scoped_ctx(school),
    )
    ids = {c.id for c in listed.data}
    assert ok.id in ids
    assert other.id not in ids
    with pytest.raises(NotFound):
        C.call(
            school,
            clerk,
            "office_staff",
            certificates.get_certificate,
            other.id,
            as_ctx=_scoped_ctx(school),
        )


def _scoped_ctx(school: Any) -> Any:
    from app.authz.context import Scopes, UserContext

    clerk = school.people["scoped_clerk"]
    return UserContext(
        user_id=clerk.user_id,
        tenant_id=school.tenant_id,
        membership_id=clerk.membership_id,
        roles=frozenset({"scoped_clerk"}),
        permissions=frozenset({"student.read_basic", "certificate.read", "certificate.issue"}),
        scopes=Scopes(
            school=False, class_ids=frozenset(), section_ids=frozenset({school.ids["section_9a"]})
        ),
        mfa=True,
        auth_time=None,
        scoped_permissions=frozenset(
            {"student.read_basic", "certificate.read", "certificate.issue"}
        ),
    )


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_CERT_011_print_page_is_escaped_bilingual_and_marks_drafts(school: Any) -> None:
    tc = C.pending_tc(school)
    page = C.call(school, school.people["principal"], "principal", certificates.print_page, tc.id)
    assert "DRAFT" in page
    assert "ముసాయిదా" in page
    assert "TRANSFER CERTIFICATE" in page
    assert "బదిలీ ధృవీకరణ పత్రం" in page
    assert "<script" not in page


# --- PDF as a document (FR-CERT-010) -------------------------------------------------------------


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_CERT_010_pdf_stored_as_private_certificate_document(
    school: Any, admin_engine: Engine
) -> None:
    store = C.install()
    cert = C.issue(school, C.student(school), "bonafide")
    renderer = C.FakeRenderer()
    assert C.render(school, cert, renderer) == "ready"
    assert C.render(school, cert, renderer) == "ready", "idempotent"
    assert len(renderer.pages) == 1
    assert "Noto Sans Telugu" in renderer.pages[0] or "SOS Noto Sans Telugu" in renderer.pages[0]
    row = C.row(admin_engine, cert.id)
    assert row["pdf_status"] == "ready"
    with admin_engine.connect() as c:
        doc = c.execute(
            text("SELECT * FROM kb.documents WHERE id = :d"), {"d": row["document_id"]}
        ).one()
        acl = {
            r[0]
            for r in c.execute(
                text("SELECT principal_ref FROM kb.document_acl WHERE document_id = :d"),
                {"d": row["document_id"]},
            )
        }
        version = c.execute(
            text("SELECT object_key, status FROM kb.document_versions WHERE document_id = :d"),
            {"d": row["document_id"]},
        ).one()
    assert (doc.purpose, doc.doc_type, doc.sensitivity) == ("certificate", "certificate", "C2")
    assert acl == {"owner", "principal", "office_admin", "office_staff"}
    assert version.object_key.startswith(f"t/{school.tenant_id}/docs/")
    assert version.status == "queued", "scanned and indexed like any upload"
    assert version.object_key in store.objects
    assert "certificate.pdf_stored" in C.actions(admin_engine, school.tenant_id, cert.id)
    # The document cannot be deleted while the register entry refers to it.
    with pytest.raises(DBAPIError), tenant_session(school.tenant_id) as s:
        s.execute(text("DELETE FROM kb.documents WHERE id = :d"), {"d": row["document_id"]})


# --- English first (ADR-0036): Telugu hidden by default --------------------------------------


def test_ADR_0036_tc_content_print_page_and_catalog_are_english_while_telugu_is_hidden(
    school: Any,
) -> None:
    tc = C.pending_tc(school)
    page = C.call(school, school.people["principal"], "principal", certificates.print_page, tc.id)
    assert "DRAFT · NOT VALID" in page
    assert "TRANSFER CERTIFICATE" in page
    assert not contains_telugu(page)
    assert FONT_FAMILY not in page
    assert "Noto Sans Telugu" not in page
    issued = C.approve(school, tc)
    assert issued.content is not None
    assert not contains_telugu(issued.content.model_dump_json())
    assert issued.content.title_te == ""
    assert not any(d.key.endswith("_te") for d in issued.content.details)
    details = {d.key: d.value for d in issued.content.details}
    assert details["class_at_leaving"] == "Class IX A (2026-27)"
    catalog = certificates.types_catalog()
    assert all(t.label_te == "" for t in catalog)
    assert not contains_telugu("".join(t.model_dump_json() for t in catalog))


def test_ADR_0036_certificate_pdf_page_has_no_telugu_or_telugu_font_by_default(
    school: Any, admin_engine: Engine
) -> None:
    C.install()
    cert = C.issue(school, C.student(school), "bonafide")
    renderer = C.FakeRenderer()
    assert C.render(school, cert, renderer) == "ready"
    (page,) = renderer.pages
    assert "This is to certify that" in page
    assert not contains_telugu(page)
    assert FONT_URL not in page
    assert "@font-face" not in page
    assert "Noto Sans Telugu" not in page
    row = C.row(admin_engine, cert.id)
    with admin_engine.connect() as c:
        language: str = c.execute(
            text("SELECT language FROM kb.documents WHERE id = :d"), {"d": row["document_id"]}
        ).scalar_one()
    assert language == "en"


def test_FR_CERT_011_download_url_after_scan_and_audited(school: Any, admin_engine: Engine) -> None:
    cert = C.issue(school, C.student(school), "bonafide")
    reader = school.people["office_staff"]
    with pytest.raises(Conflict) as exc:
        C.call(school, reader, "office_staff", certificates.download_url, cert.id)
    assert exc.value.code == "pdf_not_ready"
    C.render(school, cert)
    doc_id = C.row(admin_engine, cert.id)["document_id"]
    C.mark_document_ready(admin_engine, doc_id)
    out = C.call(school, reader, "office_staff", certificates.download_url, cert.id)
    assert out.filename.startswith("bonafide-BC-2026-27-")
    assert out.filename.endswith(".pdf")
    assert "certificate.downloaded" in C.actions(admin_engine, school.tenant_id, cert.id)


def test_FR_CERT_008_cancel_archives_the_document(school: Any, admin_engine: Engine) -> None:
    cert = C.issue(school, C.student(school), "conduct")
    C.render(school, cert)
    cancelled = C.cancel(
        school,
        C.call(
            school, school.people["principal"], "principal", certificates.get_certificate, cert.id
        ),
    )
    assert cancelled.status == "cancelled"
    with admin_engine.connect() as c:
        status: str = c.execute(
            text("SELECT status FROM kb.documents WHERE id = :d"),
            {"d": C.row(admin_engine, cert.id)["document_id"]},
        ).scalar_one()
    assert status == "archived"


def test_SEC_015_a_cancelled_certificates_clean_pdf_is_not_handed_out(
    school: Any, admin_engine: Engine
) -> None:
    """Audit 2026-10-05 A-07: the PDF stored at issue carries no CANCELLED mark, so after a
    cancellation its download would look like a valid certificate."""
    cert = C.issue(school, C.student(school), "conduct")
    C.render(school, cert)
    C.mark_document_ready(admin_engine, C.row(admin_engine, cert.id)["document_id"])
    reader = school.people["office_staff"]
    assert C.call(school, reader, "office_staff", certificates.download_url, cert.id).url
    C.cancel(
        school,
        C.call(
            school, school.people["principal"], "principal", certificates.get_certificate, cert.id
        ),
    )
    with pytest.raises(Conflict) as exc:
        C.call(school, reader, "office_staff", certificates.download_url, cert.id)
    assert exc.value.code == "certificate_cancelled"


def test_SEC_015_a_duplicate_of_a_cancelled_original_is_marked_and_not_handed_out(
    school: Any, admin_engine: Engine
) -> None:
    """Audit 2026-10-05 A-07: cancelling the original left its issued duplicates printable and
    downloadable with no mark (only the register said "Original cancelled")."""
    original = C.issue(school, C.student(school), "bonafide")
    copy = C.duplicate(school, original.id)
    assert copy.status == "issued"
    C.render(school, copy)
    C.mark_document_ready(admin_engine, C.row(admin_engine, copy.id)["document_id"])
    reader = school.people["office_staff"]
    assert "CANCELLED" not in C.call(
        school, reader, "office_staff", certificates.print_page, copy.id
    )
    C.cancel(
        school,
        C.call(
            school,
            school.people["principal"],
            "principal",
            certificates.get_certificate,
            original.id,
        ),
    )
    assert "CANCELLED" in C.call(school, reader, "office_staff", certificates.print_page, copy.id)
    with pytest.raises(Conflict) as exc:
        C.call(school, reader, "office_staff", certificates.download_url, copy.id)
    assert exc.value.code == "certificate_cancelled"


def test_FR_CERT_010_failed_render_is_marked_and_can_be_retried(
    school: Any, admin_engine: Engine
) -> None:
    cert = C.issue(school, C.student(school), "study")
    certificates.mark_pdf_failed(school.tenant_id, cert.id, "render_failed")
    assert C.row(admin_engine, cert.id)["pdf_status"] == "failed"
    out = C.call(
        school, school.people["office_admin"], "office_admin", certificates.retry_render, cert.id
    )
    assert out.pdf_status == "queued"
    assert (
        C.outbox(admin_engine, school.tenant_id, cert.id).count("certificate.render_requested") == 2
    )


def _changes_objects() -> Any:
    name = "sos_test_changes_objects"
    if name not in sys.modules:
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "changes" / "objects.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _approve_with(school: Any, tc: Any, digest: str) -> Any:
    return C.call(
        school,
        school.people["principal"],
        "principal",
        certificates.approve,
        tc.id,
        ApproveIn(draft_sha256=digest),
        expected_version=tc.version,
    )


def test_A_11_approval_refuses_a_draft_edited_after_the_principal_read_it(
    school: Any, admin_engine: Engine
) -> None:
    """The principal reads the draft; the clerk then changes a printed value. Approving with
    the fingerprint that was read is refused (409), so the edit is never frozen unseen
    (audit 2026-10-05 A-11)."""
    sid = C.student(school)
    tc = C.pending_tc(school, sid)
    read = C.draft_hash(school, tc.id)
    assert read == C.draft_hash(school, tc.id), "the fingerprint is stable between reads"
    admin = school.people["office_admin"]
    with tenant_session(school.tenant_id, admin.user_id) as s:
        students.record_value(
            s, C.ctx(school, admin, "office_admin"), sid, "mother_tongue", "parent_form", "Hindi"
        )
    changed = C.draft_hash(school, tc.id)
    assert changed != read
    with pytest.raises(Conflict) as exc:
        _approve_with(school, tc, read)
    assert exc.value.code == "certificate_draft_changed"
    assert C.row(admin_engine, tc.id)["status"] == "pending"
    assert "certificate.issued" not in C.actions(admin_engine, school.tenant_id, tc.id)
    out = _approve_with(school, tc, changed)
    assert out.status == "issued"
    assert out.draft_sha256 is None
    printed = {line.key: line.value for line in (out.content.fields if out.content else [])}
    assert printed["mother_tongue"] == "Hindi"


def test_A_11_draft_fingerprint_only_on_a_pending_certificate_read_alone(
    school: Any, admin_engine: Engine
) -> None:
    tc = C.pending_tc(school)
    principal = school.people["principal"]
    listed = C.call(
        school, principal, "principal", certificates.list_certificates, student_id=tc.student_id
    )
    assert [c.draft_sha256 for c in listed.data] == [None]
    issued = C.approve(school, tc)
    again = C.call(school, principal, "principal", certificates.get_certificate, issued.id)
    assert again.draft_sha256 is None
    # A pending TC duplicate prints its original's frozen content: that is its fingerprint.
    copy = C.duplicate(school, issued.id)
    original = C.row(admin_engine, issued.id)["content_sha256"].hex()
    assert C.draft_hash(school, copy.id) == original
    assert C.approve(school, copy).status == "issued"


def test_pending_change_request_on_a_printed_field_blocks_issuing(
    school: Any, admin_engine: Engine
) -> None:
    """A correction waiting for approval would change what the certificate prints: preview shows
    it, issuing and approving wait for the decision (audit 2026-10-05 hardening)."""
    sid = C.student(school)
    tc = C.pending_tc(school, sid)
    digest = C.draft_hash(school, tc.id)
    cr = _changes_objects()
    cr.submit(
        admin_engine,
        school,
        school.people["office_admin"],
        "office_admin",
        student_id=sid,
        attribute_key="dob",
        new_value="2012-03-15",
    )
    preview = C.call(
        school,
        school.people["office_admin"],
        "office_admin",
        certificates.preview,
        sid,
        "bonafide",
    )
    assert [(b.code, b.attribute_key) for b in preview.blockers] == [
        ("change_request_pending", "dob")
    ]
    assert preview.can_issue is False
    with pytest.raises(Conflict) as exc:
        C.issue(school, sid, "bonafide")
    assert exc.value.code == "change_request_pending"
    with pytest.raises(Conflict) as exc:
        _approve_with(school, tc, digest)
    assert exc.value.code == "change_request_pending"
    # A field the certificate does not print does not block it.
    other = C.student(school)
    cr.submit(
        admin_engine,
        school,
        school.people["office_admin"],
        "office_admin",
        student_id=other,
        attribute_key="gender",
        new_value="female",
    )
    assert C.issue(school, other, "bonafide").status == "issued"


def test_tc_approval_needs_the_preparer_to_still_be_an_active_member(
    school: Any, admin_engine: Engine
) -> None:
    maker = C.W.add_member(admin_engine, school.tenant_id, ["office_admin"])
    tc = C.call(
        school,
        maker,
        "office_admin",
        certificates.request_certificate,
        C.student(school),
        CertificateRequest(certificate_type="transfer", inputs=C.tc_inputs()),
    )
    digest = C.draft_hash(school, tc.id)
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.memberships SET status = 'suspended' WHERE id = :m"),
            {"m": maker.membership_id},
        )
    with pytest.raises(Conflict) as exc:
        _approve_with(school, tc, digest)
    assert exc.value.code == "requester_inactive"
    rejected = C.call(
        school,
        school.people["principal"],
        "principal",
        certificates.reject,
        tc.id,
        ReasonIn(reason="The person who prepared it has left the school"),
        expected_version=tc.version,
    )
    assert rejected.status == "rejected"


def test_US_1104_duplicate_reason_is_required(school: Any) -> None:
    cert = C.issue(school, C.student(school), "bonafide")
    with pytest.raises(ValidationFailed):
        C.call(
            school,
            school.people["office_admin"],
            "office_admin",
            certificates.request_duplicate,
            cert.id,
            DuplicateRequest(reason="lost"),
        )
