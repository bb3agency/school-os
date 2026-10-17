"""No personal data in logs from fees (invariant 5, FR-FEE-025, docs/12 §4.6).

Student names, UTRs, bank references, cheque numbers, reasons and amounts-with-names pass through
the fee routes while stdout/stderr are captured; none may appear in the log lines, nor in the
audit summaries of the school (ids, codes, numbers and counts only).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.redaction import verhoeff_check_digit

pytestmark = pytest.mark.db

F: Any = __import__("sys").modules["sos_test_fees_support"]

NAME = "Kothapalli Synthetica Feeharsha"
REASON = "Kothapalli family asked for the concession"
BANK = "Kothapalli Cooperative Bank"
REFERENCE = "KOTHAREF1234567"


def test_FR_FEE_025_fee_calls_do_not_log_personal_data(
    school: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "78901234567"
    aadhaar = body + verhoeff_check_digit(body)
    sid = F.SW.create(school, name=NAME, section_key="section_9a", admission_no="LRF/1")
    account_id = F.account(school, sid)
    setup = F.ensure_setup(school)
    utr = F.utr()
    capsys.readouterr()
    staff, accountant = school.people["office_staff"], school.people["accountant"]
    receipts = []
    for payload in (
        {"mode": "upi_qr", "utr": utr},
        {"mode": "bank_transfer", "mode_details": {"reference": REFERENCE}},
        {
            "mode": "cheque",
            "mode_details": {
                "cheque_number": "123987",
                "bank_name": BANK,
                "cheque_date": str(F.today()),
            },
        },
    ):
        res = api.call(
            staff,
            "POST",
            "/api/v1/fees/receipts",
            json={"account_id": str(account_id), "amount": "100.00", **payload},
        )
        assert res.status_code == 201, res.text
        receipts.append(res.json())
    # Refused Aadhaar in a free-text field must not be echoed into logs either.
    api.call(
        staff,
        "POST",
        "/api/v1/fees/receipts",
        json={
            "account_id": str(account_id),
            "amount": "1.00",
            "mode": "cheque",
            "mode_details": {
                "cheque_number": "123988",
                "bank_name": f"{BANK} {aadhaar}",
                "cheque_date": str(F.today()),
            },
        },
    )
    api.call(
        accountant,
        "POST",
        f"/api/v1/fees/accounts/{account_id}/concessions",
        json={
            "concession_type": "hardship",
            "basis": "amount",
            "value": "10.00",
            "targets": [{"instalment_no": 2, "head_id": str(setup.tui)}],
            "reason": REASON,
        },
    )
    api.call(
        accountant,
        "POST",
        "/api/v1/fees/accounts/search",
        json={"query": NAME.split()[1]},
    )
    for r in receipts:
        api.call(accountant, "GET", f"/api/v1/fees/receipts/{r['id']}/print")
    api.call(accountant, "GET", f"/api/v1/fees/accounts/{account_id}/statement")
    api.call(accountant, "GET", "/api/v1/fees/reports/daily.csv")
    api.call(accountant, "GET", "/api/v1/fees/dues.csv")
    out = capsys.readouterr()
    logs = out.out + out.err
    for secret in (NAME, "Kothapalli", REASON, BANK, REFERENCE, utr, "123987", aadhaar):
        assert secret not in logs, secret
    with admin_engine.connect() as c:
        from sqlalchemy import text

        summaries = " ".join(
            str(row[0])
            for row in c.execute(
                text(
                    "SELECT summary FROM audit.events WHERE tenant_id = :t AND action LIKE 'fee.%'"
                ),
                {"t": school.tenant_id},
            ).all()
        )
    for secret in (NAME, "Kothapalli", utr, REFERENCE, "123987"):
        assert secret not in summaries, secret
