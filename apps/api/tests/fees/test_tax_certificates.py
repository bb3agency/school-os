"""Annual tuition fee certificate for income tax (FM-06; FR-FEE-046..060; lead decision D84).
Synthetic data only.

Covers: the head setting "counts as tuition for tax" (default on for kind tuition), the amount
(tuition allocations of issued, not cancelled receipts dated in the financial year, less refunds
of those lines), receipt numbers and dates, the serial from the ``FTC`` series, re-issue
(unchanged / superseded), bulk per section, the financial-year checks, the frozen row, audit in
the same transaction, the print view and PDF, and over HTTP: allowed role 201, disallowed role
403, other school's certificate 404, no PII in logs.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal as D  # noqa: N817
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.fees import service as fees
from app.fees import tax_config
from app.fees.schemas import AllocationIn, DecisionIn, HeadCreate, HeadPatch, RefundCreate
from app.fees.tax_schemas import TaxCertificateBulkIssue, TaxCertificateIssue

pytestmark = pytest.mark.db

F: Any = __import__("sys").modules["sos_test_fees_support"]
W = F.W
FY = "2026-27"
BASE = "/api/v1/fees/tax-certificates"


def _pay(school: Any, account_id: uuid.UUID, tuition: str, exam: str | None = None) -> Any:
    """A receipt of ``tuition`` on TUI (and ``exam`` on EXM) of instalment 1."""
    setup = F.ensure_setup(school)
    parts = [AllocationIn(instalment_no=1, head_id=setup.tui, amount=D(tuition))]
    if exam is not None:
        parts.append(AllocationIn(instalment_no=1, head_id=setup.exm, amount=D(exam)))
    amount = sum((p.amount for p in parts), D("0.00"))
    return F.issue(school, account_id, amount, allocations=parts)


def _issue(school: Any, student_id: uuid.UUID, role: str = "office_staff") -> Any:
    return F.call(
        school,
        role,
        fees.issue_tax_certificate,
        TaxCertificateIssue(student_id=student_id, financial_year=FY),
    )


def _student_with_account(school: Any) -> tuple[uuid.UUID, uuid.UUID]:
    student = F.student(school)
    return student, F.account(school, student)


# --- configuration (pure) -------------------------------------------------------------------------


def test_FR_FEE_056_legal_wording_is_config_and_marked_unverified() -> None:
    cfg = tax_config.load_config()
    assert cfg.legal.verified is False
    assert "Schedule XV" in cfg.legal.section_reference
    assert "not tax advice" in cfg.legal.disclaimer
    assert "not eligibility" in cfg.legal.disclaimer
    assert cfg.numbering.render(FY, 7) == "FTC/2026-27/00007"


@pytest.mark.parametrize(
    ("label", "bounds"),
    [
        ("2026-27", (dt.date(2026, 4, 1), dt.date(2027, 3, 31))),
        ("2027-28", (dt.date(2027, 4, 1), dt.date(2028, 3, 31))),
        ("2026-28", None),
        ("26-27", None),
    ],
)
def test_FR_FEE_048_financial_year_is_april_to_march(
    label: str, bounds: tuple[dt.date, dt.date] | None
) -> None:
    assert tax_config.fy_bounds(label, 4) == bounds


@pytest.mark.parametrize(
    ("today", "year"),
    [
        (dt.date(2026, 11, 9), None),  # October-December: no button
        (dt.date(2026, 12, 31), None),
        (dt.date(2027, 1, 1), "2026-27"),  # from 1 January of the last quarter
        (dt.date(2027, 3, 31), "2026-27"),
        (dt.date(2027, 4, 1), "2026-27"),  # the year that just ended, until 30 September
        (dt.date(2027, 9, 30), "2026-27"),
        (dt.date(2027, 10, 1), None),
    ],
)
def test_FR_FEE_057_parent_button_window(today: dt.date, year: str | None) -> None:
    assert tax_config.parent_link_year(today, 4) == year


# --- heads (FR-FEE-047) ---------------------------------------------------------------------------


def test_FR_FEE_047_tuition_heads_count_by_default_and_the_setting_changes(school: Any) -> None:
    setup = F.ensure_setup(school)
    heads = {h.id: h for h in F.call(school, "accountant", fees.list_heads)}
    assert heads[setup.tui].tax_tuition is True
    assert heads[setup.exm].tax_tuition is False
    assert heads[setup.bus].tax_tuition is False
    made = F.call(
        school,
        "accountant",
        fees.create_head,
        HeadCreate(code=f"T{uuid.uuid4().hex[:5].upper()}", name="Term tuition", kind="tuition"),
    )
    assert made.tax_tuition is True
    off = F.call(
        school,
        "accountant",
        fees.create_head,
        HeadCreate(
            code=f"L{uuid.uuid4().hex[:5].upper()}",
            name="Lab tuition",
            kind="other",
            tax_tuition=True,
        ),
    )
    assert off.tax_tuition is True
    changed = F.call(
        school,
        "accountant",
        fees.update_head,
        off.id,
        HeadPatch(tax_tuition=False),
        expected_version=off.version,
    )
    assert changed.tax_tuition is False


# --- issuing (FR-FEE-048..052) --------------------------------------------------------------------


def test_FR_FEE_048_amount_is_tuition_of_issued_receipts_less_refunds(
    school: Any, admin_engine: Engine
) -> None:
    student, account = _student_with_account(school)
    first = _pay(school, account, "1000.00", exam="500.00")
    second = _pay(school, account, "600.00")
    cancelled = _pay(school, account, "200.00")
    with admin_engine.begin() as c:  # an approved cancellation marks the receipt cancelled
        c.execute(
            text("UPDATE fin.receipts SET status = 'cancelled' WHERE id = :r"), {"r": cancelled.id}
        )
    req = F.call(
        school,
        "accountant",
        fees.request_refund,
        second.id,
        RefundCreate(amount=D("100.00"), mode="cash", reason="Paid twice for the same term"),
    )
    F.call(school, "principal", fees.approve_refund, req.id, DecisionIn(), expected_version=None)

    out = _issue(school, student)
    assert out.outcome == "issued"
    assert out.financial_year == FY
    assert out.paid == D("1600.00")  # exam fee and the cancelled receipt left out
    assert out.refunded == D("100.00")
    assert out.amount == D("1500.00")
    assert [r.receipt_number for r in out.receipts] == [first.receipt_number, second.receipt_number]
    assert [r.amount for r in out.receipts] == [D("1000.00"), D("600.00")]
    assert out.receipts[1].refunded == D("100.00")
    assert "Tuition fee" in out.head_names
    assert "Exam fee" not in out.head_names
    assert out.serial_number.startswith("FTC/2026-27/")
    assert out.legal_verified is False
    assert out.pdf_status == "queued"
    # Audit in the same transaction, ids and amounts only (FR-FEE-025).
    events = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "fee.tax_certificate.issued")
        if e["resource_id"] == out.id
    ]
    assert len(events) == 1
    summary = events[0]["summary"]
    assert summary["serial_number"] == out.serial_number
    assert summary["amount"] == "1500.00"
    assert "name" not in str(summary).lower()
    # The PDF job is queued in the outbox.
    queued = F.scalar(
        admin_engine,
        "SELECT count(*) FROM ops.outbox WHERE event_type = 'fee.tax_certificate.issued' "
        "AND payload->>'certificate_id' = :i",
        i=str(out.id),
    )
    assert queued == 1


def test_FR_FEE_052_reissue_is_unchanged_or_supersedes(school: Any, admin_engine: Engine) -> None:
    student, account = _student_with_account(school)
    _pay(school, account, "700.00")
    first = _issue(school, student)
    again = _issue(school, student, role="accountant")
    assert again.outcome == "unchanged"
    assert again.id == first.id
    assert again.serial_number == first.serial_number
    _pay(school, account, "300.00")
    newer = _issue(school, student)
    assert newer.outcome == "reissued"
    assert newer.id != first.id
    assert newer.amount == D("1000.00")
    assert newer.serial_number != first.serial_number
    old = F.call(school, "accountant", fees.get_tax_certificate, first.id)
    assert old.status == "superseded"
    assert old.superseded_by == newer.id
    page = F.call(school, "accountant", fees.tax_certificate_print_page, first.id)
    assert f"SUPERSEDED by certificate {newer.serial_number}" in page
    # Serials are gap-free in the year (FR-FEE-051).
    numbers = sorted(
        int(n.rsplit("/", 1)[1])
        for (n,) in admin_engine.connect().execute(
            text(
                "SELECT serial_number FROM fin.tax_certificates WHERE tenant_id = :t "
                "AND financial_year = :y"
            ),
            {"t": school.tenant_id, "y": FY},
        )
    )
    assert numbers == list(range(1, len(numbers) + 1))


def test_FR_FEE_049_nothing_to_certify_and_unknown_students(school: Any) -> None:
    student, account = _student_with_account(school)
    setup = F.ensure_setup(school)
    F.issue(
        school,
        account,
        "200.00",
        allocations=[AllocationIn(instalment_no=1, head_id=setup.exm, amount=D("200.00"))],
    )
    with pytest.raises(Conflict) as err:
        _issue(school, student)
    assert err.value.code == "no_tuition_paid"
    with pytest.raises(NotFound):
        _issue(school, uuid.uuid4())


@pytest.mark.parametrize(("year", "code"), [("2027-28", "not_started"), ("2015-16", "too_old")])
def test_FR_FEE_048_financial_year_must_have_started_and_be_kept(
    school: Any, year: str, code: str
) -> None:
    student, _ = _student_with_account(school)
    with pytest.raises(ValidationFailed) as err:
        F.call(
            school,
            "office_staff",
            fees.issue_tax_certificate,
            TaxCertificateIssue(student_id=student, financial_year=year),
        )
    assert err.value.errors[0]["code"] == code


def test_FR_FEE_050_bulk_for_a_section_skips_students_without_tuition(
    school: Any, admin_engine: Engine
) -> None:
    payer, account = _student_with_account(school)
    _pay(school, account, "450.00")
    out = F.call(
        school,
        "accountant",
        fees.issue_tax_certificates_bulk,
        TaxCertificateBulkIssue(financial_year=FY, section_id=school.ids["section_9a"]),
    )
    assert out.students >= 1
    assert out.issued + out.reissued + out.unchanged == len(out.certificate_ids)
    assert out.skipped_no_tuition == out.students - len(out.certificate_ids)
    mine = F.call(school, "accountant", fees.list_tax_certificates, financial_year=FY,
                  student_id=payer, class_id=None, section_id=None, status="issued",
                  limit=10, cursor=None)  # fmt: skip
    assert [x.amount for x in mine.data] == [D("450.00")]
    # A second run changes nothing.
    again = F.call(
        school,
        "accountant",
        fees.issue_tax_certificates_bulk,
        TaxCertificateBulkIssue(financial_year=FY, section_id=school.ids["section_9a"]),
    )
    assert again.issued == 0
    assert again.reissued == 0
    assert again.unchanged == len(again.certificate_ids)
    assert W.audit_events(admin_engine, school.tenant_id, "fee.tax_certificate.bulk_issued")


def test_FR_FEE_050_bulk_skips_a_student_with_too_many_receipts(
    school: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One student with more receipts than a certificate lists never fails the whole class."""
    payer, account = _student_with_account(school)
    _pay(school, account, "100.00")
    _pay(school, account, "100.00")
    cfg = tax_config.load_config()
    tight = cfg.model_copy(update={"limits": cfg.limits.model_copy(update={"max_receipts": 1})})
    monkeypatch.setattr("app.fees.tax_certificates.load_config", lambda: tight)
    out = F.call(
        school,
        "accountant",
        fees.issue_tax_certificates_bulk,
        TaxCertificateBulkIssue(financial_year=FY, section_id=school.ids["section_9a"]),
    )
    assert out.skipped_too_many_receipts >= 1
    assert out.issued + out.reissued + out.unchanged == len(out.certificate_ids)
    mine = F.call(school, "accountant", fees.list_tax_certificates, financial_year=FY,
                  student_id=payer, class_id=None, section_id=None, status="issued",
                  limit=10, cursor=None)  # fmt: skip
    assert mine.data == []


