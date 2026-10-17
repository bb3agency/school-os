"""UPI and bank statement routes over HTTP: guards, school-wide permissions, other-school 404,
hidden module, QR headers, statement import and confirmation, problem codes, and no personal data
in logs or audit summaries (docs/09 "Fees"; US-2109; FR-FEE-014..017, FR-FEE-024, FR-FEE-025;
invariants 2, 3, 5, 7)."""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db

F: Any = __import__("sys").modules["sos_test_fees_support"]
U: Any = __import__("sys").modules["sos_test_fees_upi_support"]
W = F.W


def _amount() -> str:
    return f"{1000 + secrets.randbelow(5000)}.{secrets.randbelow(100):02d}"


def _import(api: Any, school: Any, admin: Engine, credits: list[Any]) -> Any:
    doc = U.upload(admin, school, U.statement("hdfc", credits))
    res = api.call(
        school.people["accountant"],
        "POST",
        "/api/v1/fees/bank-statements",
        json={"document_id": str(doc), "preset": "hdfc"},
    )
    assert res.status_code == 201, res.text
    assert res.headers["Location"] == f"/api/v1/fees/bank-statements/{res.json()['id']}"
    return res.json()


def test_FR_FEE_001_upi_routes_are_hidden_while_fees_are_off(world: Any, api: Any) -> None:
    for method, path in (
        ("GET", "/api/v1/fees/upi-settings"),
        ("GET", "/api/v1/fees/upi-exceptions"),
        ("GET", "/api/v1/fees/bank-statements"),
    ):
        res = api.call(world.person("accountant"), method, path)
        assert res.status_code == 404, (path, res.text)
        assert res.json()["code"] == "module_hidden"


