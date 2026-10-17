"""UPI settings, bank statements, proposals, confirmations and exceptions through the real services
(R2-2E; US-2109 AC1-AC3; FR-FEE-014..017, FR-FEE-025, FR-FEE-026; ADR-0042 decision 9; owner
decision D19; invariants 1, 4, 6, 7). Synthetic statements only (invariant 11)."""

from __future__ import annotations

import datetime as dt
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.core.db import tenant_session
from app.core.errors import Conflict, Forbidden, NotFound, PreconditionFailed, ValidationFailed
from app.fees import _common
from app.fees import service as fees
from app.fees.schemas import CancellationRequest, DecisionIn
from app.fees.upi_schemas import (
    ConfirmIn,
    IgnoreIn,
    MatchIn,
    StatementImportIn,
    UpiSettingsIn,
)
from app.fees.upi_tasks import morning_for_school

pytestmark = pytest.mark.db

F: Any = __import__("sys").modules["sos_test_fees_support"]
U: Any = __import__("sys").modules["sos_test_fees_upi_support"]
W = F.W


def _amount() -> str:
    """A rupee amount no other test of the session uses (receipts and credits are matched on it)."""
    return f"{1000 + secrets.randbelow(5000)}.{secrets.randbelow(100):02d}"


def _import(
    school: Any,
    admin: Engine,
    credits: list[Any],
    *,
    preset: str = "generic",
    kind: str = "csv",
    role: str = "accountant",
) -> tuple[Any, uuid.UUID]:
    doc = U.upload(admin, school, U.statement(preset, credits, kind=kind), role=role, kind=kind)
    ctx = F.ctx(school, school.people[role], role)
    return fees.import_statement(ctx, StatementImportIn(document_id=doc)), doc


def _lines(school: Any, import_id: uuid.UUID) -> list[Any]:
    out = F.call(school, "accountant", fees.get_statement, import_id)
    return list(out.lines)


def _receipt(school: Any, receipt_id: uuid.UUID) -> Any:
    return F.call(school, "accountant", fees.get_receipt, receipt_id)


def _cancel(school: Any, receipt_id: uuid.UUID) -> None:
    req = F.call(
        school,
        "office_staff",
        fees.request_cancellation,
        receipt_id,
        CancellationRequest(reason_code="wrong_student", reason=F.REASON),
    )
    F.call(
        school, "principal", fees.approve_cancellation, req.id, DecisionIn(), expected_version=None
    )


def _today() -> dt.date:
    value: dt.date = U.today()
    return value


# --- settings and QR (FR-FEE-015, US-2109 AC1) ---------------------------------------------------


def test_US_2109_AC1_upi_settings_roundtrip_audit_and_qr(school: Any, admin_engine: Engine) -> None:
    current = F.call(school, "office_staff", fees.get_upi_settings)
    body = UpiSettingsIn(
        payee_name="Synthetic Public School", vpa="synthetic.school@sbi", confirm_within_days=7
    )
    out = F.call(
        school,
        "accountant",
        fees.put_upi_settings,
        body,
        expected_version=current.version or None,
    )
    assert out.configured
    assert out.upi_uri == "upi://pay?pa=synthetic.school@sbi&pn=Synthetic%20Public%20School&cu=INR"
    with pytest.raises(PreconditionFailed):
        F.call(school, "accountant", fees.put_upi_settings, body, expected_version=out.version + 9)
    # The counter (fee.collect) reads it and the QR; only fee.manage changes it.
    seen = F.call(school, "office_staff", fees.get_upi_settings)
    assert seen.vpa == "synthetic.school@sbi"
    svg = F.call(school, "office_staff", fees.upi_qr_svg)
    assert svg.startswith("<svg")
    assert "Synthetic Public School" in svg
    with pytest.raises(Forbidden):
        F.call(school, "office_staff", fees.put_upi_settings, body, expected_version=None)
    for role in ("class_teacher", "teacher"):
        with pytest.raises(Forbidden):
            F.call(school, role, fees.get_upi_settings)
    events = W.audit_events(admin_engine, school.tenant_id, "fee.upi.settings_updated")
    assert events
    assert "synthetic.school" not in str(events[-1]["summary"])
    with tenant_session(school.tenant_id) as s:
        payee = fees.upi_payee(s)
    assert payee is not None
    assert payee[1] == "synthetic.school@sbi"


