"""Fee ledger and receipts through the real services (US-2101..US-2106, US-2110; FR-FEE-*)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.fees import service as fees
from app.fees.schemas import (
    AccountSearch,
    AdjustmentCreate,
    AllocationIn,
    CancellationRequest,
    ConcessionCreate,
    ConcessionTarget,
    DecisionIn,
    FeePlanCreate,
    FeePlanPatch,
    FeeSettingsIn,
    HeadCreate,
    HeadPatch,
    InstalmentIn,
    LateFeeRule,
    ModeDetailsIn,
    PlanApplyIn,
    PlanLineIn,
    ProposeAdjustmentsIn,
    RefundCreate,
    RejectIn,
)

pytestmark = pytest.mark.db

F: Any = __import__("sys").modules["sos_test_fees_support"]
D = Decimal


def _account(school: Any, account_id: uuid.UUID, role: str = "accountant") -> Any:
    return F.call(school, role, fees.get_account, account_id)


def _line(out: Any, inst: int, head: uuid.UUID | None) -> Any:
    return next(x for x in out.lines if x.instalment_no == inst and x.head_id == head)


# --- gate (FR-FEE-001) ---------------------------------------------------------------------------


def test_FR_FEE_001_fees_off_answer_module_hidden(school: Any, admin_engine: Engine) -> None:
    F.set_setting(admin_engine, school.tenant_id, enabled=False)
    try:
        with pytest.raises(NotFound) as err:
            F.call(school, "accountant", fees.list_heads)
        assert err.value.code == "module_hidden"
    finally:
        F.set_setting(admin_engine, school.tenant_id, enabled=True)
    F.set_flag(admin_engine, school.tenant_id, enabled=False)
    try:
        with pytest.raises(NotFound):
            F.call(school, "accountant", fees.list_heads)
    finally:
        F.set_flag(admin_engine, school.tenant_id, enabled=True)
    assert F.call(school, "accountant", fees.list_heads) is not None


def test_FR_FEE_001_alongside_mode_hides_fees_until_the_owner_turns_them_on(
    school: Any, admin_engine: Engine
) -> None:
    from app.tenancy import service as tenancy

    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE core.tenants SET settings = jsonb_set(settings, '{operating_mode}', "
                "'\"alongside\"') WHERE id = :t"
            ),
            {"t": school.tenant_id},
        )
    try:
        with tenant_session(school.tenant_id) as s:
            assert tenancy.module_enabled(s, "fees") is True  # fees_enabled is on
            assert "fees" not in tenancy.get_tenant(s).modules_hidden
            assert "tally" in tenancy.get_tenant(s).modules_hidden
        F.set_setting(admin_engine, school.tenant_id, enabled=False)
        with tenant_session(school.tenant_id) as s:
            assert tenancy.module_enabled(s, "fees") is False
            assert "fees" in tenancy.get_tenant(s).modules_hidden
    finally:
        F.set_setting(admin_engine, school.tenant_id, enabled=True)
        with admin_engine.begin() as c:
            c.execute(
                text(
                    "UPDATE core.tenants SET settings = jsonb_set(settings, '{operating_mode}', "
                    "'\"full\"') WHERE id = :t"
                ),
                {"t": school.tenant_id},
            )


# --- heads and plans (US-2101) -------------------------------------------------------------------


def test_US_2101_AC1_heads_and_plan(school: Any) -> None:
    setup = F.ensure_setup(school)
    plan = F.call(school, "accountant", fees.get_plan, setup.plan_id)
    assert plan.status == "active"
    assert plan.annual_total == D("9000.00")
    assert [i.total for i in plan.instalments] == [D("4500.00"), D("4500.00")]
    with pytest.raises(Conflict) as err:
        F.call(
            school,
            "accountant",
            fees.create_head,
            HeadCreate(code="tui", name="Again", kind="tuition"),
        )
    assert err.value.code == "head_code_taken"


def test_FR_FEE_003_instalments_must_add_up(school: Any) -> None:
    setup = F.ensure_setup(school)
    with pytest.raises(ValidationFailed) as err:
        F.call(
            school,
            "accountant",
            fees.create_plan,
            FeePlanCreate(
                academic_year_id=school.ids["year"],
                class_id=school.ids["class_x"],
                category_code="odd_sum",
                name="Wrong",
                lines=[PlanLineIn(head_id=setup.tui, annual_amount=D("1000.00"))],
                instalments=[
                    InstalmentIn(
                        instalment_no=1,
                        label="T1",
                        due_on=setup.second_due,
                        amounts={setup.tui: D("999.99")},
                    )
                ],
            ),
        )
    assert err.value.errors[0]["code"] == "instalments_do_not_add_up"


def test_FR_FEE_003_due_dates_inside_the_year(school: Any) -> None:
    setup = F.ensure_setup(school)
    with pytest.raises(ValidationFailed) as err:
        F.call(
            school,
            "accountant",
            fees.create_plan,
            FeePlanCreate(
                academic_year_id=school.ids["year"],
                class_id=school.ids["class_x"],
                category_code="late_date",
                name="Wrong",
                lines=[PlanLineIn(head_id=setup.tui, annual_amount=D("1000.00"))],
                instalments=[
                    InstalmentIn(
                        instalment_no=1,
                        label="T1",
                        due_on=dt.date(2027, 6, 1),
                        amounts={setup.tui: D("1000.00")},
                    )
                ],
            ),
        )
    assert err.value.errors[0]["code"] == "due_outside_year"


def test_FR_FEE_004_active_plan_is_frozen_new_version_proposes_adjustments(school: Any) -> None:
    setup = F.ensure_setup(school)
    plan_id, account_id = _late_plan(school, LateFeeRule(), "versioned")
    plan = F.call(school, "accountant", fees.get_plan, plan_id)
    with pytest.raises(Conflict) as err:
        F.call(
            school,
            "accountant",
            fees.update_plan,
            plan.id,
            FeePlanPatch(name="Edited"),
            expected_version=plan.version,
        )
    assert err.value.code == "plan_in_use"
    draft = F.call(school, "accountant", fees.new_version, plan.id)
    assert draft.status == "draft"
    assert draft.previous_plan_id == plan.id
    draft = F.call(
        school,
        "accountant",
        fees.update_plan,
        draft.id,
        FeePlanPatch(
            lines=[PlanLineIn(head_id=setup.tui, annual_amount=D("1100.00"))],
            instalments=[
                InstalmentIn(
                    instalment_no=1,
                    label="Term 1",
                    due_on=setup.first_due,
                    amounts={setup.tui: D("1100.00")},
                )
            ],
        ),
        expected_version=draft.version,
    )
    active = F.call(
        school, "accountant", fees.activate_plan, draft.id, expected_version=draft.version
    )
    assert active.status == "active"
    assert F.call(school, "accountant", fees.get_plan, plan.id).status == "superseded"
    diffs = F.call(school, "accountant", fees.plan_differences, active.id)
    mine = [d for d in diffs.items if d.account_id == account_id]
    assert [(d.instalment_no, d.difference) for d in mine] == [(1, D("100.00"))]
    before = _account(school, account_id).totals.charged
    out = F.call(
        school,
        "accountant",
        fees.propose_adjustments,
        active.id,
        ProposeAdjustmentsIn(reason="Tuition increased by the management"),
    )
    assert out.proposed == 1
    again = F.call(
        school,
        "accountant",
        fees.propose_adjustments,
        active.id,
        ProposeAdjustmentsIn(reason="Tuition increased by the management"),
    )
    assert again.proposed == 0
    # Charges are never rewritten; the adjustment waits for approval.
    acc = _account(school, account_id)
    assert acc.totals.charged == before
    assert acc.plan_id == active.id
    pending = F.call(school, "accountant", fees.list_adjustments, account_id)
    assert [a.status for a in pending if a.source_plan_id == active.id] == ["pending"]


def test_US_2101_AC2_AC3_apply_is_idempotent_and_skips_past_instalments(school: Any) -> None:
    setup = F.ensure_setup(school)
    sid = F.student(school, "section_9c")
    first = F.call(
        school,
        "accountant",
        fees.apply_plan,
        setup.plan_id,
        PlanApplyIn(section_ids=[school.ids["section_9c"]], dry_run=True),
    )
    assert first.dry_run
    assert first.accounts_created >= 1
    out = F.call(
        school,
        "accountant",
        fees.apply_plan,
        setup.plan_id,
        PlanApplyIn(section_ids=[school.ids["section_9c"]]),
    )
    assert out.accounts_created == first.accounts_created
    again = F.call(
        school,
        "accountant",
        fees.apply_plan,
        setup.plan_id,
        PlanApplyIn(section_ids=[school.ids["section_9c"]]),
    )
    assert again.accounts_created == 0
    assert again.charges_posted == 0
    with tenant_session(school.tenant_id) as s:
        acc_raw: object = s.execute(
            text("SELECT id FROM fin.student_fee_accounts WHERE student_id = :s"), {"s": sid}
        ).scalar_one()
    acc = uuid.UUID(str(acc_raw))
    out_acc = _account(school, acc)
    # Admitted today: only the instalment due after admission (AC3).
    assert {x.instalment_no for x in out_acc.lines} == {2}
    # Asking for past instalments adds instalment 1 once.
    F.call(
        school,
        "accountant",
        fees.apply_plan,
        setup.plan_id,
        PlanApplyIn(section_ids=[school.ids["section_9c"]], include_past_instalments=True),
    )
    assert {x.instalment_no for x in _account(school, acc).lines} == {1, 2}


# --- receipts (US-2102) ---------------------------------------------------------------------------


def test_US_2102_AC3_receipt_number_ledger_and_frozen_content(
    school: Any, admin_engine: Engine
) -> None:
    account_id = F.account(school)
    before = _account(school, account_id)
    assert before.totals.collectable == D("7000.00")  # BUS is taxable: never collected
    r = F.issue(school, account_id, "3500.00")
    assert r.receipt_number.startswith("RCT/2026-27/")
    assert r.receipt_date == F.today()
    assert sum((x.amount for x in r.lines), D(0)) == D("3500.00")
    # Oldest due first: Term 1 tuition and exam.
    assert {(x.instalment_no, x.head_code) for x in r.lines} == {(1, "TUI"), (1, "EXM")}
    after = _account(school, account_id)
    assert after.totals.paid == D("3500.00")
    assert after.totals.balance == before.totals.balance - D("3500.00")
    sha = F.scalar(
        admin_engine, "SELECT encode(content_sha256, 'hex') FROM fin.receipts WHERE id = :i", i=r.id
    )
    content = F.scalar(admin_engine, "SELECT content FROM fin.receipts WHERE id = :i", i=r.id)
    from app.fees.receipts import content_digest

    assert content_digest(content).hex() == sha == r.content_sha256
    assert content["student"]["class_section"]
    assert content["mode"] == "cash"


def test_US_2102_AC5_no_advances(school: Any) -> None:
    account_id = F.account(school)
    with pytest.raises(Conflict) as err:
        F.issue(school, account_id, "7000.01")
    assert err.value.code == "amount_exceeds_due"


def test_FR_FEE_023_taxable_head_is_not_collectable(school: Any) -> None:
    setup = F.ensure_setup(school)
    account_id = F.account(school)
    with pytest.raises(Conflict) as err:
        F.issue(
            school,
            account_id,
            "100.00",
            allocations=[AllocationIn(instalment_no=1, head_id=setup.bus, amount=D("100.00"))],
        )
    assert err.value.code == "head_not_collectable"


def test_FR_FEE_010_given_allocation_must_add_up_and_stay_within_due(school: Any) -> None:
    setup = F.ensure_setup(school)
    account_id = F.account(school)
    with pytest.raises(ValidationFailed) as err:
        F.issue(
            school,
            account_id,
            "600.00",
            allocations=[AllocationIn(instalment_no=2, head_id=setup.exm, amount=D("500.00"))],
        )
    assert err.value.errors[0]["code"] == "allocations_do_not_add_up"
    with pytest.raises(ValidationFailed):
        F.issue(
            school,
            account_id,
            "600.00",
            allocations=[AllocationIn(instalment_no=2, head_id=setup.exm, amount=D("600.00"))],
        )
    r = F.issue(
        school,
        account_id,
        "500.00",
        allocations=[AllocationIn(instalment_no=2, head_id=setup.exm, amount=D("500.00"))],
    )
    assert [(x.instalment_no, x.head_code) for x in r.lines] == [(2, "EXM")]


def test_FR_FEE_010_modes_and_their_details(school: Any) -> None:
    account_id = F.account(school)
    cheque = F.issue(
        school,
        account_id,
        "100.00",
        mode="cheque",
        mode_details=ModeDetailsIn(
            cheque_number="123456", bank_name="Synthetic Bank", cheque_date=F.today()
        ),
    )
    assert cheque.mode_details["cheque_number"] == "123456"
    with pytest.raises(ValidationFailed):
        F.issue(
            school,
            account_id,
            "100.00",
            mode="cheque",
            mode_details=ModeDetailsIn(cheque_number="12", bank_name="B", cheque_date=F.today()),
        )
    bank = F.issue(
        school,
        account_id,
        "100.00",
        mode="bank_transfer",
        mode_details=ModeDetailsIn(reference="abcd12345678"),
    )
    assert bank.mode_details["reference"] == "ABCD12345678"
    with pytest.raises(ValidationFailed) as err:
        F.issue(school, account_id, "100.00", mode="upi_qr", utr="12345")
    assert err.value.errors[0]["code"] == "invalid_utr"


def test_US_2102_AC6_utr_on_one_issued_receipt_only(school: Any) -> None:
    account_id = F.account(school)
    utr = F.utr()
    first = F.issue(school, account_id, "100.00", mode="upi_qr", utr=utr)
    assert first.utr == utr
    with pytest.raises(Conflict) as err:
        F.issue(school, F.account(school), "100.00", mode="upi_qr", utr=utr)
    assert err.value.code == "utr_in_use"
    assert first.receipt_number in (err.value.detail or "")


def test_US_2102_AC6_full_aadhaar_in_a_typed_note_is_refused(school: Any) -> None:
    account_id = F.account(school)
    with pytest.raises(ValidationFailed) as err:
        F.issue(
            school,
            account_id,
            "100.00",
            mode="cheque",
            mode_details=ModeDetailsIn(
                cheque_number="654321", bank_name="Bank 499118665246", cheque_date=F.today()
            ),
        )
    assert err.value.errors[0]["code"] == "aadhaar_full_number_rejected"


def test_FR_FEE_024_scoped_holder_is_refused(school: Any) -> None:
    account_id = F.account(school)
    ctx = F.ctx(
        school,
        school.people["scoped_collector"],
        "office_staff",
        section_ids=frozenset({school.ids["section_9a"]}),
    )
    import dataclasses

    ctx = dataclasses.replace(
        ctx, scoped_permissions=frozenset({"fee.collect", "student.read_basic"})
    )
    with pytest.raises(Forbidden):
        F.call(school, "office_staff", fees.get_account, account_id, as_ctx=ctx)


# --- search, statement, receipts list ------------------------------------------------------------


def test_US_2102_AC1_counter_search_by_admission_number(school: Any) -> None:
    sid = F.student(school)
    account_id = F.account(school, sid)
    with tenant_session(school.tenant_id) as s:
        adm: object = s.execute(
            text("SELECT admission_no FROM sis.students WHERE id = :i"), {"i": sid}
        ).scalar_one()
    found = F.call(school, "office_staff", fees.search_accounts, AccountSearch(query=adm))
    assert [a.account_id for a in found][:1] == [account_id]


def test_FR_FEE_021_statement_has_running_balance(school: Any) -> None:
    account_id = F.account(school)
    F.issue(school, account_id, "250.00")
    st = F.call(school, "accountant", fees.statement, account_id)
    running = D(0)
    for e in st.entries:
        running += e.amount
        assert e.balance == running
    assert st.balance == _account(school, account_id).totals.balance
    assert any(e.entry_kind == "payment" and e.receipt_number for e in st.entries)


# --- cancellations (US-2105) ---------------------------------------------------------------------


def test_US_2105_cancellation_is_maker_checker_and_keeps_the_number(
    school: Any, admin_engine: Engine
) -> None:
    account_id = F.account(school)
    before = _account(school, account_id).totals.balance
    r = F.issue(school, account_id, "400.00", role="accountant")
    req = F.call(
        school,
        "accountant",
        fees.request_cancellation,
        r.id,
        CancellationRequest(reason_code="wrong_amount", reason="Typed the wrong amount"),
    )
    with pytest.raises(Forbidden) as err:
        F.call(
            school,
            "accountant",
            fees.approve_cancellation,
            req.id,
            DecisionIn(),
            expected_version=None,
        )
    assert err.value.code == "self_approval_forbidden"
    done = F.call(
        school, "principal", fees.approve_cancellation, req.id, DecisionIn(), expected_version=None
    )
    assert done.status == "approved"
    assert done.cancelled_on == F.today()
    got = F.call(school, "accountant", fees.get_receipt, r.id)
    assert got.status == "cancelled"
    assert got.receipt_number == r.receipt_number
    assert _account(school, account_id).totals.balance == before
    page = F.call(school, "accountant", fees.print_page, r.id)
    assert "CANCELLED on" in page
    with pytest.raises(Conflict):
        F.call(
            school,
            "accountant",
            fees.request_cancellation,
            r.id,
            CancellationRequest(reason_code="other", reason="Second time asking"),
        )


def test_US_2105_reject_and_withdraw(school: Any) -> None:
    account_id = F.account(school)
    r = F.issue(school, account_id, "100.00")
    req = F.call(
        school,
        "office_staff",
        fees.request_cancellation,
        r.id,
        CancellationRequest(reason_code="duplicate_entry", reason="Entered this twice"),
    )
    with pytest.raises(ValidationFailed):
        F.call(
            school,
            "accountant",
            fees.reject_cancellation,
            req.id,
            RejectIn(reason="short"),
            expected_version=None,
        )
    out = F.call(
        school,
        "accountant",
        fees.reject_cancellation,
        req.id,
        RejectIn(reason="The receipt is correct"),
        expected_version=None,
    )
    assert out.status == "rejected"
    again = F.call(
        school,
        "office_staff",
        fees.request_cancellation,
        r.id,
        CancellationRequest(reason_code="duplicate_entry", reason="Entered this twice"),
    )
    with pytest.raises(Forbidden) as err:
        F.call(school, "accountant", fees.withdraw_cancellation, again.id)
    assert err.value.code == "not_requester"
    gone = F.call(school, "office_staff", fees.withdraw_cancellation, again.id)
    assert gone.status == "withdrawn"


# --- concessions and adjustments (US-2103, US-2104) ----------------------------------------------


def test_US_2103_concession_needs_another_person(school: Any) -> None:
    setup = F.ensure_setup(school)
    account_id = F.account(school)
    req = F.call(
        school,
        "office_admin",
        fees.request_concession,
        account_id,
        ConcessionCreate(
            concession_type="sibling",
            basis="percent",
            value=D("10"),
            targets=[
                ConcessionTarget(instalment_no=1, head_id=setup.tui),
                ConcessionTarget(instalment_no=2, head_id=setup.tui),
            ],
            reason="Second child of the same family",
        ),
    )
    assert req.status == "pending"
    with pytest.raises(Forbidden):
        F.call(
            school,
            "office_admin",
            fees.approve_concession,
            req.id,
            DecisionIn(),
            expected_version=None,
        )
    before = _account(school, account_id).totals.balance
    out = F.call(
        school, "principal", fees.approve_concession, req.id, DecisionIn(), expected_version=None
    )
    assert out.status == "approved"
    acc = _account(school, account_id)
    assert acc.totals.balance == before - out.total_amount
    assert _line(acc, 1, setup.tui).concession == _line(acc, 2, setup.tui).concession > 0


def test_US_2103_AC2_self_approval_refused_for_a_holder_of_both(school: Any) -> None:
    setup = F.ensure_setup(school)
    account_id = F.account(school)
    req = F.call(
        school,
        "principal",
        fees.request_concession,
        account_id,
        ConcessionCreate(
            concession_type="merit",
            basis="amount",
            value=D("100.00"),
            targets=[ConcessionTarget(instalment_no=2, head_id=setup.exm)],
            reason="Merit scholarship for this term",
        ),
    )
    with pytest.raises(Forbidden) as err:
        F.call(
            school,
            "principal",
            fees.approve_concession,
            req.id,
            DecisionIn(),
            expected_version=None,
        )
    assert err.value.code == "self_approval_forbidden"
    out = F.call(
        school, "accountant", fees.approve_concession, req.id, DecisionIn(), expected_version=None
    )
    assert out.total_amount == D("100.00")


def test_US_2104_adjustment_and_late_fee_waiver_maker_checker(school: Any) -> None:
    setup = F.ensure_setup(school)
    account_id = F.account(school)
    adj = F.call(
        school,
        "accountant",
        fees.request_adjustment,
        account_id,
        AdjustmentCreate(
            instalment_no=1,
            head_id=setup.tui,
            amount=D("-50.00"),
            reason_code="correction",
            reason="Rounded off by the management",
        ),
    )
    with pytest.raises(Forbidden):
        F.call(
            school,
            "accountant",
            fees.approve_adjustment,
            adj.id,
            DecisionIn(),
            expected_version=None,
        )
    out = F.call(
        school,
        "accountant",
        fees.approve_adjustment,
        adj.id,
        DecisionIn(),
        expected_version=None,
        person=school.people["accountant2"],
    )
    assert out.status == "approved"
    assert _line(_account(school, account_id), 1, setup.tui).adjustment == D("-50.00")
    with pytest.raises(ValidationFailed):
        F.call(
            school,
            "accountant",
            fees.request_adjustment,
            account_id,
            AdjustmentCreate(
                instalment_no=1,
                head_id=None,
                amount=D("-10.00"),
                reason_code="late_fee_waiver",
                reason="No late fee was charged here",
            ),
        )


# --- late fees (FR-FEE-009) ----------------------------------------------------------------------


def _late_plan(school: Any, rule: LateFeeRule, category: str) -> tuple[uuid.UUID, uuid.UUID]:
    setup = F.ensure_setup(school)
    plan = F.call(
        school,
        "accountant",
        fees.create_plan,
        FeePlanCreate(
            academic_year_id=school.ids["year"],
            class_id=school.ids["class_x"],
            category_code=category,
            name=f"Class X {category}",
            lines=[PlanLineIn(head_id=setup.tui, annual_amount=D("1000.00"))],
            instalments=[
                InstalmentIn(
                    instalment_no=1,
                    label="Term 1",
                    due_on=setup.first_due,
                    amounts={setup.tui: D("1000.00")},
                )
            ],
            late_fee_rule=rule,
        ),
    )
    plan = F.call(school, "accountant", fees.activate_plan, plan.id, expected_version=plan.version)
    sid = F.student(school, "section_10a")
    F.call(
        school,
        "accountant",
        fees.apply_plan,
        plan.id,
        PlanApplyIn(section_ids=[school.ids["section_10a"]], include_past_instalments=True),
    )
    with tenant_session(school.tenant_id) as s:
        acc_raw: object = s.execute(
            text("SELECT id FROM fin.student_fee_accounts WHERE student_id = :s"), {"s": sid}
        ).scalar_one()
    return plan.id, uuid.UUID(str(acc_raw))


def _post(school: Any) -> dict[str, int]:
    with tenant_session(school.tenant_id) as s:
        result: dict[str, int] = fees.post_late_fees(s)
    return result


def test_FR_FEE_009_flat_late_fee_posted_once(school: Any) -> None:
    _, account_id = _late_plan(
        school,
        LateFeeRule(kind="flat_per_instalment", amount=D("100.00"), grace_days=5),
        "flat_rule",
    )
    _post(school)
    _post(school)
    late = _line(_account(school, account_id), 1, None)
    assert late.late_fee == D("100.00")


def test_FR_FEE_009_per_day_late_fee_capped_never_compounding(school: Any) -> None:
    _, account_id = _late_plan(
        school,
        LateFeeRule(kind="per_day", amount=D("7.00"), grace_days=0, cap=D("50.00")),
        "day_rule",
    )
    _post(school)
    once = _line(_account(school, account_id), 1, None).late_fee
    _post(school)
    assert _line(_account(school, account_id), 1, None).late_fee == once == D("50.00")


# --- refunds (US-2106) ---------------------------------------------------------------------------


def test_US_2106_cash_refund_voucher_on_approval(school: Any) -> None:
    account_id = F.account(school)
    r = F.issue(school, account_id, "800.00")
    with pytest.raises(Conflict) as err:
        F.call(
            school,
            "accountant",
            fees.request_refund,
            r.id,
            RefundCreate(amount=D("800.01"), mode="cash", reason="Student left the school"),
        )
    assert err.value.code == "refund_exceeds_paid"
    req = F.call(
        school,
        "accountant",
        fees.request_refund,
        r.id,
        RefundCreate(amount=D("300.00"), mode="cash", reason="Student left the school"),
    )
    with pytest.raises(Forbidden):
        F.call(
            school, "accountant", fees.approve_refund, req.id, DecisionIn(), expected_version=None
        )
    out = F.call(
        school, "principal", fees.approve_refund, req.id, DecisionIn(), expected_version=None
    )
    assert out.status == "processed"
    assert out.voucher_number
    assert out.voucher_number.startswith("RFD/2026-27/")
    got = F.call(school, "accountant", fees.get_receipt, r.id)
    assert got.refunded == D("300.00")
    with pytest.raises(Conflict) as err:
        F.call(
            school,
            "accountant",
            fees.request_cancellation,
            r.id,
            CancellationRequest(reason_code="other", reason="Cancel after a refund"),
        )
    assert err.value.code == "receipt_has_refunds"
    page = F.call(school, "accountant", fees.voucher_page, out.id)
    assert "REFUND VOUCHER" in page
    assert out.voucher_number in page
    assert r.receipt_number in page
    pending = F.call(
        school,
        "accountant",
        fees.request_refund,
        r.id,
        RefundCreate(amount=D("1.00"), mode="cash", reason="Second synthetic refund request"),
    )
    with pytest.raises(Conflict) as err:
        F.call(school, "accountant", fees.voucher_page, pending.id)
    assert err.value.code == "voucher_not_ready"


# --- reports, day close, dues (US-2110) ----------------------------------------------------------


def test_US_2110_daily_report_and_day_close(school: Any, admin_engine: Engine) -> None:
    account_id = F.account(school)
    r = F.issue(school, account_id, "123.00")
    report = F.call(school, "accountant", fees.daily_report, None)
    assert r.id in {x.id for x in report.receipts}
    assert report.cash_to_hand_over >= D("123.00")
    filename, csv_text = F.call(school, "accountant", fees.daily_csv, None)
    assert r.receipt_number in csv_text
    assert filename.endswith(".csv")
    # Close a day: later receipts dated that day are refused. Close today in a separate school
    # day by moving the clock is not possible here, so close and reopen is checked on today and
    # undone by the purge-free path: the test runs last-ish with its own account.
    closed = F.call(school, "accountant", fees.close_day, F.today())
    assert closed.closed is not None
    with pytest.raises(Conflict) as err:
        F.issue(school, account_id, "1.00")
    assert err.value.code == "day_closed"
    with pytest.raises(Conflict):
        F.call(school, "accountant", fees.close_day, F.today())
    # Reopen for the other tests (the day-close row is append-only for the app; removed as the
    # test superuser, never by the app).
    with admin_engine.begin() as c:
        c.execute(text("ALTER TABLE fin.day_closes DISABLE TRIGGER day_closes_append_only"))
        c.execute(text("DELETE FROM fin.day_closes WHERE tenant_id = :t"), {"t": school.tenant_id})
        c.execute(text("ALTER TABLE fin.day_closes ENABLE TRIGGER day_closes_append_only"))


def test_FR_FEE_020_dues_list_oldest_first(school: Any) -> None:
    account_id = F.account(school)
    dues = F.call(
        school, "accountant", fees.dues_list, section_id=school.ids["section_9a"], class_id=None
    )
    row = next(r for r in dues.rows if r.account_id == account_id)
    assert row.overdue > 0
    assert row.oldest_due_on is not None
    dates = [r.oldest_due_on for r in dues.rows]
    assert dates == sorted(dates)


def test_fee_settings_round_trip(school: Any) -> None:
    current = F.call(school, "accountant", fees.get_settings)
    out = F.call(
        school,
        "accountant",
        fees.put_settings,
        FeeSettingsIn(
            paper_size="A4", gst_note="Exempt", reminder_offsets=[0, -3], min_reminder_amount=D("0")
        ),
        expected_version=current.version,
    )
    assert out.paper_size == "A4"
    assert out.reminder_offsets == [-3, 0]
    F.call(
        school,
        "accountant",
        fees.put_settings,
        FeeSettingsIn(
            paper_size="A5",
            gst_note="Education services: exempt from GST",
            reminder_offsets=[-3, 0, 7],
            min_reminder_amount=D("0"),
        ),
        expected_version=out.version,
    )


def test_head_patch_needs_version(school: Any) -> None:
    setup = F.ensure_setup(school)
    heads = F.call(school, "accountant", fees.list_heads)
    tui = next(h for h in heads if h.id == setup.tui)
    out = F.call(
        school,
        "accountant",
        fees.update_head,
        tui.id,
        HeadPatch(sort_order=5),
        expected_version=tui.version,
    )
    assert out.sort_order == 5


# --- data contract for other modules -------------------------------------------------------------


def test_data_contract_account_summary_and_dues(school: Any) -> None:
    sid = F.student(school)
    F.account(school, sid)
    online = __import__("sys").modules["sos_test_fees_online_support"]
    online.disconnect_if_connected(school)
    with tenant_session(school.tenant_id) as s:
        summary = fees.account_summary(s, [sid])[sid]
        dues = fees.dues_for_student(s, sid)
        assert fees.payments_connected(s) is False
        with pytest.raises(Conflict) as err:
            fees.create_payment_intent(s, summary.account_id, [1], created_via="parent_page")
    assert err.value.code == "payments_not_connected"
    # R2-2D: once the school's Razorpay account is connected, the parent page gets a link.
    online.connect(school)
    with tenant_session(school.tenant_id) as s:
        intent_id = fees.create_payment_intent(
            s, summary.account_id, [1], created_via="parent_page"
        )
        link = fees.payment_link(s, intent_id)
    assert link is not None
    assert link.amount == D("3500.00")
    assert link.status == "created"
    assert link.short_url.startswith("https://")
    assert dues is not None
    assert dues.total_due == D("7000.00")
    assert summary.overdue == D("3500.00")
