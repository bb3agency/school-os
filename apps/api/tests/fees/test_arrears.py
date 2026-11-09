"""Earlier-year dues (arrears), left with dues and write-offs (FM-01; FR-FEE-031..045; D84).

An earlier-year account is made the way a school's 2025-26 account looks in June 2027: an
active plan of the earlier year and charge entries on an open account of that year (written by
the admin engine, since the synthetic school has no 2025-26 enrolments to apply a plan to).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.core.db import tenant_session
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.fees import service as fees
from app.fees.schemas import (
    AccountSearch,
    AdjustmentCreate,
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

OLD_DUE = (dt.date(2025, 7, 15), dt.date(2025, 12, 15))
_OLD_PLANS: dict[uuid.UUID, uuid.UUID] = {}


def _old_plan(school: Any) -> uuid.UUID:
    """The active Class IX plan of 2025-26 (once per school): TUI 2,000 + EXM 400 per term."""
    found = _OLD_PLANS.get(school.tenant_id)
    if found is not None:
        return found
    setup = F.ensure_setup(school)
    amounts = {setup.tui: D("2000.00"), setup.exm: D("400.00")}
    plan = F.call(
        school,
        "accountant",
        fees.create_plan,
        FeePlanCreate(
            academic_year_id=school.ids["old_year"],
            class_id=school.ids["class_ix"],
            name="Class IX 2025-26",
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
    _OLD_PLANS[school.tenant_id] = plan.id
    return uuid.UUID(str(plan.id))


def _old_account(admin: Engine, school: Any, student_id: uuid.UUID) -> uuid.UUID:
    """An open 2025-26 account of ``student_id`` charged both terms (4,800 overdue)."""
    setup = F.ensure_setup(school)
    plan_id = _old_plan(school)
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


def _student_with_arrears(admin: Engine, school: Any) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(student, current account, 2025-26 account)."""
    sid = F.student(school)
    current = F.account(school, sid)
    return sid, current, _old_account(admin, school, sid)


def _set_status(admin: Engine, student_id: uuid.UUID, status: str) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE sis.students SET status = :s WHERE id = :i"),
            {"s": status, "i": student_id},
        )


# --- dues list, CSV, tiles (FR-FEE-031..033) ----------------------------------------------------


def test_FR_FEE_031_dues_list_has_earlier_years_with_a_year_column(
    school: Any, admin_engine: Engine
) -> None:
    sid, current, old = _student_with_arrears(admin_engine, school)
    out = F.call(school, "accountant", fees.dues_list, section_id=None, class_id=None)
    rows = {r.account_id: r for r in out.rows}
    assert rows[old].earlier_year is True
    assert rows[old].academic_year_label == "2025-26"
    assert rows[old].overdue == D("4800.00")
    assert rows[old].oldest_due_on == OLD_DUE[0]
    assert rows[current].earlier_year is False
    assert rows[current].academic_year_label == "2026-27"
    # Tiles: students are counted once; arrears cover earlier-year accounts only.
    assert out.students == len({r.student_id for r in out.rows})
    assert out.arrears_total == sum((r.overdue for r in out.rows if r.earlier_year), D("0"))
    assert out.arrears_students >= 1
    # Earlier years come first (oldest due first).
    assert out.rows.index(rows[old]) < out.rows.index(rows[current])
    # The class filter reaches the student's earlier-year account through today's class.
    by_class = F.call(
        school, "accountant", fees.dues_list, section_id=None, class_id=school.ids["class_ix"]
    )
    assert old in {r.account_id for r in by_class.rows}
    del sid


def test_FR_FEE_032_arrears_only_filter(school: Any, admin_engine: Engine) -> None:
    _, current, old = _student_with_arrears(admin_engine, school)
    out = F.call(
        school, "accountant", fees.dues_list, section_id=None, class_id=None, arrears_only=True
    )
    ids = {r.account_id for r in out.rows}
    assert old in ids
    assert current not in ids
    assert all(r.earlier_year for r in out.rows)
    assert out.arrears_total == out.total_overdue


def test_FR_FEE_033_csv_has_the_year_and_is_audited(
    school: Any, admin_engine: Engine, api: Any
) -> None:
    _student_with_arrears(admin_engine, school)
    who = school.people["accountant"]
    res = api.call(who, "GET", "/api/v1/fees/dues.csv?arrears_only=true")
    assert res.status_code == 200, res.text
    header = res.text.lstrip("﻿").splitlines()[0]
    assert "academic_year" in header
    assert "2025-26" in res.text
    assert "2026-27" not in res.text
    events = W.audit_events(admin_engine, school.tenant_id, "fee.report.downloaded")
    assert any(
        e["summary"].get("report") == "dues" and e["summary"].get("arrears_only") is True
        for e in events
    )