def test_FR_FEE_015_bad_upi_id_is_refused_by_shape() -> None:
    with pytest.raises(ValueError, match=r"pattern"):
        UpiSettingsIn(payee_name="Synthetic", vpa="no-at-sign")


# --- the counter's UTR check (FR-FEE-014) --------------------------------------------------------


def test_FR_FEE_014_utr_check_at_the_counter(school: Any, admin_engine: Engine) -> None:
    assert F.call(school, "office_staff", fees.check_utr, "12345").state == "invalid"
    utr = F.utr()
    assert F.call(school, "office_staff", fees.check_utr, utr).state == "free"
    receipt = U.upi_receipt(school, _amount(), utr=utr)
    held = F.call(school, "office_staff", fees.check_utr, f" {utr} ")
    assert (held.state, held.receipt_id, held.receipt_number) == (
        "in_use",
        receipt.id,
        receipt.receipt_number,
    )
    assert held.seen_in_bank is False
    _import(school, admin_engine, [U.Credit(_today(), str(receipt.amount), utr)])
    assert F.call(school, "office_staff", fees.check_utr, utr).seen_in_bank is True
    with pytest.raises(Forbidden):
        F.call(school, "auditor_readonly", fees.check_utr, utr)


# --- import: parse then delete (FR-FEE-016, US-2109 AC2) -----------------------------------------


def test_US_2109_AC2_statement_is_read_deleted_and_audited(
    school: Any, admin_engine: Engine
) -> None:
    utr = F.utr()
    credits = [
        U.Credit(_today(), _amount(), utr),
        U.Credit(_today(), "99.00", debit=True),
        U.Credit(_today(), _amount(), upi=False),
    ]
    data = U.statement("sbi", credits, kind="xlsx")
    doc = U.upload(admin_engine, school, data, kind="xlsx")
    ctx = F.ctx(school, school.people["accountant"], "accountant")
    out = fees.import_statement(ctx, StatementImportIn(document_id=doc))
    assert (out.preset, out.file_kind, out.line_count, out.credit_count) == (
        "sbi",
        "xlsx",
        3,
        2,
    )
    assert out.file_deleted_at is not None
    assert not U.document_exists(admin_engine, doc), "the statement file is deleted once read"
    read = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "fee.statement.read")
        if e["resource_id"] == doc
    ]
    assert len(read) == 1
    assert set(read[0]["summary"]) == {"file_kind", "size_bytes"}
    imported = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "fee.statement.imported")
        if e["resource_id"] == out.id
    ]
    assert len(imported) == 1
    assert imported[0]["summary"]["credits"] == 2
    # Narrations are encrypted at rest; no account number, payer name or UPI ID in clear text.
    with admin_engine.connect() as c:
        rows = (
            c.execute(
                text("SELECT * FROM fin.bank_statement_lines WHERE import_id = :i"), {"i": out.id}
            )
            .mappings()
            .all()
        )
    assert len(rows) == 2
    raw = " ".join(str(dict(r)) for r in rows)
    for secret in (U.ACCOUNT_NO, U.PAYER.upper(), U.PAYER_VPA):
        assert secret not in raw
    assert all(r["narration_ciphertext"] is not None for r in rows)
    lines = _lines(school, out.id)
    assert lines[0].narration is not None
    assert U.PAYER.upper() in lines[0].narration
    assert U.ACCOUNT_NO not in str([x.model_dump() for x in lines])
    # The same file again is refused, and that upload is consumed too.
    again = U.upload(admin_engine, school, data, kind="xlsx")
    with pytest.raises(Conflict) as err:
        fees.import_statement(ctx, StatementImportIn(document_id=again))
    assert err.value.code == "statement_already_imported"
    assert not U.document_exists(admin_engine, again)