def test_FR_FEE_052_issued_certificates_are_frozen(school: Any, admin_engine: Engine) -> None:
    student, account = _student_with_account(school)
    _pay(school, account, "100.00")
    out = _issue(school, student)
    with pytest.raises(DBAPIError, match="tax_certificates_frozen"), admin_engine.begin() as c:
        c.execute(text("UPDATE fin.tax_certificates SET amount = 1 WHERE id = :i"), {"i": out.id})
    # Never two issued certificates of one student and year (partial unique index).
    with pytest.raises(DBAPIError, match="tax_certificates_one_issued"), admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO fin.tax_certificates (id, tenant_id, student_id, financial_year, "
                "serial_no, serial_number, as_of, amount, receipt_count, content, content_sha256, "
                "basis_sha256, issued_by) SELECT gen_random_uuid(), tenant_id, student_id, "
                "financial_year, 99999, 'FTC/2026-27/X', as_of, amount, receipt_count, content, "
                "content_sha256, basis_sha256, issued_by FROM fin.tax_certificates WHERE id = :i"
            ),
            {"i": out.id},
        )


def test_FR_FEE_053_print_view_and_pdf(school: Any, admin_engine: Engine) -> None:
    student, account = _student_with_account(school)
    _pay(school, account, "1234.00")
    out = _issue(school, student)
    page = F.call(school, "auditor_readonly", fees.tax_certificate_print_page, out.id)
    assert "TUITION FEE CERTIFICATE" in page
    assert out.serial_number in page
    assert "1,234.00" in page
    assert "not tax advice" in page
    assert "<script" not in page
    renderer = F.FakeRenderer()
    status = fees.render_tax_certificate_pdf(
        school.tenant_id, out.id, school.people["office_staff"].user_id, renderer=renderer
    )
    assert status == "ready"
    assert len(renderer.pages) == 1
    again = F.call(school, "accountant", fees.get_tax_certificate, out.id)
    assert again.pdf_status == "ready"
    # Idempotent: a second run renders nothing.
    assert (
        fees.render_tax_certificate_pdf(
            school.tenant_id, out.id, school.people["office_staff"].user_id, renderer=renderer
        )
        == "ready"
    )
    assert len(renderer.pages) == 1
    assert W.audit_events(admin_engine, school.tenant_id, "fee.tax_certificate.pdf_stored")