def test_FR_FEE_031_over_http(school: Any, admin_engine: Engine, api: Any) -> None:
    _, _, old = _student_with_arrears(admin_engine, school)
    res = api.call(school.people["principal"], "GET", "/api/v1/fees/dues?arrears_only=true")
    assert res.status_code == 200, res.text
    assert str(old) in {r["account_id"] for r in res.json()["rows"]}
    assert res.json()["arrears_total"] == res.json()["total_overdue"]
    assert api.call(school.people["office_staff"], "GET", "/api/v1/fees/dues").status_code == 403


# --- reminders (FR-FEE-034, FR-FEE-035) ---------------------------------------------------------


def _offset(admin: Engine, school: Any) -> int:
    with admin.connect() as c:
        found = c.execute(
            text("SELECT reminder_offsets FROM fin.fee_settings WHERE tenant_id = :t"),
            {"t": school.tenant_id},
        ).scalar()
    return int(sorted(found)[0]) if found else -3


def test_FR_FEE_034_automatic_reminders_skip_earlier_years_and_students_who_left(
    school: Any, admin_engine: Engine
) -> None:
    setup = F.ensure_setup(school)
    _, staying, staying_old = _student_with_arrears(admin_engine, school)
    gone_id, gone, _ = _student_with_arrears(admin_engine, school)
    _set_status(admin_engine, gone_id, "left")
    offset = _offset(admin_engine, school)
    with tenant_session(school.tenant_id) as s:
        day = setup.first_due + dt.timedelta(days=offset)
        listed = {d.account_id for d in fees.reminder_dues(s, day)}
        old_day = OLD_DUE[0] + dt.timedelta(days=offset)
        listed_old = {d.account_id for d in fees.reminder_dues(s, old_day)}
    assert staying in listed
    assert gone not in listed  # left the school: manual reminders only
    assert staying_old not in listed | listed_old  # earlier years: never automatic


def test_FR_FEE_035_manual_reminder_needs_the_option_for_earlier_years(
    school: Any, admin_engine: Engine
) -> None:
    sid, current, old = _student_with_arrears(admin_engine, school)
    with pytest.raises(ValidationFailed) as exc:
        F.call(
            school,
            "accountant",
            fees.reminder_accounts,
            account_ids=[old],
            section_id=None,
            class_id=None,
        )
    assert exc.value.errors[0]["code"] == "earlier_year"
    plain = F.call(
        school,
        "accountant",
        fees.reminder_accounts,
        account_ids=[current],
        section_id=None,
        class_id=None,
    )
    out = F.call(
        school,
        "accountant",
        fees.reminder_accounts,
        account_ids=[old, current],
        section_id=None,
        class_id=None,
        include_earlier_years=True,
    )
    # One entry per student with the total of all its open accounts, on the current account.
    assert len(out) == 1
    assert out[0].student_id == sid
    assert out[0].account_id == current
    assert out[0].overdue == plain[0].overdue + D("4800.00")
    assert out[0].oldest_due_on == OLD_DUE[0]
    # Without the option the filter path leaves earlier years out.
    every = F.call(
        school,
        "accountant",
        fees.reminder_accounts,
        account_ids=None,
        section_id=None,
        class_id=None,
    )
    assert old not in {a.account_id for a in every}


def test_FR_FEE_035_other_schools_account_is_404(
    school: Any, other_school: Any, admin_engine: Engine
) -> None:
    _, _, old = _student_with_arrears(admin_engine, school)
    F.ensure_setup(other_school)
    with pytest.raises(NotFound):
        F.call(
            other_school,
            "accountant",
            fees.reminder_accounts,
            account_ids=[old],
            section_id=None,
            class_id=None,
            include_earlier_years=True,
        )


# --- counter (FR-FEE-036, FR-FEE-037) -----------------------------------------------------------


def test_FR_FEE_036_counter_shows_earlier_years_first(school: Any, admin_engine: Engine) -> None:
    sid, current, old = _student_with_arrears(admin_engine, school)
    out = F.call(school, "office_staff", fees.student_years, sid, split_amount=None)
    assert [y.account_id for y in out.years] == [old, current]
    assert out.years[0].earlier_year is True
    assert out.earlier_years_due == D("4800.00")
    assert out.split is None
    found = F.call(
        school,
        "office_staff",
        fees.search_accounts,
        AccountSearch(section_id=school.ids["section_9a"]),
    )
    mine = next(a for a in found if a.account_id == current)
    assert mine.earlier_years_due == D("4800.00")