def test_FR_FEE_016_unreadable_statement_is_refused_and_still_deleted(
    school: Any, admin_engine: Engine
) -> None:
    table = U.rows("generic", [U.Credit(_today(), "10.00", F.utr())])
    table[5][4] = "=1+1"
    doc = U.upload(admin_engine, school, U.csv_bytes(table))
    ctx = F.ctx(school, school.people["accountant"], "accountant")
    with pytest.raises(ValidationFailed) as err:
        fees.import_statement(ctx, StatementImportIn(document_id=doc))
    assert err.value.errors[0]["code"] == "formula_cell"
    assert not U.document_exists(admin_engine, doc)


def test_FR_FEE_016_someone_elses_upload_is_not_read(school: Any, admin_engine: Engine) -> None:
    doc = U.upload(
        admin_engine, school, U.statement("generic", [U.Credit(_today(), "10.00", F.utr())])
    )
    ctx = F.ctx(school, school.people["accountant2"], "accountant")
    with pytest.raises(NotFound):
        fees.import_statement(ctx, StatementImportIn(document_id=doc))
    assert U.document_exists(admin_engine, doc), "another person's upload is left alone"
    staff = F.ctx(school, school.people["office_staff"], "office_staff")
    with pytest.raises(Forbidden):
        fees.import_statement(staff, StatementImportIn(document_id=doc))


def test_FR_FEE_016_overlapping_statements_store_each_credit_once(
    school: Any, admin_engine: Engine
) -> None:
    shared = U.Credit(_today() - dt.timedelta(days=1), _amount(), F.utr())
    first, _ = _import(school, admin_engine, [shared])
    second, _ = _import(school, admin_engine, [shared, U.Credit(_today(), _amount(), F.utr())])
    assert (first.credit_count, first.duplicate_count) == (1, 0)
    assert (second.credit_count, second.duplicate_count) == (2, 1)
    assert len(_lines(school, second.id)) == 1


# --- proposals, confirmations, exceptions (FR-FEE-016/017, US-2109 AC2/AC3) -----------------------


def test_US_2109_AC2_exact_utr_is_proposed_and_confirmed_once(
    school: Any, admin_engine: Engine
) -> None:
    amount = _amount()
    receipt = U.upi_receipt(school, amount)
    before = _receipt(school, receipt.id)
    assert before.bank_confirmed_at is None, "not yet seen in a bank statement"
    out, _ = _import(school, admin_engine, [U.Credit(_today(), amount, receipt.utr)])
    assert out.proposed_count == 1
    (line,) = _lines(school, out.id)
    assert (line.match_status, line.match_kind, line.receipt.id) == (
        "proposed",
        "utr_exact",
        receipt.id,
    )
    # Proposed is not confirmed: nothing was posted.
    assert F.scalar(
        admin_engine, "SELECT count(*) FROM fin.upi_confirmations WHERE receipt_id = :r",
        r=receipt.id,
    ) == 0  # fmt: skip
    done = F.call(
        school, "accountant", fees.confirm_statement, out.id, ConfirmIn(line_ids=[line.id])
    )
    assert (done.confirmed, done.already_confirmed, done.skipped) == (1, 0, [])
    again = F.call(
        school, "accountant", fees.confirm_statement, out.id, ConfirmIn(line_ids=[line.id])
    )
    assert (again.confirmed, again.already_confirmed) == (0, 1), "confirming again is harmless"
    after = _receipt(school, receipt.id)
    assert after.bank_confirmed_at is not None
    # The receipt itself is never edited by a confirmation.
    assert (after.version, after.amount, after.utr, after.status, after.content_sha256) == (
        before.version,
        before.amount,
        before.utr,
        before.status,
        before.content_sha256,
    )
    assert F.scalar(
        admin_engine, "SELECT count(*) FROM fin.upi_confirmations WHERE receipt_id = :r",
        r=receipt.id,
    ) == 1  # fmt: skip
    events = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "fee.upi.confirmed")
        if e["resource_id"] == out.id
    ]
    assert [e["summary"]["confirmed"] for e in events] == [1, 0]