# --- HTTP (FR-FEE-054: guards, school-wide permissions, other school 404) -------------------------


def test_FR_FEE_054_routes_allow_fee_roles_only(school: Any, api: Any) -> None:
    student, account = _student_with_account(school)
    _pay(school, account, "800.00")
    body = {"student_id": str(student), "financial_year": FY}
    denied = api.call(school.people["class_teacher"], "POST", BASE, json=body)
    assert denied.status_code == 403
    auditor = api.call(school.people["auditor_readonly"], "POST", BASE, json=body)
    assert auditor.status_code == 403  # finance.read reads, never issues
    made = api.call(school.people["office_staff"], "POST", BASE, json=body)
    assert made.status_code == 201, made.text
    cert = made.json()
    assert cert["amount"] == "800.00"
    assert made.headers["Location"].endswith(cert["id"])
    read = api.call(school.people["auditor_readonly"], "GET", f"{BASE}/{cert['id']}")
    assert read.status_code == 200
    page = api.call(school.people["accountant"], "GET", f"{BASE}/{cert['id']}/print")
    assert page.status_code == 200
    assert page.headers["Content-Security-Policy"].startswith("default-src 'none'")
    assert page.headers["Cache-Control"] == "no-store"
    listed = api.call(
        school.people["accountant"], "GET", f"{BASE}?financial_year={FY}&student_id={student}"
    )
    assert listed.status_code == 200
    assert [x["id"] for x in listed.json()["data"]] == [cert["id"]]
    teacher = api.call(school.people["teacher"], "GET", f"{BASE}/{cert['id']}")
    assert teacher.status_code == 403
    not_ready = api.call(school.people["accountant"], "GET", f"{BASE}/{cert['id']}/download-url")
    assert not_ready.status_code == 409
    assert not_ready.json()["code"] == "pdf_not_ready"
    bad = api.call(
        school.people["office_staff"],
        "POST",
        f"{BASE}/bulk",
        json={"financial_year": FY, "class_id": str(uuid.uuid4()), "section_id": str(uuid.uuid4())},
    )
    assert bad.status_code == 422