def test_FR_FEE_037_split_oldest_first_only_when_asked(school: Any, admin_engine: Engine) -> None:
    sid, current, old = _student_with_arrears(admin_engine, school)
    out = F.call(school, "office_staff", fees.student_years, sid, split_amount=D("5000.00"))
    assert [(p.account_id, p.amount) for p in out.split] == [
        (old, D("4800.00")),
        (current, D("200.00")),
    ]
    small = F.call(school, "office_staff", fees.student_years, sid, split_amount=D("100.00"))
    assert [(p.account_id, p.amount) for p in small.split] == [(old, D("100.00"))]
    for bad, code in ((D("0"), "not_positive"), (out.total_collectable + 1, "above_outstanding")):
        with pytest.raises(ValidationFailed) as exc:
            F.call(school, "office_staff", fees.student_years, sid, split_amount=bad)
        assert exc.value.errors[0]["code"] == code
    # Each part is an ordinary receipt on its own account (FR-FEE-010).
    F.issue(school, old, "4800.00")
    after = F.call(school, "office_staff", fees.student_years, sid, split_amount=None)
    assert after.earlier_years_due == D("0.00")


def test_FR_FEE_036_authz(school: Any, other_school: Any, admin_engine: Engine, api: Any) -> None:
    sid, _, _ = _student_with_arrears(admin_engine, school)
    path = f"/api/v1/fees/students/{sid}/years"
    assert api.call(school.people["office_staff"], "GET", path).status_code == 200
    assert (
        api.call(school.people["accountant"], "GET", path + "?split_amount=10.00").status_code
        == 200
    )
    assert api.call(school.people["class_teacher"], "GET", path).status_code == 403
    # fee.collect held for one section only: fee permissions are school-wide (FR-FEE-024).
    assert api.call(school.people["scoped_collector"], "GET", path).status_code == 403
    F.ensure_setup(other_school)
    res = api.call(other_school.people["accountant"], "GET", path)
    assert res.status_code == 404


# --- left with dues (FR-FEE-038) ----------------------------------------------------------------


def test_FR_FEE_038_left_with_dues(school: Any, admin_engine: Engine) -> None:
    gone_id, gone, gone_old = _student_with_arrears(admin_engine, school)
    staying_id, _, _ = _student_with_arrears(admin_engine, school)
    _set_status(admin_engine, gone_id, "left")
    out = F.call(school, "principal", fees.left_with_dues)
    rows = {r.student_id: r for r in out.rows}
    assert staying_id not in rows
    row = rows[gone_id]
    assert row.status == "left"
    assert row.accounts == 2
    assert row.oldest_due_on == OLD_DUE[0]
    assert row.written_off == D("0.00")
    assert out.total_balance == sum((r.balance for r in out.rows), D("0"))
    with pytest.raises(Forbidden):
        F.call(school, "office_staff", fees.left_with_dues)
    del gone, gone_old


def test_FR_FEE_038_over_http_and_csv(school: Any, admin_engine: Engine, api: Any) -> None:
    gone_id, _, _ = _student_with_arrears(admin_engine, school)
    _set_status(admin_engine, gone_id, "graduated")
    who = school.people["accountant"]
    res = api.call(who, "GET", "/api/v1/fees/reports/left-with-dues")
    assert res.status_code == 200, res.text
    assert str(gone_id) in {r["student_id"] for r in res.json()["rows"]}
    assert (
        api.call(
            school.people["office_staff"], "GET", "/api/v1/fees/reports/left-with-dues"
        ).status_code
        == 403
    )
    path = "/api/v1/fees/reports/left-with-dues.csv"
    assert api.call(who, "GET", path, auth_age_s=301).status_code == 428
    csv = api.call(who, "GET", path)
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    assert "graduated" in csv.text
    events = W.audit_events(admin_engine, school.tenant_id, "fee.report.downloaded")
    assert any(e["summary"].get("report") == "left_with_dues" for e in events)


def test_FR_FEE_040_left_with_dues_never_blocks_a_tc(school: Any, admin_engine: Engine) -> None:
    """Information only: the report changes nothing on the student or the accounts."""
    gone_id, gone, old = _student_with_arrears(admin_engine, school)
    _set_status(admin_engine, gone_id, "left")
    F.call(school, "principal", fees.left_with_dues)
    status = F.scalar(admin_engine, "SELECT status FROM sis.students WHERE id = :i", i=gone_id)
    assert status == "left"
    still = F.scalar(
        admin_engine,
        "SELECT count(*) FROM fin.student_fee_accounts WHERE id IN (:a, :b) AND status = 'open'",
        a=gone,
        b=old,
    )
    assert still == 2