def test_FR_FEE_016_amount_mismatch_is_an_exception_never_a_correction(
    school: Any, admin_engine: Engine
) -> None:
    receipt = U.upi_receipt(school, "1500.00")
    before = _receipt(school, receipt.id)
    out, _ = _import(school, admin_engine, [U.Credit(_today(), "1400.00", receipt.utr)])
    (line,) = _lines(school, out.id)
    assert (line.match_status, line.exception) == ("unmatched", "amount_mismatch")
    assert line.conflict_receipt is not None
    assert line.conflict_receipt.id == receipt.id
    with pytest.raises(Conflict) as err:
        F.call(school, "accountant", fees.match_line, line.id, MatchIn(receipt_id=receipt.id))
    assert err.value.code == "amount_mismatch"
    after = _receipt(school, receipt.id)
    assert (after.version, after.amount, after.bank_confirmed_at) == (
        before.version,
        before.amount,
        None,
    )


def test_FR_FEE_016_different_utr_is_never_matched(school: Any, admin_engine: Engine) -> None:
    amount = _amount()
    receipt = U.upi_receipt(school, amount)
    out, _ = _import(school, admin_engine, [U.Credit(_today(), amount, F.utr())])
    (line,) = _lines(school, out.id)
    assert (line.match_status, line.exception) == ("unmatched", "no_receipt")
    with pytest.raises(Conflict) as err:
        F.call(school, "accountant", fees.match_line, line.id, MatchIn(receipt_id=receipt.id))
    assert err.value.code == "utr_differs"


def test_FR_FEE_016_amount_and_date_rule_for_credits_without_utr(
    school: Any, admin_engine: Engine
) -> None:
    single = _amount()
    receipt = U.upi_receipt(school, single)
    twice = _amount()
    U.upi_receipt(school, twice)
    U.upi_receipt(school, twice)
    out, _ = _import(
        school,
        admin_engine,
        [U.Credit(_today(), single, upi=False), U.Credit(_today(), twice, upi=False)],
    )
    lines = {str(x.amount): x for x in _lines(school, out.id)}
    proposed = lines[single]
    assert (proposed.match_status, proposed.match_kind) == ("proposed", "amount_date")
    assert proposed.receipt is not None
    assert proposed.receipt.id == receipt.id
    assert lines[twice].match_status == "unmatched", "two candidates: a person chooses"
    done = F.call(
        school, "accountant", fees.confirm_statement, out.id, ConfirmIn(line_ids=[proposed.id])
    )
    assert done.confirmed == 1


def test_FR_FEE_016_stale_proposal_is_skipped_when_the_receipt_was_cancelled(
    school: Any, admin_engine: Engine
) -> None:
    amount = _amount()
    receipt = U.upi_receipt(school, amount)
    out, _ = _import(school, admin_engine, [U.Credit(_today(), amount, receipt.utr)])
    (line,) = _lines(school, out.id)
    assert line.match_status == "proposed"
    _cancel(school, receipt.id)
    done = F.call(
        school, "accountant", fees.confirm_statement, out.id, ConfirmIn(line_ids=[line.id])
    )
    assert done.confirmed == 0
    assert [(s.line_id, s.code) for s in done.skipped] == [(line.id, "receipt_cancelled")]
    (line,) = _lines(school, out.id)
    assert (line.match_status, line.exception) == ("unmatched", "receipt_cancelled")


def test_FR_FEE_016_receipt_cancelled_after_confirmation_is_matched_again_by_a_person(
    school: Any, admin_engine: Engine
) -> None:
    amount = _amount()
    receipt = U.upi_receipt(school, amount)
    out, _ = _import(school, admin_engine, [U.Credit(_today(), amount, receipt.utr)])
    (line,) = _lines(school, out.id)
    F.call(school, "accountant", fees.confirm_statement, out.id, ConfirmIn(line_ids=[line.id]))
    _cancel(school, receipt.id)
    (line,) = _lines(school, out.id)
    assert (line.match_status, line.exception) == ("confirmed", "confirmed_receipt_cancelled")
    exceptions = F.call(school, "accountant", fees.upi_exceptions)
    assert line.id in {x.id for x in exceptions.lines}
    # The receipt is issued again (same UTR, the right student) and matched by a person.
    again = F.issue(school, F.account(school), amount, mode="upi_qr", utr=receipt.utr)
    matched = F.call(school, "accountant", fees.match_line, line.id, MatchIn(receipt_id=again.id))
    assert (matched.match_status, matched.receipt.id, matched.exception) == (
        "confirmed",
        again.id,
        None,
    )
    # Repeating the same match is harmless.
    same = F.call(school, "accountant", fees.match_line, line.id, MatchIn(receipt_id=again.id))
    assert same.version == matched.version


