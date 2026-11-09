"""Tally voucher export and ledger-vs-Tally check (FM-03; FR-TALLY-011..030; synthetic data).

Each test makes its own school (a day closes once per school), with fees on; the connector flag
only where a test says so. Receipts are issued and days closed through the fee module's service,
as at the counter.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any
from xml.dom import minidom

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.fees import service as fees
from app.fees.schemas import CancellationRequest, DecisionIn, ModeDetailsIn

from .conftest import T

pytestmark = pytest.mark.db

BASE = "/api/v1/tally/vouchers"
STUDENT = "Synthetica Voucher Kumari"


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


F = _load("sos_test_fees_support", Path(__file__).resolve().parents[1] / "fees" / "support.py")


def _school(admin: Engine, *, connector: bool = False) -> Any:
    F.install()
    school = T.fresh_school(admin, flag=connector)
    F.enable(admin, school.tenant_id)
    return school


def _mapping(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "party_mode": "student",
        "receivable_ledger": "Fee receivable",
        "mode_ledgers": {"cash": "Cash", "cheque": "SBI Current A/c"},
    }
    body.update(extra)
    return body


def _close_today(school: Any) -> None:
    F.call(school, "accountant", fees.close_day, F.today())


def _period() -> dict[str, str]:
    day = F.today().isoformat()
    return {"period_from": day, "period_to": day}


def _vouchers(body: bytes) -> list[Any]:
    doc = minidom.parseString(body)  # noqa: S318 - SchoolOS's own output, parsed to assert on it
    return list(doc.getElementsByTagName("VOUCHER"))


def _tag(el: Any, name: str) -> str:
    value: str = el.getElementsByTagName(name)[0].firstChild.data
    return value


def _audits(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    return [a for a in T.audit_actions(admin, tenant_id, "") if a["action"] == action]


# --- mapping --------------------------------------------------------------------------------------


def test_FR_TALLY_011_mapping_defaults_save_and_permissions(api: Any, admin_engine: Engine) -> None:
    school = _school(admin_engine)
    setup = F.ensure_setup(school)
    accountant = school.people["accountant"]
    res = api.call(accountant, "GET", f"{BASE}/settings")
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["configured"] is False
    assert out["party_mode"] == "student"  # D85 default
    assert out["include_demand"] is False
    assert out["receivable_ledger"] == "Fee receivable"
    assert {h["code"] for h in out["heads"]} >= {"TUI", "EXM", "BUS"}
    assert out["links_available"] is False

    body = _mapping(head_ledgers={str(setup.tui): "Tuition Fee Income"})
    saved = api.call(accountant, "PUT", f"{BASE}/settings", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["configured"] is True
    assert saved.headers["ETag"] == 'W/"1"'
    tui = next(h for h in saved.json()["heads"] if h["code"] == "TUI")
    assert tui["ledger"] == "Tuition Fee Income"
    stale = api.call(
        accountant, "PUT", f"{BASE}/settings", json=body, headers={"If-Match": 'W/"7"'}
    )
    assert stale.status_code == 412
    unknown = api.call(
        accountant, "PUT", f"{BASE}/settings", json=_mapping(head_ledgers={str(uuid.uuid4()): "X"})
    )
    assert unknown.status_code == 422
    assert [e["code"] for e in unknown.json()["errors"]] == ["unknown_head"]
    events = _audits(admin_engine, school.tenant_id, "tally.export_settings.updated")
    assert len(events) == 1
    assert events[0]["summary"]["party_mode"] == "student"

    # Reading needs tally.configure or fee.report; saving tally.configure (school-wide).
    assert api.call(school.people["auditor_readonly"], "GET", f"{BASE}/settings").status_code == 200
    assert (
        api.call(
            school.people["auditor_readonly"], "PUT", f"{BASE}/settings", json=body
        ).status_code
        == 403
    )
    assert api.call(school.people["office_staff"], "GET", f"{BASE}/settings").status_code == 403
    assert api.call(school.people["teacher"], "GET", f"{BASE}/settings").status_code == 403


def test_FR_TALLY_013_fees_off_answers_404(api: Any, admin_engine: Engine) -> None:
    school = T.fresh_school(admin_engine, flag=True)
    res = api.call(school.people["accountant"], "GET", f"{BASE}/settings")
    assert res.status_code == 404
    assert res.json()["code"] == "module_hidden"


# --- export ---------------------------------------------------------------------------------------


def test_FR_TALLY_016_export_closed_days_mapping_and_idempotency(  # noqa: PLR0915 - one flow
    api: Any, admin_engine: Engine
) -> None:
    school = _school(admin_engine)
    accountant = school.people["accountant"]
    sid = F.SW.create(school, name=STUDENT, section_key="section_9a", admission_no="V/0001")
    account = F.account(school, sid)
    cash = F.issue(school, account, "1500.00")
    F.issue(
        school,
        account,
        "500.00",
        mode="cheque",
        mode_details=ModeDetailsIn(
            cheque_number="123456", bank_name="Synthetic Bank", cheque_date=F.today()
        ),
    )
    upi = F.issue(school, account, "250.00", mode="upi_qr", utr=F.utr())

    # Not configured yet.
    first = api.call(accountant, "POST", f"{BASE}/export", json=_period())
    assert first.status_code == 409
    assert first.json()["code"] == "not_configured"
    assert api.call(accountant, "PUT", f"{BASE}/settings", json=_mapping()).status_code == 200

    # The day is still open.
    preview = api.call(accountant, "GET", f"{BASE}/preview", params=_period()).json()
    assert preview["open_days"] == [F.today().isoformat()]
    assert preview["ready"] is False
    assert {k["kind"]: k["count"] for k in preview["by_kind"]}["receipt"] == 3
    open_day = api.call(accountant, "POST", f"{BASE}/export", json=_period())
    assert open_day.status_code == 409
    assert open_day.json()["code"] == "days_not_closed"

    # Closed, but the UPI ledger is not mapped: nothing is created implicitly in Tally.
    _close_today(school)
    missing = api.call(accountant, "POST", f"{BASE}/export", json=_period())
    assert missing.status_code == 422
    assert [e["field"] for e in missing.json()["errors"]] == ["mode_ledgers.upi_qr"]
    mapped = _mapping(mode_ledgers={"cash": "Cash", "cheque": "SBI Current A/c", "upi_qr": "SBI"})
    assert api.call(accountant, "PUT", f"{BASE}/settings", json=mapped).status_code == 200
    preview = api.call(accountant, "GET", f"{BASE}/preview", params=_period()).json()
    assert preview["ready"] is True
    assert Decimal(preview["cash_in"]) == Decimal("2250.00")
    assert preview["students_on_receivable"] == 1

    # Step-up and fee.report.
    assert (
        api.call(accountant, "POST", f"{BASE}/export", json=_period(), auth_age_s=301).status_code
        == 428
    )
    assert (
        api.call(
            school.people["office_staff"], "POST", f"{BASE}/export", json=_period()
        ).status_code
        == 403
    )

    res = api.call(accountant, "POST", f"{BASE}/export", json=_period())
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/xml")
    assert "attachment" in res.headers["content-disposition"]
    assert res.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in res.headers["content-security-policy"]
    vouchers = _vouchers(res.content)
    assert [v.getAttribute("VCHTYPE") for v in vouchers] == ["Receipt"] * 3
    first_voucher = vouchers[0]
    assert _tag(first_voucher, "VOUCHERNUMBER") == cash.receipt_number
    # No link to a Tally ledger (connector off): the receivable ledger is the party.
    assert _tag(first_voucher, "PARTYLEDGERNAME") == "Fee receivable"
    assert STUDENT in _tag(first_voucher, "NARRATION")
    assert upi.receipt_number in res.text
    events = _audits(admin_engine, school.tenant_id, "fee.report.downloaded")
    assert events[-1]["summary"]["report"] == "tally_vouchers"
    assert events[-1]["summary"]["new_vouchers"] == 3

    # Idempotent: nothing new, then everything again only on request, marked.
    again = api.call(accountant, "POST", f"{BASE}/export", json=_period())
    assert again.status_code == 409
    assert again.json()["code"] == "nothing_to_export"
    both = api.call(
        accountant, "POST", f"{BASE}/export", json={**_period(), "include_exported": True}
    )
    assert both.status_code == 200
    assert all(
        _tag(v, "NARRATION").startswith("[Exported before]") for v in _vouchers(both.content)
    )
    history = api.call(school.people["auditor_readonly"], "GET", f"{BASE}/exports").json()
    assert [(h["vouchers"], h["new_vouchers"]) for h in history] == [(3, 0), (3, 3)]
    assert history[1]["created_by_name"]
    assert api.call(school.people["office_staff"], "GET", f"{BASE}/exports").status_code == 403


def test_FR_TALLY_020_cancellation_party_link_and_demand(api: Any, admin_engine: Engine) -> None:
    school = _school(admin_engine, connector=True)
    setup = F.ensure_setup(school)
    accountant, owner = school.people["accountant"], school.people["owner"]
    sid = F.SW.create(school, name=STUDENT, section_key="section_9a", admission_no="V/0002")
    account = F.account(school, sid)
    party = T.seed_party(admin_engine, school.tenant_id, owner.user_id, ledger="Kumari Family 9A")
    T.seed_link(admin_engine, school.tenant_id, party, sid, owner.user_id)
    receipt = F.issue(school, account, "400.00", role="accountant")
    req = F.call(
        school,
        "accountant",
        fees.request_cancellation,
        receipt.id,
        CancellationRequest(reason_code="wrong_amount", reason="Typed the wrong amount"),
    )
    F.call(
        school, "principal", fees.approve_cancellation, req.id, DecisionIn(), expected_version=None
    )
    _close_today(school)
    heads = {str(setup.tui): "Tuition Fee", str(setup.exm): "Exam Fee"}
    body = _mapping(include_demand=True, head_ledgers=heads)
    assert api.call(accountant, "PUT", f"{BASE}/settings", json=body).status_code == 200
    missing = api.call(accountant, "POST", f"{BASE}/export", json=_period())
    assert missing.status_code == 422
    assert [e["field"] for e in missing.json()["errors"]] == [f"head_ledgers.{setup.bus}"]
    heads[str(setup.bus)] = "Transport Fee"
    body = _mapping(include_demand=True, head_ledgers=heads)
    assert api.call(accountant, "PUT", f"{BASE}/settings", json=body).status_code == 200
    res = api.call(accountant, "POST", f"{BASE}/export", json=_period())
    assert res.status_code == 200, res.text
    by_type: dict[str, list[Any]] = {}
    for v in _vouchers(res.content):
        by_type.setdefault(v.getAttribute("VCHTYPE"), []).append(v)
    assert len(by_type["Receipt"]) == 1
    assert _tag(by_type["Receipt"][0], "PARTYLEDGERNAME") == "Kumari Family 9A"
    (cancel,) = by_type["Payment"]
    assert _tag(cancel, "VOUCHERNUMBER") == f"{receipt.receipt_number}-C"
    # Dr the party, Cr the cash ledger.
    lines = [
        (_tag(e, "LEDGERNAME"), _tag(e, "AMOUNT"))
        for e in cancel.getElementsByTagName("ALLLEDGERENTRIES.LIST")
    ]
    assert lines == [("Kumari Family 9A", "-400.00"), ("Cash", "400.00")]
    # Demand: one journal for the student and day; Dr party 9000, Cr the three heads.
    journals = [j for j in by_type["Journal"] if _tag(j, "PARTYLEDGERNAME") == "Kumari Family 9A"]
    assert len(journals) == 1
    amounts = {
        _tag(e, "LEDGERNAME"): Decimal(_tag(e, "AMOUNT"))
        for e in journals[0].getElementsByTagName("ALLLEDGERENTRIES.LIST")
    }
    assert amounts == {
        "Kumari Family 9A": Decimal("-9000.00"),
        "Tuition Fee": Decimal("6000.00"),
        "Exam Fee": Decimal("1000.00"),
        "Transport Fee": Decimal("2000.00"),
    }


# --- ledger-vs-Tally check ------------------------------------------------------------------------


def _set_balance(admin: Engine, party_id: uuid.UUID, balance: str) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE ops.tally_parties SET closing_balance = :b, as_of = :d WHERE id = :p"),
            {"b": Decimal(balance), "d": F.today(), "p": party_id},
        )


def test_FR_TALLY_025_check_compares_balances(api: Any, admin_engine: Engine) -> None:
    school = _school(admin_engine, connector=True)
    owner = school.people["owner"]
    sid = F.SW.create(school, name=STUDENT, section_key="section_9a", admission_no="V/0003")
    account = F.account(school, sid)
    F.issue(school, account, "1000.00")
    party = T.seed_party(admin_engine, school.tenant_id, owner.user_id, ledger="Kumari 9A")
    T.seed_link(admin_engine, school.tenant_id, party, sid, owner.user_id)
    _set_balance(admin_engine, party, "8000.00")  # 9000 charged - 1000 paid

    who = school.people["accountant"]
    res = api.call(who, "GET", f"{BASE}/check")
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["as_of"] == F.today().isoformat()
    assert out["compared"] == 1
    assert out["differing"] == 0
    assert out["not_exported"] == 1  # the receipt is in no file yet

    _set_balance(admin_engine, party, "8500.00")
    out = api.call(who, "GET", f"{BASE}/check").json()
    assert out["differing"] == 1
    (row,) = out["rows"]
    assert row["ledger_name"] == "Kumari 9A"
    assert row["state"] == "differs"
    assert Decimal(row["difference"]) == Decimal("500.00")

    # finance.read school-wide only; the connector off answers 404.
    assert api.call(school.people["office_staff"], "GET", f"{BASE}/check").status_code == 403
    T.set_flag(admin_engine, school.tenant_id, enabled=False)
    assert api.call(who, "GET", f"{BASE}/check").status_code == 404


def test_SEC_001_other_school_sees_nothing(api: Any, admin_engine: Engine) -> None:
    school = _school(admin_engine)
    other = _school(admin_engine)
    account = F.account(school, F.student(school))
    F.issue(school, account, "700.00")
    _close_today(school)
    accountant = school.people["accountant"]
    assert api.call(accountant, "PUT", f"{BASE}/settings", json=_mapping()).status_code == 200
    assert api.call(accountant, "POST", f"{BASE}/export", json=_period()).status_code == 200
    theirs = other.people["accountant"]
    assert api.call(theirs, "GET", f"{BASE}/exports").json() == []
    assert api.call(theirs, "GET", f"{BASE}/settings").json()["configured"] is False
    preview = api.call(theirs, "GET", f"{BASE}/preview", params=_period()).json()
    assert preview["vouchers"] == 0
    with tenant_session(other.tenant_id) as s:
        count: int = s.execute(
            text("SELECT count(*) FROM ops.tally_exported_vouchers")
        ).scalar_one()
    assert count == 0


def test_SEC_008_export_does_not_log_names_or_ledgers(
    api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    school = _school(admin_engine)
    sid = F.SW.create(school, name=STUDENT, section_key="section_9a", admission_no="V/0004")
    F.issue(school, F.account(school, sid), "321.50")
    _close_today(school)
    accountant = school.people["accountant"]
    body = _mapping(receivable_ledger="Kothapalli Synthetic Receivable")
    capsys.readouterr()
    assert api.call(accountant, "PUT", f"{BASE}/settings", json=body).status_code == 200
    assert api.call(accountant, "POST", f"{BASE}/export", json=_period()).status_code == 200
    logs = "".join(capsys.readouterr())
    for secret in (STUDENT, "Kothapalli Synthetic Receivable", "321.50"):
        assert secret not in logs