# --- parent dues page (FR-FEE-039) --------------------------------------------------------------


def test_FR_FEE_039_parent_dues_show_earlier_years(school: Any, admin_engine: Engine) -> None:
    _, current, _ = _student_with_arrears(admin_engine, school)
    with tenant_session(school.tenant_id) as s:
        found = fees.parent_dues(s, current)
    assert found is not None
    assert [(e.label, e.due) for e in found.earlier_years] == [("2025-26", D("4800.00"))]


# --- write-off (FR-FEE-041..043) ----------------------------------------------------------------


def _write_off(school: Any, account_id: uuid.UUID, amount: str, role: str = "accountant") -> Any:
    setup = F.ensure_setup(school)
    return F.call(
        school,
        role,
        fees.request_adjustment,
        account_id,
        AdjustmentCreate(
            instalment_no=1,
            head_id=setup.tui,
            amount=D(amount),
            reason_code="write_off",
            reason="Family moved away, management decision",
        ),
    )


def test_FR_FEE_041_write_off_is_maker_checker_with_step_up(
    school: Any, admin_engine: Engine
) -> None:
    gone_id, _, old = _student_with_arrears(admin_engine, school)
    _set_status(admin_engine, gone_id, "left")
    with pytest.raises(ValidationFailed) as exc:
        _write_off(school, old, "500.00")
    assert exc.value.errors[0]["code"] == "write_off_reduces_due"
    with pytest.raises(Conflict):
        _write_off(school, old, "-2500.00")  # more than the line's 2,000
    req = _write_off(school, old, "-2000.00")
    assert req.status == "pending"
    with pytest.raises(Forbidden):  # the maker never approves
        F.call(
            school,
            "accountant",
            fees.approve_adjustment,
            req.id,
            DecisionIn(),
            expected_version=None,
        )
    with pytest.raises(Forbidden):  # office staff cannot approve
        F.call(
            school,
            "office_staff",
            fees.approve_adjustment,
            req.id,
            DecisionIn(),
            expected_version=None,
        )
    out = F.call(
        school, "principal", fees.approve_adjustment, req.id, DecisionIn(), expected_version=None
    )
    assert out.status == "approved"
    # In the ledger, labelled through its reason code.
    statement = F.call(school, "accountant", fees.statement, old)
    entry = next(e for e in statement.entries if e.source_id == req.id)
    assert entry.entry_kind == "adjustment"
    assert entry.amount == D("-2000.00")
    assert entry.reason_code == "write_off"
    # In the report.
    report = F.call(school, "principal", fees.left_with_dues)
    row = next(r for r in report.rows if r.student_id == gone_id)
    assert row.written_off == D("2000.00")
    assert report.written_off_total >= D("2000.00")
    # Audited in the same transaction with the reason code, never the reason text.
    events = W.audit_events(admin_engine, school.tenant_id, "fee.adjustment.approved")
    mine = [e for e in events if e["resource_id"] == req.id]
    assert len(mine) == 1
    assert mine[0]["summary"]["reason_code"] == "write_off"
    assert "management decision" not in str(mine[0]["summary"])


def test_FR_FEE_041_write_off_approval_needs_step_up_over_http(
    school: Any, admin_engine: Engine, api: Any
) -> None:
    _, _, old = _student_with_arrears(admin_engine, school)
    req = _write_off(school, old, "-100.00")
    path = f"/api/v1/fees/adjustments/{req.id}/approve"
    who = school.people["principal"]
    assert api.call(who, "POST", path, json={}, auth_age_s=301).status_code == 428
    res = api.call(who, "POST", path, json={})
    assert res.status_code == 200, res.text
    assert res.json()["reason_code"] == "write_off"


def test_FR_FEE_041_database_refuses_a_write_off_that_adds(
    school: Any, admin_engine: Engine
) -> None:
    _, _, old = _student_with_arrears(admin_engine, school)
    setup = F.ensure_setup(school)
    with pytest.raises(DBAPIError, match="adjustments_write_off_shape"), admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO fin.adjustments (id, tenant_id, account_id, instalment_no, head_id, "
                "amount, reason_code, reason, requested_by) "
                "VALUES (:i, :t, :a, 1, :h, 10, 'write_off', 'Synthetic write-off reason', :b)"
            ),
            {
                "i": uuid.uuid4(),
                "t": school.tenant_id,
                "a": old,
                "h": setup.tui,
                "b": school.people["accountant"].membership_id,
            },
        )
