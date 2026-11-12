"""Family counter: siblings and one payment across their accounts (FM-07; FR-FEE-101..112; D86).

Siblings are students who share a guardian on their records (``students.add_guardian`` with an
existing ``guardian_id``). A family payment issues one ordinary receipt per account in one
transaction (gap-free numbers, append-only ledger, the receipt design unchanged).
"""

from __future__ import annotations

import dataclasses
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine

from app.authz.context import Scopes
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.fees import family
from app.fees import service as fees
from app.fees.family_schemas import FamilyPaymentCreate, FamilyPaymentPart

pytestmark = pytest.mark.db

F: Any = __import__("sys").modules["sos_test_fees_support"]
W = F.W
SW = F.SW
D = Decimal
PAY = "/api/v1/fees/family-payments"


def _family(school: Any, *, size: int = 2, section_key: str = "section_9a") -> list[uuid.UUID]:
    """``size`` new students of 9A sharing one guardian (the first's father), with accounts."""
    kids = [F.student(school, section_key) for _ in range(size)]
    guardian = SW.add_guardian(school, kids[0], full_name="Synthetica Family Guardian")
    for kid in kids[1:]:
        SW.add_guardian(school, kid, guardian_id=guardian)
    return kids


def _accounts(school: Any, kids: list[uuid.UUID]) -> list[uuid.UUID]:
    return [F.account(school, kid) for kid in kids]


def _payment(accounts: list[uuid.UUID], amount: str = "500.00", **kw: Any) -> FamilyPaymentCreate:
    return FamilyPaymentCreate(
        mode=kw.pop("mode", "cash"),
        parts=[FamilyPaymentPart(account_id=a, amount=D(amount)) for a in accounts],
        **kw,
    )


# --- siblings (FR-FEE-101/102) -------------------------------------------------------------------


def test_FR_FEE_101_family_lists_siblings_sharing_a_guardian(school: Any) -> None:
    kids = _family(school, size=3)
    accounts = _accounts(school, kids)
    stranger = F.student(school)
    out = F.call(school, "office_staff", fees.family_counter, kids[0])
    ids = [m.student_id for m in out.members]
    assert ids[0] == kids[0]
    assert out.members[0].selected is True
    assert set(ids) == set(kids)
    assert stranger not in ids
    sibling = next(m for m in out.members if m.student_id == kids[1])
    assert sibling.linked_by == ["father"]
    assert sibling.accounts[0].account_id == accounts[1]
    assert sibling.collectable == sibling.accounts[0].collectable > 0
    assert out.total_collectable == sum((m.collectable for m in out.members), D("0"))
    assert out.modes == ["cash", "cheque", "bank_transfer"]


def test_FR_FEE_101_student_without_siblings_is_alone(school: Any) -> None:
    sid = F.student(school)
    out = F.call(school, "office_staff", fees.family_counter, sid)
    assert [m.student_id for m in out.members] == [sid]


def test_FR_FEE_102_sibling_outside_the_clerks_scope_is_not_shown(school: Any) -> None:
    kid_a = F.student(school, "section_9a")
    kid_c = F.student(school, "section_9c")
    guardian = SW.add_guardian(school, kid_a, full_name="Synthetica Split Guardian")
    SW.add_guardian(school, kid_c, guardian_id=guardian)
    person = school.people["office_staff"]
    base = F.ctx(school, person, "office_staff")
    narrow = dataclasses.replace(
        base,
        scopes=Scopes(school=False, section_ids=frozenset({school.ids["section_9a"]})),
        scoped_permissions=frozenset({"student.read_basic"}),
    )
    out = F.call(school, "office_staff", fees.family_counter, kid_a, as_ctx=narrow)
    assert [m.student_id for m in out.members] == [kid_a]
    with pytest.raises(NotFound):
        F.call(school, "office_staff", fees.family_counter, kid_c, as_ctx=narrow)


# --- one payment, one receipt per account (FR-FEE-103..106) --------------------------------------


