"""Fee routes over HTTP: guards, school-wide permissions, other-school 404, step-up, idempotent
receipts, problem codes, print and CSV headers, audit in the same transaction (docs/09 "Fees";
FR-FEE-001, FR-FEE-010, FR-FEE-018..020, FR-FEE-024, FR-FEE-025; invariants 2, 3, 7)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db

F: Any = __import__("sys").modules["sos_test_fees_support"]
W = F.W


def _receipt_body(account_id: uuid.UUID, amount: str = "100.00") -> dict[str, Any]:
    return {"account_id": str(account_id), "amount": amount, "mode": "cash"}


def test_FR_FEE_001_hidden_module_answers_404_module_hidden(world: Any, api: Any) -> None:
    res = api.call(world.person("accountant"), "GET", "/api/v1/fees/heads")
    assert res.status_code == 404
    assert res.json()["code"] == "module_hidden"


def test_FR_FEE_010_receipt_over_http_is_idempotent(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    account_id = F.account(school)
    who = school.people["office_staff"]
    headers = {"Idempotency-Key": f"rcpt-{uuid.uuid4().hex}"}
    first = api.call(
        who, "POST", "/api/v1/fees/receipts", json=_receipt_body(account_id), headers=headers
    )
    assert first.status_code == 201, first.text
    again = api.call(
        who, "POST", "/api/v1/fees/receipts", json=_receipt_body(account_id), headers=headers
    )
    assert again.status_code == 201
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert again.json()["receipt_number"] == first.json()["receipt_number"]
    count = F.scalar(
        admin_engine,
        "SELECT count(*) FROM fin.receipts WHERE account_id = :a",
        a=account_id,
    )
    assert count == 1
    # Audit in the same transaction: the receipt event names the number, mode and amount only.
    events = W.audit_events(admin_engine, school.tenant_id, "fee.receipt.issued")
    mine = [e for e in events if e["resource_id"] == uuid.UUID(first.json()["id"])]
    assert len(mine) == 1
    summary = mine[0]["summary"]
    assert summary["receipt_number"] == first.json()["receipt_number"]
    assert summary["amount"] == "100.00"
    assert summary["mode"] == "cash"
    assert "name" not in str(summary).lower()


def test_FR_FEE_010_amount_is_exact_decimal_string(school: Any, api: Any) -> None:
    account_id = F.account(school)
    who = school.people["office_staff"]
    res = api.call(who, "POST", "/api/v1/fees/receipts", json=_receipt_body(account_id, "10.005"))
    assert res.status_code == 422
    ok = api.call(who, "POST", "/api/v1/fees/receipts", json=_receipt_body(account_id, "10.5"))
    assert ok.status_code == 201
    assert ok.json()["amount"] == "10.50"


def test_FR_FEE_024_teachers_have_no_fee_access(school: Any, api: Any) -> None:
    account_id = F.account(school)
    for role in ("class_teacher", "teacher"):
        res = api.call(school.people[role], "GET", f"/api/v1/fees/accounts/{account_id}")
        assert res.status_code == 403, (role, res.text)


def test_FR_FEE_024_section_scoped_collector_is_refused(school: Any, api: Any) -> None:
    account_id = F.account(school)
    who = school.people["scoped_collector"]
    assert (
        api.call(who, "POST", "/api/v1/fees/receipts", json=_receipt_body(account_id)).status_code
        == 403
    )
    assert api.call(who, "GET", f"/api/v1/fees/accounts/{account_id}").status_code == 403


def test_other_school_gets_404(school: Any, world: Any, api: Any, admin_engine: Engine) -> None:
    account_id = F.account(school)
    receipt = F.issue(school, account_id)
    with F.fees_on(admin_engine, world.b.tenant_id):
        owner_b = world.b.people["owner"]
        for path in (
            f"/api/v1/fees/receipts/{receipt.id}",
            f"/api/v1/fees/receipts/{receipt.id}/print",
            f"/api/v1/fees/accounts/{account_id}",
            f"/api/v1/fees/accounts/{account_id}/statement",
        ):
            res = api.call(owner_b, "GET", path)
            assert res.status_code == 404, (path, res.text)
            assert res.json()["code"] == "not_found"


def test_FR_FEE_012_approval_needs_step_up_and_another_person(school: Any, api: Any) -> None:
    receipt = F.issue(school, F.account(school), role="accountant")
    who = school.people["accountant"]
    req = api.call(
        who,
        "POST",
        f"/api/v1/fees/receipts/{receipt.id}/cancellation",
        json={"reason_code": "wrong_student", "reason": "Receipt made for the wrong child"},
    )
    assert req.status_code == 201, req.text
    cid = req.json()["id"]
    stale = api.call(
        school.people["principal"],
        "POST",
        f"/api/v1/fees/cancellations/{cid}/approve",
        json={},
        auth_age_s=301,
    )
    assert stale.status_code == 428
    own = api.call(who, "POST", f"/api/v1/fees/cancellations/{cid}/approve", json={})
    assert own.status_code == 403
    assert own.json()["code"] == "self_approval_forbidden"
    ok = api.call(
        school.people["principal"], "POST", f"/api/v1/fees/cancellations/{cid}/approve", json={}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved"


def test_print_view_is_script_free_with_csp(school: Any, api: Any) -> None:
    receipt = F.issue(school, F.account(school))
    res = api.call(school.people["accountant"], "GET", f"/api/v1/fees/receipts/{receipt.id}/print")
    assert res.status_code == 200
    assert "default-src 'none'" in res.headers["Content-Security-Policy"]
    assert res.headers["Cache-Control"] == "no-store"
    assert "<script" not in res.text


def test_FR_FEE_018_day_book_csv_needs_step_up(school: Any, api: Any) -> None:
    F.issue(school, F.account(school))
    who = school.people["accountant"]
    assert api.call(who, "GET", "/api/v1/fees/reports/daily.csv", auth_age_s=301).status_code == 428
    res = api.call(who, "GET", "/api/v1/fees/reports/daily.csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    assert (
        api.call(school.people["office_staff"], "GET", "/api/v1/fees/reports/daily.csv").status_code
        == 403
    )


def test_FR_FEE_020_dues_list_and_csv(school: Any, api: Any) -> None:
    F.account(school)
    who = school.people["accountant"]
    res = api.call(
        who, "GET", "/api/v1/fees/dues", params={"section_id": str(school.ids["section_9a"])}
    )
    assert res.status_code == 200
    assert res.json()["students"] >= 1
    csv = api.call(who, "GET", "/api/v1/fees/dues.csv")
    assert csv.status_code == 200
    assert "overdue" in csv.text


def test_status_says_today_and_fy(school: Any, api: Any) -> None:
    res = api.call(school.people["office_staff"], "GET", "/api/v1/fees/status")
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is True
    assert body["financial_year"] == "2026-27"


def test_approvals_inbox_marks_mine(school: Any, api: Any) -> None:
    receipt = F.issue(school, F.account(school), role="accountant")
    api.call(
        school.people["accountant"],
        "POST",
        f"/api/v1/fees/receipts/{receipt.id}/cancellation",
        json={"reason_code": "other", "reason": "Checking the approvals inbox"},
    )
    mine = api.call(school.people["accountant"], "GET", "/api/v1/fees/approvals").json()
    item = next(i for i in mine if i["receipt_id"] == str(receipt.id))
    assert item["mine"] is True
    assert item["can_decide"] is False
    theirs = api.call(school.people["principal"], "GET", "/api/v1/fees/approvals").json()
    item = next(i for i in theirs if i["receipt_id"] == str(receipt.id))
    assert item["mine"] is False
    assert item["can_decide"] is True