def test_US_2109_AC1_settings_and_qr_over_http(school: Any, api: Any) -> None:
    accountant, staff = school.people["accountant"], school.people["office_staff"]
    got = api.call(staff, "GET", "/api/v1/fees/upi-settings")
    assert got.status_code == 200
    headers = {"If-Match": got.headers["ETag"]} if got.json()["version"] else {}
    put = api.call(
        accountant,
        "PUT",
        "/api/v1/fees/upi-settings",
        json={"payee_name": "Synthetic Public School", "vpa": "synthetic.school@sbi"},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    stale = api.call(
        accountant,
        "PUT",
        "/api/v1/fees/upi-settings",
        json={"payee_name": "Synthetic Public School", "vpa": "synthetic.school@sbi"},
        headers={"If-Match": 'W/"999"'},
    )
    assert stale.status_code == 412
    bad = api.call(
        accountant,
        "PUT",
        "/api/v1/fees/upi-settings",
        json={"payee_name": "Synthetic", "vpa": "not a upi id"},
    )
    assert bad.status_code == 422
    assert api.call(staff, "PUT", "/api/v1/fees/upi-settings", json={}).status_code == 403
    qr = api.call(staff, "GET", "/api/v1/fees/upi-settings/qr.svg")
    assert qr.status_code == 200
    assert qr.headers["content-type"].startswith("image/svg+xml")
    assert qr.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in qr.headers["content-security-policy"]
    assert qr.text.startswith("<svg")
    for role in ("class_teacher", "teacher"):
        res = api.call(school.people[role], "GET", "/api/v1/fees/upi-settings/qr.svg")
        assert res.status_code == 403, role


def test_FR_FEE_014_utr_check_travels_in_the_body(school: Any, api: Any) -> None:
    res = api.call(
        school.people["office_staff"], "POST", "/api/v1/fees/upi/utr-check", json={"utr": "123"}
    )
    assert res.status_code == 200
    assert res.json() == {
        "state": "invalid",
        "receipt_id": None,
        "receipt_number": None,
        "seen_in_bank": False,
    }


def test_US_2109_AC2_import_review_and_confirm_over_http(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    amount = _amount()
    receipt = U.upi_receipt(school, amount)
    stmt = _import(api, school, admin_engine, [U.Credit(U.today(), amount, receipt.utr)])
    accountant = school.people["accountant"]
    detail = api.call(accountant, "GET", f"/api/v1/fees/bank-statements/{stmt['id']}")
    assert detail.status_code == 200
    (line,) = detail.json()["lines"]
    assert line["match_status"] == "proposed"
    assert line["amount"] == amount
    proposed = api.call(
        accountant,
        "GET",
        f"/api/v1/fees/bank-statements/{stmt['id']}",
        params={"status": "ignored"},
    )
    assert proposed.json()["lines"] == []
    done = api.call(
        accountant,
        "POST",
        f"/api/v1/fees/bank-statements/{stmt['id']}/confirm",
        json={"line_ids": [line["id"]]},
    )
    assert done.status_code == 200, done.text
    assert done.json()["confirmed"] == 1
    receipt_view = api.call(accountant, "GET", f"/api/v1/fees/receipts/{receipt.id}")
    assert receipt_view.json()["bank_confirmed_at"] is not None
    listing = api.call(accountant, "GET", "/api/v1/fees/bank-statements")
    assert stmt["id"] in {s["id"] for s in listing.json()}
    presets = api.call(accountant, "GET", "/api/v1/fees/bank-statement-presets")
    assert "hdfc" in {p["preset"] for p in presets.json()}
    report = api.call(school.people["auditor_readonly"], "GET", "/api/v1/fees/reports/upi")
    assert report.status_code == 200
    assert api.call(accountant, "POST", "/api/v1/fees/bank-statements/rematch").status_code == 200


def test_FR_FEE_024_reconciliation_is_for_fee_reconcile_holders_only(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    stmt = _import(api, school, admin_engine, [U.Credit(U.today(), _amount(), upi=False)])
    for role in ("office_staff", "office_admin", "auditor_readonly", "class_teacher", "teacher"):
        who = school.people[role]
        assert api.call(who, "GET", f"/api/v1/fees/bank-statements/{stmt['id']}").status_code == 403
        assert api.call(who, "GET", "/api/v1/fees/upi-exceptions").status_code == 403
    # A section-scoped custom role never reaches fee data (FR-FEE-024).
    res = api.call(school.people["scoped_collector"], "POST", "/api/v1/fees/upi/utr-check",
                   json={"utr": "123456789012"})  # fmt: skip
    assert res.status_code == 403


def test_INV_3_other_school_statement_and_line_ids_are_404(
    school: Any, world: Any, api: Any, admin_engine: Engine
) -> None:
    stmt = _import(api, school, admin_engine, [U.Credit(U.today(), _amount(), upi=False)])
    with admin_engine.connect() as c:
        line_id: object = c.execute(
            text("SELECT id FROM fin.bank_statement_lines WHERE import_id = :i"),
            {"i": uuid.UUID(stmt["id"])},
        ).scalar_one()
    with F.fees_on(admin_engine, world.a.tenant_id):
        other = world.person("principal")
        statement = f"/api/v1/fees/bank-statements/{stmt['id']}"
        line = f"/api/v1/fees/bank-statement-lines/{line_id}"
        for method, path, body in (
            ("GET", statement, None),
            ("POST", f"{statement}/confirm", {"line_ids": [str(line_id)]}),
            ("POST", f"{line}/dismiss", {"reason": "other"}),
            ("POST", f"{line}/confirm", {"receipt_id": str(uuid.uuid4())}),
        ):
            res = api.call(other, method, path, json=body)
            assert res.status_code == 404, (path, res.text)


def test_FR_FEE_025_upi_calls_do_not_log_personal_data(
    school: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    amount = _amount()
    receipt = U.upi_receipt(school, amount)
    capsys.readouterr()
    stmt = _import(
        api,
        school,
        admin_engine,
        [U.Credit(U.today(), amount, receipt.utr), U.Credit(U.today(), _amount(), upi=False)],
    )
    accountant = school.people["accountant"]
    detail = api.call(accountant, "GET", f"/api/v1/fees/bank-statements/{stmt['id']}").json()
    api.call(
        accountant,
        "POST",
        f"/api/v1/fees/bank-statements/{stmt['id']}/confirm",
        json={"line_ids": [x["id"] for x in detail["lines"]]},
    )
    api.call(school.people["office_staff"], "POST", "/api/v1/fees/upi/utr-check",
             json={"utr": receipt.utr})  # fmt: skip
    api.call(accountant, "GET", "/api/v1/fees/upi-exceptions")
    out = capsys.readouterr()
    logs = out.out + out.err
    for secret in (receipt.utr, U.PAYER.upper(), U.PAYER, U.ACCOUNT_NO, U.PAYER_VPA, "synthetica"):
        assert secret not in logs, secret
    with admin_engine.connect() as c:
        summaries = " ".join(
            str(row[0])
            for row in c.execute(
                text(
                    "SELECT summary FROM audit.events WHERE tenant_id = :t AND "
                    "(action LIKE 'fee.%' OR action LIKE 'document.%')"
                ),
                {"t": school.tenant_id},
            ).all()
        )
    for secret in (receipt.utr, U.PAYER.upper(), U.ACCOUNT_NO, U.PAYER_VPA):
        assert secret not in summaries, secret