def test_FR_FEE_103_one_payment_issues_one_receipt_per_child(
    school: Any, admin_engine: Engine
) -> None:
    kids = _family(school)
    accounts = _accounts(school, kids)
    out = F.call(school, "office_staff", fees.issue_family_payment, _payment(accounts, "700.00"))
    assert [r.account_id for r in out.receipts] == accounts
    assert [r.student_id for r in out.receipts] == kids
    assert out.amount == D("1400.00")
    numbers = [
        F.scalar(admin_engine, "SELECT receipt_no FROM fin.receipts WHERE id = :i", i=r.id)
        for r in out.receipts
    ]
    assert numbers[1] == numbers[0] + 1  # consecutive, one series (FR-FEE-010)
    for receipt, account in zip(out.receipts, accounts, strict=True):
        assert receipt.amount == D("700.00")
        paid = F.scalar(
            admin_engine,
            "SELECT -sum(amount) FROM fin.ledger_entries WHERE receipt_id = :r AND account_id = :a",
            r=receipt.id,
            a=account,
        )
        assert paid == D("700.00")
    # Audit in the same transaction: each receipt's own event and one family event.
    issued = W.audit_events(admin_engine, school.tenant_id, "fee.receipt.issued")
    assert {e["resource_id"] for e in issued} >= {r.id for r in out.receipts}
    family = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "fee.family_payment.issued")
        if e["resource_id"] == out.receipts[0].id
    ]
    assert len(family) == 1
    summary = family[0]["summary"]
    assert summary["receipts"] == 2
    assert summary["amount"] == "1400.00"
    assert summary["receipt_numbers"] == [r.receipt_number for r in out.receipts]
    assert "Synthetica" not in str(summary)


def test_FR_FEE_103_cheque_details_are_on_every_receipt(school: Any) -> None:
    accounts = _accounts(school, _family(school))
    out = F.call(
        school,
        "office_staff",
        fees.issue_family_payment,
        _payment(
            accounts,
            "100.00",
            mode="cheque",
            mode_details={
                "cheque_number": "123456",
                "bank_name": "Synthetic Bank",
                "cheque_date": F.today().isoformat(),
            },
        ),
    )
    assert {r.mode_details["cheque_number"] for r in out.receipts} == {"123456"}


def test_FR_FEE_104_upi_is_refused(school: Any) -> None:
    accounts = _accounts(school, _family(school))
    with pytest.raises(ValidationFailed) as err:
        F.call(school, "office_staff", fees.issue_family_payment, _payment(accounts, mode="upi_qr"))
    assert err.value.errors[0]["code"] == "upi_one_receipt_per_utr"


def test_FR_FEE_105_all_or_none_when_one_part_is_above_due(
    school: Any, admin_engine: Engine
) -> None:
    accounts = _accounts(school, _family(school))
    before = F.scalar(admin_engine, "SELECT count(*) FROM fin.receipts")
    data = FamilyPaymentCreate(
        mode="cash",
        parts=[
            FamilyPaymentPart(account_id=accounts[0], amount=D("100.00")),
            FamilyPaymentPart(account_id=accounts[1], amount=D("999999.00")),
        ],
    )
    with pytest.raises(Conflict) as err:
        F.call(school, "office_staff", fees.issue_family_payment, data)
    assert err.value.code == "amount_exceeds_due"
    assert F.scalar(admin_engine, "SELECT count(*) FROM fin.receipts") == before


def test_FR_FEE_106_accounts_of_another_family_are_refused(school: Any) -> None:
    mine = _accounts(school, _family(school))
    other = F.account(school, F.student(school))
    with pytest.raises(ValidationFailed) as err:
        F.call(school, "office_staff", fees.issue_family_payment, _payment([mine[0], other]))
    assert err.value.errors[0] == {
        "field": "parts.1.account_id",
        "code": "not_in_family",
        "message_key": "errors.not_in_family",
    }


def test_FR_FEE_106_duplicate_and_too_many_parts(school: Any) -> None:
    accounts = _accounts(school, _family(school))
    with pytest.raises(ValidationFailed) as err:
        F.call(school, "office_staff", fees.issue_family_payment, _payment([accounts[0]] * 2))
    assert err.value.errors[0]["code"] == "duplicate"
    many = [uuid.uuid4() for _ in range(family.load_config().max_accounts + 1)]
    with pytest.raises(ValidationFailed) as err:
        F.call(school, "office_staff", fees.issue_family_payment, _payment(many))
    assert err.value.errors[0]["code"] == "too_many"


# --- hand-over slip (FR-FEE-107) -----------------------------------------------------------------


def test_FR_FEE_107_slip_lists_the_receipts_and_total(school: Any, admin_engine: Engine) -> None:
    accounts = _accounts(school, _family(school))
    out = F.call(school, "office_staff", fees.issue_family_payment, _payment(accounts, "250.00"))
    page = F.call(school, "accountant", fees.family_slip, [r.id for r in out.receipts])
    for receipt in out.receipts:
        assert receipt.receipt_number in page
        assert receipt.student_name in page
    assert "500.00" in page
    assert "<script" not in page
    events = W.audit_events(admin_engine, school.tenant_id, "fee.family_payment.slip_viewed")
    assert any(e["resource_id"] == out.receipts[0].id for e in events)


