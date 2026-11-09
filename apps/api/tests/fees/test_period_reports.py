"""Accountant and management reports (FM-04; FR-FEE-061..075).

The fee ``school`` is shared by every fee test, so assertions look at this test's own receipts,
accounts and students, and at identities that hold whatever else is in the school (totals equal
the sum of their parts). Synthetic data only.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import io
import uuid
import zipfile
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.errors import Forbidden, NotFound, StepUpRequired, ValidationFailed
from app.fees import service as fees
from app.fees.reports_config import load_config
from app.fees.schemas import (
    AdjustmentCreate,
    CancellationRequest,
    DecisionIn,
    FeePlanCreate,
    InstalmentIn,
    LateFeeRule,
    PlanLineIn,
)

pytestmark = pytest.mark.db

F: Any = __import__("sys").modules["sos_test_fees_support"]
W = F.W
D = Decimal
BASE = "/api/v1/fees/reports"
OLD_DUE = (dt.date(2025, 7, 15), dt.date(2025, 12, 15))


def _today_query() -> str:
    day = F.today().isoformat()
    return f"from={day}&to={day}"


# --- earlier-year account (as in test_arrears, reusing the 2025-26 plan when it exists) ---------


def _old_plan(school: Any, admin: Engine) -> uuid.UUID:
    found = F.scalar(
        admin,
        "SELECT id FROM fin.fee_plans WHERE tenant_id = :t AND academic_year_id = :y "
        "AND class_id = :c AND status = 'active' AND category_code = 'fm04_reports'",
        t=school.tenant_id,
        y=school.ids["old_year"],
        c=school.ids["class_ix"],
    )
    if found is not None:
        return uuid.UUID(str(found))
    setup = F.ensure_setup(school)
    amounts = {setup.tui: D("2000.00"), setup.exm: D("400.00")}
    plan = F.call(
        school,
        "accountant",
        fees.create_plan,
        FeePlanCreate(
            academic_year_id=school.ids["old_year"],
            class_id=school.ids["class_ix"],
            category_code="fm04_reports",
            name="Class IX 2025-26 (reports)",
            lines=[
                PlanLineIn(head_id=setup.tui, annual_amount=D("4000.00")),
                PlanLineIn(head_id=setup.exm, annual_amount=D("800.00")),
            ],
            instalments=[
                InstalmentIn(instalment_no=n, label=f"Term {n}", due_on=due, amounts=amounts)
                for n, due in enumerate(OLD_DUE, start=1)
            ],
            late_fee_rule=LateFeeRule(),
        ),
    )
    plan = F.call(school, "accountant", fees.activate_plan, plan.id, expected_version=plan.version)
    return uuid.UUID(str(plan.id))


def _old_account(admin: Engine, school: Any, student_id: uuid.UUID) -> uuid.UUID:
    """An open 2025-26 account of ``student_id`` charged both terms (4,800 overdue)."""
    setup = F.ensure_setup(school)
    plan_id = _old_plan(school, admin)
    account_id = uuid.uuid4()
    by = school.people["accountant"].membership_id
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO fin.student_fee_accounts "
                "(id, tenant_id, student_id, academic_year_id, plan_id, opened_by) "
                "VALUES (:i, :t, :s, :y, :p, :b)"
            ),
            {
                "i": account_id,
                "t": school.tenant_id,
                "s": student_id,
                "y": school.ids["old_year"],
                "p": plan_id,
                "b": by,
            },
        )
        for no, due in enumerate(OLD_DUE, start=1):
            for head, amount in ((setup.tui, "2000.00"), (setup.exm, "400.00")):
                c.execute(
                    text(
                        "INSERT INTO fin.ledger_entries (id, tenant_id, account_id, entry_kind, "
                        "amount, instalment_no, head_id, entry_date, posted_by) "
                        "VALUES (:i, :t, :a, 'charge', :m, :n, :h, :d, :b)"
                    ),
                    {
                        "i": uuid.uuid4(),
                        "t": school.tenant_id,
                        "a": account_id,
                        "m": D(amount),
                        "n": no,
                        "h": head,
                        "d": due,
                        "b": by,
                    },
                )
    return account_id


def _write_off(school: Any, account_id: uuid.UUID, amount: str) -> uuid.UUID:
    setup = F.ensure_setup(school)
    req = F.call(
        school,
        "accountant",
        fees.request_adjustment,
        account_id,
        AdjustmentCreate(
            instalment_no=1,
            head_id=setup.tui,
            amount=D(amount),
            reason_code="write_off",
            reason="Synthetic write-off, management decision",
        ),
    )
    F.call(
        school, "principal", fees.approve_adjustment, req.id, DecisionIn(), expected_version=None
    )
    return uuid.UUID(str(req.id))


def _cancel(school: Any, receipt_id: uuid.UUID) -> uuid.UUID:
    req = F.call(
        school,
        "accountant",
        fees.request_cancellation,
        receipt_id,
        CancellationRequest(reason_code="wrong_amount", reason="Synthetic wrong amount typed"),
    )
    F.call(
        school, "principal", fees.approve_cancellation, req.id, DecisionIn(), expected_version=None
    )
    return uuid.UUID(str(req.id))


# --- collection register (FR-FEE-061..063) --------------------------------------------------------


def test_FR_FEE_061_collection_register_by_day_head_mode_and_collector(school: Any) -> None:
    account = F.account(school)
    kept = F.issue(school, account, "1200.00")
    gone = F.issue(school, account, "300.00", role="accountant")
    _cancel(school, gone.id)
    today = F.today()
    out = F.call(school, "accountant", fees.collection_register, today, today)
    assert (out.date_from, out.date_to) == (today, today)
    numbers = {r.receipt_number: r for r in out.receipts}
    assert numbers[kept.receipt_number].status == "issued"
    # The cancelled receipt keeps its number and is listed again apart.
    assert numbers[gone.receipt_number].status == "cancelled"
    assert gone.receipt_number in {x.receipt_number for x in out.cancellations}
    assert out.net_total == out.receipts_total - out.cancelled_total - out.refunded_total
    assert out.receipts_total == sum((m.gross for m in out.by_mode), D("0"))
    assert out.receipts_total == sum((d.gross for d in out.by_day), D("0"))
    assert out.receipts_total == sum((x.gross for x in out.by_collector), D("0"))
    assert out.net_total == sum((h.net for h in out.by_head), D("0"))
    assert out.net_total == sum((d.net for d in out.by_day), D("0"))
    assert out.receipts_count == sum(d.receipts for d in out.by_day)
    tuition = next(h for h in out.by_head if h.head_code == "TUI")
    assert tuition.collected >= D("1200.00")


def test_FR_FEE_061_period_checks(school: Any) -> None:
    today = F.today()
    cases = [
        (today, today + dt.timedelta(days=1), "date_to", "in_future"),
        (today, today - dt.timedelta(days=1), "date_from", "after_date_to"),
        (
            today - dt.timedelta(days=load_config().max_range_days),
            today,
            "date_from",
            "range_too_long",
        ),
    ]
    for first, last, field, code in cases:
        with pytest.raises(ValidationFailed) as exc:
            F.call(school, "accountant", fees.collection_register, first, last)
        assert (exc.value.errors[0]["field"], exc.value.errors[0]["code"]) == (field, code)
    # Defaults: the first of this month to today.
    out = F.call(school, "accountant", fees.collection_register, None, None)
    assert (out.date_from, out.date_to) == (today.replace(day=1), today)


def test_FR_FEE_062_print_is_a4_and_audited(school: Any, admin_engine: Engine, api: Any) -> None:
    F.issue(school, F.account(school), "250.00")
    res = api.call(school.people["principal"], "GET", f"{BASE}/collections/print?{_today_query()}")
    assert res.status_code == 200, res.text
    assert "Collection register" in res.text
    assert "<script" not in res.text
    assert res.headers["content-security-policy"].startswith("default-src 'none'")
    events = W.audit_events(admin_engine, school.tenant_id, "fee.report.print_viewed")
    assert any(e["summary"].get("report") == "collections" for e in events)


def test_FR_FEE_063_downloads_need_step_up_and_are_audited(
    school: Any, admin_engine: Engine, api: Any
) -> None:
    receipt = F.issue(school, F.account(school), "640.00")
    who = school.people["accountant"]
    path = f"{BASE}/collections/download?{_today_query()}"
    stale = api.call(who, "GET", path, auth_age_s=3600)
    assert stale.status_code == 428
    res = api.call(who, "GET", path)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    assert receipt.receipt_number in res.text
    heads = api.call(who, "GET", f"{path}&part=heads&format=xlsx")
    assert heads.status_code == 200, heads.text
    assert heads.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert heads.content[:2] == b"PK"
    events = W.audit_events(admin_engine, school.tenant_id, "fee.report.downloaded")
    mine = [e for e in events if e["summary"].get("report") == "collections"]
    assert {e["summary"].get("part") for e in mine} >= {"receipts", "heads"}
    assert all("Synthetica" not in str(e["summary"]) for e in mine)


def test_FR_FEE_072_reports_are_school_wide_finance_only(school: Any, api: Any) -> None:
    for role in ("office_staff", "class_teacher", "teacher"):
        res = api.call(school.people[role], "GET", f"{BASE}/collections")
        assert res.status_code == 403, (role, res.text)
        with pytest.raises(Forbidden):
            F.call(school, role, fees.ageing)
    for role in ("owner", "principal", "accountant", "auditor_readonly"):
        res = api.call(school.people[role], "GET", f"{BASE}/dcb")
        assert res.status_code == 200, (role, res.text)
    # Office staff hold no fee.report: no download either.
    res = api.call(school.people["office_staff"], "GET", f"{BASE}/ageing/download")
    assert res.status_code == 403


def test_FR_FEE_072_other_school_year_is_404(school: Any, world: Any, api: Any) -> None:
    other = world.a.ids["year"]
    with pytest.raises(NotFound):
        F.call(school, "accountant", fees.dcb, other)
    res = api.call(school.people["accountant"], "GET", f"{BASE}/dcb?academic_year_id={other}")
    assert res.status_code == 404


# --- DCB (FR-FEE-064, FR-FEE-065) ---------------------------------------------------------------


def test_FR_FEE_064_dcb_by_class_and_section_with_write_offs(
    school: Any, admin_engine: Engine
) -> None:
    sid = F.student(school)
    current = F.account(school, sid)
    old = _old_account(admin_engine, school, sid)
    F.issue(school, current, "500.00")
    _write_off(school, old, "-1500.00")
    out = F.call(school, "accountant", fees.dcb, None)
    assert out.academic_year_id == school.ids["year"]
    row = next(r for r in out.rows if r.section_id == school.ids["section_9a"])
    assert row.accounts >= 1
    assert row.demand >= D("9000.00")
    for r in [*out.rows, out.totals]:
        assert r.net_demand == (
            r.demand - r.concessions + r.late_fees + r.adjustments - r.written_off
        )
        assert r.balance == r.net_demand - r.collected + r.refunded
    assert out.totals.accounts == sum(r.accounts for r in out.rows)
    assert out.totals.balance == sum((r.balance for r in out.rows), D("0"))
    # Earlier-year dues (FM-01) with the write-off apart.
    old_year = next(e for e in out.earlier_years if e.academic_year_id == school.ids["old_year"])
    assert old_year.academic_year_label == "2025-26"
    assert old_year.written_off >= D("1500.00")
    assert old_year.balance >= D("3300.00")
    assert out.earlier_years_balance == sum((e.balance for e in out.earlier_years), D("0"))
    # The earlier year's own DCB shows the write-off as written off, not as an adjustment.
    past = F.call(school, "accountant", fees.dcb, school.ids["old_year"])
    assert past.totals.written_off >= D("1500.00")
    assert past.earlier_years == [] or all(
        e.academic_year_id != school.ids["old_year"] for e in past.earlier_years
    )


# --- registers (FR-FEE-066, FR-FEE-067) ---------------------------------------------------------


def test_FR_FEE_066_registers_label_write_offs_and_keep_reasons_out(
    school: Any, admin_engine: Engine, api: Any
) -> None:
    sid = F.student(school)
    F.account(school, sid)
    old = _old_account(admin_engine, school, sid)
    adjustment = _write_off(school, old, "-700.00")
    receipt = F.issue(school, F.account(school), "150.00", role="accountant")
    cancellation = _cancel(school, receipt.id)
    today = F.today()
    out = F.call(school, "accountant", fees.registers, today, today)
    entry = next(a for a in out.adjustments if a.adjustment_id == adjustment)
    assert entry.reason_code == "write_off"
    assert entry.reason_label == "Written off by management"
    assert entry.academic_year_label == "2025-26"
    assert entry.approved_by_name is not None
    assert out.written_off_total >= D("700.00")
    assert cancellation in {x.cancellation_id for x in out.cancellations}
    assert "management decision" not in out.model_dump_json()
    who = school.people["accountant"]
    plain = api.call(who, "GET", f"{BASE}/registers/download?kind=adjustments&{_today_query()}")
    assert plain.status_code == 200, plain.text
    assert "Written off by management" in plain.text
    assert "management decision" not in plain.text
    full = api.call(
        who,
        "GET",
        f"{BASE}/registers/download?kind=adjustments&include_reasons=true&{_today_query()}",
    )
    assert "management decision" in full.text
    events = W.audit_events(admin_engine, school.tenant_id, "fee.report.downloaded")
    flags = {
        e["summary"].get("include_reasons")
        for e in events
        if e["summary"].get("report") == "register_adjustments"
    }
    assert flags >= {True, False}


# --- ageing (FR-FEE-068) -------------------------------------------------------------------------


def test_FR_FEE_068_ageing_buckets_and_earlier_years(school: Any, admin_engine: Engine) -> None:
    sid = F.student(school)
    F.account(school, sid)
    _old_account(admin_engine, school, sid)
    out = F.call(school, "accountant", fees.ageing)
    assert [(b.from_days, b.to_days) for b in out.bands] == [(0, 30), (31, 90), (91, None)]
    row = next(r for r in out.rows if r.student_id == sid)
    assert row.earlier_years == D("4800.00")
    assert row.overdue == sum(row.bands, D("0")) + row.earlier_years
    assert row.oldest_due_on == OLD_DUE[0]
    assert out.overdue_total == sum(out.totals, D("0")) + out.earlier_years_total
    assert out.overdue_total == sum((c.overdue for c in out.by_class), D("0"))


# --- receipt series and year-end pack (FR-FEE-070, FR-FEE-071) -----------------------------------


def test_FR_FEE_070_series_check_reports_first_last_and_missing(
    school: Any, admin_engine: Engine
) -> None:
    receipt = F.issue(school, F.account(school), "90.00")
    fy = receipt.receipt_number.split("/")[1]
    out = F.call(school, "accountant", fees.series_check, fy)
    rct = next(s for s in out.series if s.series == "receipt")
    assert rct.used >= 1
    assert rct.missing_count == 0
    assert rct.counter == int(str(rct.last_number).rsplit("/", 1)[1])
    # A counter ahead of the receipts (numbers taken and never issued) is reported.
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO fin.receipt_counters (id, tenant_id, series, financial_year, last_no)"
                " VALUES (:i, :t, 'RCT', '2025-26', 2) ON CONFLICT DO NOTHING"
            ),
            {"i": uuid.uuid4(), "t": school.tenant_id},
        )
    past = F.call(school, "accountant", fees.series_check, "2025-26")
    assert past.gap_free is False
    assert next(s for s in past.series if s.series == "receipt").missing == [1, 2]
    for bad, code in (("2026-28", "invalid"), ("2031-32", "in_future")):
        with pytest.raises(ValidationFailed) as exc:
            F.call(school, "accountant", fees.series_check, bad)
        assert exc.value.errors[0]["code"] == code


def test_FR_FEE_071_year_end_pack_zip(school: Any, admin_engine: Engine, api: Any) -> None:
    receipt = F.issue(school, F.account(school), "75.00")
    fy = receipt.receipt_number.split("/")[1]
    who = school.people["auditor_readonly"]
    res = api.call(who, "GET", f"{BASE}/year-end-pack?financial_year={fy}")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        names = set(zf.namelist())
        assert {
            "README.txt",
            "collections-receipts.csv",
            "collections-heads.csv",
            "register-adjustments.csv",
            "register-cancellations.csv",
            "receipt-series.csv",
        } <= names
        assert any(n.startswith("dcb-") for n in names)
        assert any(n.startswith("ageing-") for n in names)
        assert receipt.receipt_number in zf.read("collections-receipts.csv").decode("utf-8")
        assert f"Financial year: {fy}" in zf.read("README.txt").decode("utf-8")
    events = W.audit_events(admin_engine, school.tenant_id, "fee.report.downloaded")
    assert any(
        e["summary"].get("report") == "year_end_pack" and e["summary"].get("financial_year") == fy
        for e in events
    )


def test_FR_FEE_071_pack_needs_recent_mfa_in_the_service(school: Any) -> None:
    person = school.people["accountant"]
    stale = dataclasses.replace(
        F.ctx(school, person, "accountant"),
        auth_time=dt.datetime.now(dt.UTC) - dt.timedelta(hours=2),
    )
    with pytest.raises(StepUpRequired):
        F.call(school, "accountant", fees.year_end_pack, "2026-27", as_ctx=stale)


def test_FR_FEE_073_reports_hidden_while_fees_are_off(
    school: Any, admin_engine: Engine, api: Any
) -> None:
    F.set_setting(admin_engine, school.tenant_id, enabled=False)
    try:
        res = api.call(school.people["accountant"], "GET", f"{BASE}/ageing")
        assert res.status_code == 404
        assert res.json()["code"] == "module_hidden"
    finally:
        F.set_setting(admin_engine, school.tenant_id, enabled=True)