def test_FR_FEE_054_other_schools_certificate_is_404(
    school: Any, other_school: Any, api: Any
) -> None:
    student, account = _student_with_account(other_school)
    _pay(other_school, account, "300.00")
    theirs = _issue(other_school, student, role="accountant")
    for path in (f"{BASE}/{theirs.id}", f"{BASE}/{theirs.id}/print"):
        res = api.call(school.people["accountant"], "GET", path)
        assert res.status_code == 404, path
    # Another school's student cannot be certified here either.
    res = api.call(
        school.people["office_staff"],
        "POST",
        BASE,
        json={"student_id": str(student), "financial_year": FY},
    )
    assert res.status_code == 404


def test_invariant_5_issuing_logs_no_names_or_amounts(
    school: Any, api: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    name = "Kothapalli Synthetica Taxharsha"
    student = F.SW.create(school, name=name, section_key="section_9a", admission_no="LTX/1")
    account = F.account(school, student)
    _pay(school, account, "2321.00")
    capsys.readouterr()
    res = api.call(
        school.people["office_staff"],
        "POST",
        BASE,
        json={"student_id": str(student), "financial_year": FY},
    )
    assert res.status_code == 201, res.text
    page = api.call(school.people["accountant"], "GET", f"{BASE}/{res.json()['id']}/print")
    assert name in page.text
    out, err = capsys.readouterr()
    logged = out + err
    assert "Kothapalli" not in logged
    assert "2321" not in logged
    assert "LTX/1" not in logged