# --- over HTTP: guards, other school, idempotency, print headers (FR-FEE-108..110) ---------------


def test_FR_FEE_108_family_authz(school: Any, other_school: Any, api: Any) -> None:
    kids = _family(school)
    path = f"/api/v1/fees/students/{kids[0]}/family"
    assert api.call(school.people["office_staff"], "GET", path).status_code == 200
    assert api.call(school.people["accountant"], "GET", path).status_code == 200
    assert api.call(school.people["class_teacher"], "GET", path).status_code == 403
    assert api.call(school.people["scoped_collector"], "GET", path).status_code == 403
    F.ensure_setup(other_school)
    assert api.call(other_school.people["accountant"], "GET", path).status_code == 404


def test_FR_FEE_109_family_payment_over_http(
    school: Any, other_school: Any, api: Any, admin_engine: Engine
) -> None:
    accounts = _accounts(school, _family(school))
    body = {
        "mode": "cash",
        "parts": [{"account_id": str(a), "amount": "10.00"} for a in accounts],
    }
    key = {"Idempotency-Key": uuid.uuid4().hex}
    staff = school.people["office_staff"]
    assert (
        api.call(school.people["class_teacher"], "POST", PAY, json=body, headers=key).status_code
        == 403
    )
    assert (
        api.call(school.people["auditor_readonly"], "POST", PAY, json=body, headers=key).status_code
        == 403
    )
    first = api.call(staff, "POST", PAY, json=body, headers=key)
    assert first.status_code == 201, first.text
    again = api.call(staff, "POST", PAY, json=body, headers=key)
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert [r["receipt_number"] for r in again.json()["receipts"]] == [
        r["receipt_number"] for r in first.json()["receipts"]
    ]
    count = F.scalar(
        admin_engine,
        "SELECT count(*) FROM fin.receipts WHERE account_id = ANY(:a)",
        a=accounts,
    )
    assert count == 2
    upi = {**body, "mode": "upi_qr"}
    res = api.call(staff, "POST", PAY, json=upi, headers={"Idempotency-Key": uuid.uuid4().hex})
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "upi_one_receipt_per_utr"
    F.ensure_setup(other_school)
    res = api.call(
        other_school.people["office_staff"],
        "POST",
        PAY,
        json=body,
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert res.status_code == 404


def test_FR_FEE_110_slip_print_headers(school: Any, api: Any) -> None:
    accounts = _accounts(school, _family(school))
    out = F.call(school, "office_staff", fees.issue_family_payment, _payment(accounts, "5.00"))
    query = "&".join(f"receipt_ids={r.id}" for r in out.receipts)
    res = api.call(school.people["office_staff"], "GET", f"{PAY}/slip?{query}")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "style-src 'sha256-" in res.headers["content-security-policy"]
    assert res.headers["cache-control"] == "no-store"
    assert api.call(school.people["class_teacher"], "GET", f"{PAY}/slip?{query}").status_code == 403


def test_FR_FEE_111_family_calls_do_not_log_personal_data(
    school: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    name = "Kothapalli Synthetica Familyrani"
    kids = [
        SW.create(school, name=name, section_key="section_9a", admission_no=f"LRFAM/{n}")
        for n in (1, 2)
    ]
    guardian = SW.add_guardian(school, kids[0], full_name="Kothapalli Synthetica Guardian")
    SW.add_guardian(school, kids[1], guardian_id=guardian)
    accounts = _accounts(school, kids)
    capsys.readouterr()
    staff = school.people["office_staff"]
    assert api.call(staff, "GET", f"/api/v1/fees/students/{kids[0]}/family").status_code == 200
    res = api.call(
        staff,
        "POST",
        PAY,
        json={
            "mode": "cheque",
            "mode_details": {
                "cheque_number": "654321",
                "bank_name": "Kothapalli Cooperative Bank",
                "cheque_date": str(F.today()),
            },
            "parts": [{"account_id": str(a), "amount": "20.00"} for a in accounts],
        },
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert res.status_code == 201, res.text
    query = "&".join(f"receipt_ids={r['id']}" for r in res.json()["receipts"])
    assert api.call(staff, "GET", f"{PAY}/slip?{query}").status_code == 200
    out = capsys.readouterr()
    logs = out.out + out.err
    for secret in ("Kothapalli", "Familyrani", "654321"):
        assert secret not in logs
    events = W.audit_events(admin_engine, school.tenant_id, "fee.family_payment.issued")
    assert all("Kothapalli" not in str(e["summary"]) for e in events)