def test_FR_FEE_016_dismissed_credit_stays_dismissed(school: Any, admin_engine: Engine) -> None:
    out, _ = _import(school, admin_engine, [U.Credit(_today(), _amount(), upi=False)])
    (line,) = _lines(school, out.id)
    ignored = F.call(school, "accountant", fees.ignore_line, line.id, IgnoreIn(reason="not_a_fee"))
    assert (ignored.match_status, ignored.ignore_reason) == ("ignored", "not_a_fee")
    twice = F.call(school, "accountant", fees.ignore_line, line.id, IgnoreIn(reason="other"))
    assert twice.ignore_reason == "not_a_fee"
    events = W.audit_events(admin_engine, school.tenant_id, "fee.upi.line_ignored")
    assert [e["summary"]["reason"] for e in events if e["resource_id"] == line.id] == ["not_a_fee"]


def test_FR_FEE_016_rematch_proposes_receipts_issued_after_the_import(
    school: Any, admin_engine: Engine
) -> None:
    amount, utr = _amount(), F.utr()
    out, _ = _import(school, admin_engine, [U.Credit(_today(), amount, utr)])
    (line,) = _lines(school, out.id)
    assert (line.match_status, line.exception) == ("unmatched", "no_receipt")
    receipt = U.upi_receipt(school, amount, utr=utr)
    found = F.call(school, "accountant", fees.rematch)
    assert found.proposed >= 1
    (line,) = _lines(school, out.id)
    assert (line.match_status, line.receipt.id) == ("proposed", receipt.id)


def test_US_2109_AC3_unconfirmed_after_n_days_and_report(
    school: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = U.upi_receipt(school, _amount())
    days = F.call(school, "accountant", fees.get_upi_settings).confirm_within_days
    now = _common.now

    def later(session: Any) -> dt.datetime:
        return now(session) + dt.timedelta(days=days + 1)

    out = F.call(school, "accountant", fees.upi_exceptions)
    assert receipt.id not in {r.id for r in out.unconfirmed_receipts}
    monkeypatch.setattr(_common, "now", later)
    out = F.call(school, "accountant", fees.upi_exceptions)
    mine = [r for r in out.unconfirmed_receipts if r.id == receipt.id]
    assert len(mine) == 1
    assert mine[0].days_waiting == days + 1
    report = F.call(
        school, "auditor_readonly", fees.upi_report, date_from=receipt.receipt_date, date_to=None
    )
    assert report.unconfirmed_count >= 1
    assert report.overdue_count >= 1
    assert report.unconfirmed_total >= Decimal(str(receipt.amount))
    with pytest.raises(Forbidden):
        F.call(school, "office_staff", fees.upi_exceptions)


def test_FR_FEE_026_morning_notification_and_narration_erasure(
    school: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, _ = _import(school, admin_engine, [U.Credit(_today(), _amount(), upi=False)])
    first = morning_for_school(school.tenant_id)
    assert first["notified"] >= 1
    assert morning_for_school(school.tenant_id)["notified"] == 0, "once per day"
    rows = F.scalar(
        admin_engine,
        "SELECT count(*) FROM ops.notifications WHERE tenant_id = :t "
        "AND template_key = 'fee.upi_exceptions'",
        t=school.tenant_id,
    )
    assert rows >= 2, "every fee.reconcile holder (principal, accountants)"
    now = _common.now
    monkeypatch.setattr(_common, "now", lambda s: now(s) + dt.timedelta(days=91))
    morning_for_school(school.tenant_id)
    (line,) = _lines(school, out.id)
    assert (line.narration, line.narration_erased) == (None, True)


def test_FR_FEE_016_concurrent_confirmations_confirm_once(
    school: Any, admin_engine: Engine
) -> None:
    amount = _amount()
    receipt = U.upi_receipt(school, amount)
    out, _ = _import(school, admin_engine, [U.Credit(_today(), amount, receipt.utr)])
    (line,) = _lines(school, out.id)
    start = threading.Barrier(2)

    def one(role: str) -> tuple[int, int]:
        start.wait()
        res = F.call(school, role, fees.confirm_statement, out.id, ConfirmIn(line_ids=[line.id]))
        return res.confirmed, res.already_confirmed

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(one, ["accountant", "principal"]))
    assert results == [(0, 1), (1, 0)]
    assert F.scalar(
        admin_engine, "SELECT count(*) FROM fin.upi_confirmations WHERE receipt_id = :r",
        r=receipt.id,
    ) == 1  # fmt: skip


# --- database guards (as the test superuser: triggers still apply) -------------------------------


def _constraint(exc: DBAPIError) -> str:
    return str(getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or "")


def test_FR_FEE_016_database_refuses_corrections_and_edits(
    school: Any, admin_engine: Engine
) -> None:
    amount = _amount()
    receipt = U.upi_receipt(school, amount)
    other = U.upi_receipt(school, _amount())
    out, _ = _import(school, admin_engine, [U.Credit(_today(), amount, receipt.utr)])
    (line,) = _lines(school, out.id)
    member = school.people["accountant"].membership_id

    def refused(sql: str, **params: Any) -> str:
        with pytest.raises(DBAPIError) as err, admin_engine.begin() as c:
            c.execute(text(sql), params)
        return _constraint(err.value)

    insert = (
        "INSERT INTO fin.upi_confirmations (id, tenant_id, receipt_id, statement_line_id, "
        "match_kind, confirmed_by) VALUES (:i, :t, :r, :l, 'manual', :m)"
    )
    common = {"t": school.tenant_id, "l": line.id, "m": member}
    assert refused(insert, i=uuid.uuid4(), r=other.id, **common) in {
        "upi_confirmations_amount_differs",
        "upi_confirmations_utr_differs",
    }
    assert (
        refused("UPDATE fin.bank_statement_lines SET amount = amount + 1 WHERE id = :l", l=line.id)
        == "bank_statement_lines_frozen"
    )
    assert (
        refused("DELETE FROM fin.bank_statement_lines WHERE id = :l", l=line.id)
        == "bank_statement_lines_frozen"
    )
    assert (
        refused(
            "UPDATE fin.bank_statement_lines SET match_status = 'confirmed', decided_by = :m, "
            "decided_at = now() WHERE id = :l",
            l=line.id,
            m=member,
        )
        == "bank_statement_lines_frozen"
    ), "confirmed only with its confirmation row"
    assert (
        refused("UPDATE fin.bank_statement_imports SET line_count = 0 WHERE id = :i", i=out.id)
        == "bank_statement_imports_append_only"
    )
    F.call(school, "accountant", fees.confirm_statement, out.id, ConfirmIn(line_ids=[line.id]))
    assert (
        refused("DELETE FROM fin.upi_confirmations WHERE receipt_id = :r", r=receipt.id)
        == "upi_confirmations_append_only"
    )
    assert (
        refused(
            "UPDATE fin.bank_statement_lines SET match_status = 'unmatched', match_kind = NULL, "
            "receipt_id = NULL, decided_by = NULL, decided_at = NULL WHERE id = :l",
            l=line.id,
        )
        == "bank_statement_lines_frozen"
    )


def test_INV_1_statement_lines_are_invisible_to_another_school(
    school: Any, world: Any, admin_engine: Engine
) -> None:
    out, _ = _import(school, admin_engine, [U.Credit(_today(), _amount(), F.utr())])

    with tenant_session(world.b.tenant_id) as s:
        count = s.execute(
            text("SELECT count(*) FROM fin.bank_statement_lines WHERE import_id = :i"),
            {"i": out.id},
        ).scalar()
    assert count == 0
