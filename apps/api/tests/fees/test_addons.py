"""Per-student add-on fees: transport bands, hostel, activities (FM-02; FR-FEE-081..100; D85).

Runs in its own synthetic school (``addon_school``) so its plans, heads and late-fee rule never
touch the shared fee school: heads TUI (tuition), VAN (transport, GST exempt), HST (other:
hostel), EXM (exam, not an add-on kind) and TAX (transport, taxable); a Class IX plan of TUI
3,000 per term with a flat late fee of 50 per overdue instalment (term 1 overdue, term 2 due in
30 days), and a Class X plan of TUI only (for plan versions).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.core.db import tenant_session
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.fees import service as fees
from app.fees.addons_schemas import (
    AddonAssignIn,
    AddonCreate,
    AddonPatch,
    AddonUnassignIn,
)
from app.fees.schemas import (
    AllocationIn,
    DecisionIn,
    FeePlanCreate,
    FeePlanPatch,
    HeadCreate,
    InstalmentIn,
    LateFeeRule,
    PlanApplyIn,
    PlanLineIn,
)

pytestmark = pytest.mark.db

F: Any = __import__("sys").modules["sos_test_fees_support"]
W = F.W
D = Decimal
REASON = "Student moved closer to the school"


@dataclasses.dataclass
class Heads:
    tui: uuid.UUID
    van: uuid.UUID
    hst: uuid.UUID
    exm: uuid.UUID
    tax: uuid.UUID
    plan_ix: uuid.UUID
    plan_x: uuid.UUID


_HEADS: dict[uuid.UUID, Heads] = {}
_labels = iter(range(1, 10_000))


def _role(admin: Engine, tenant_id: uuid.UUID, key: str, perms: tuple[str, ...]) -> None:
    role_id = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te) "
                "VALUES (:r, :t, :k, 'Synthetic transport in-charge', 'కృత్రిమ')"
            ),
            {"r": role_id, "t": tenant_id, "k": key},
        )
        for perm in perms:
            c.execute(
                text(
                    "INSERT INTO core.role_permissions (tenant_id, role_id, permission_key) "
                    "VALUES (:t, :r, :p)"
                ),
                {"t": tenant_id, "r": role_id, "p": perm},
            )


@pytest.fixture(scope="module")
def addon_school(world: Any, admin_engine: Engine) -> Any:
    F.install()
    s = W.School(W.provision_school())
    add = W.add_member
    s.people["owner"] = add(admin_engine, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in ("principal", "office_admin", "office_staff", "accountant"):
        s.people[role] = add(admin_engine, s.tenant_id, [role])
    s.people["class_teacher"] = add(
        admin_engine, s.tenant_id, ["class_teacher"], scopes=[("section", s.ids["section_9a"])]
    )
    _role(admin_engine, s.tenant_id, "transport_incharge", ("student.read_basic", "fee.addon.read"))
    s.people["incharge"] = add(
        admin_engine, s.tenant_id, ["transport_incharge"], scopes=[("school", None)]
    )
    F.enable(admin_engine, s.tenant_id)
    return s


def _plan(school: Any, class_key: str, name: str, rule: LateFeeRule, tui: uuid.UUID) -> Any:
    first, second = F.due_dates()
    plan = F.call(
        school,
        "accountant",
        fees.create_plan,
        FeePlanCreate(
            academic_year_id=school.ids["year"],
            class_id=school.ids[class_key],
            name=name,
            lines=[PlanLineIn(head_id=tui, annual_amount=D("6000.00"))],
            instalments=[
                InstalmentIn(
                    instalment_no=1, label="Term 1", due_on=first, amounts={tui: D("3000.00")}
                ),
                InstalmentIn(
                    instalment_no=2, label="Term 2", due_on=second, amounts={tui: D("3000.00")}
                ),
            ],
            late_fee_rule=rule,
        ),
    )
    return F.call(school, "accountant", fees.activate_plan, plan.id, expected_version=plan.version)


def _setup(school: Any) -> Heads:
    found = _HEADS.get(school.tenant_id)
    if found is not None:
        return found
    ids = {}
    for code, name, kind, exempt in (
        ("TUI", "Tuition fee", "tuition", True),
        ("VAN", "Transport fee", "transport", True),
        ("HST", "Hostel fee", "other", True),
        ("EXM", "Exam fee", "exam", True),
        ("TAX", "Hired bus fee", "transport", False),
    ):
        ids[code] = F.call(
            school,
            "accountant",
            fees.create_head,
            HeadCreate(code=code, name=name, kind=kind, gst_exempt=exempt),
        ).id
    late = LateFeeRule(kind="flat_per_instalment", amount=D("50.00"), grace_days=0)
    ix = _plan(school, "class_ix", "Class IX 2026-27", late, ids["TUI"])
    x = _plan(school, "class_x", "Class X 2026-27", LateFeeRule(), ids["TUI"])
    out = Heads(ids["TUI"], ids["VAN"], ids["HST"], ids["EXM"], ids["TAX"], ix.id, x.id)
    _HEADS[school.tenant_id] = out
    return out


def _account(school: Any, section: str = "section_9a") -> tuple[uuid.UUID, uuid.UUID]:
    """A fresh student of ``section`` with a fee account charged both terms."""
    h = _setup(school)
    sid = F.student(school, section)
    plan = h.plan_ix if section == "section_9a" else h.plan_x
    F.call(
        school,
        "accountant",
        fees.apply_plan,
        plan,
        PlanApplyIn(section_ids=[school.ids[section]], include_past_instalments=True),
    )
    with tenant_session(school.tenant_id) as s:
        raw: object = s.execute(
            text("SELECT id FROM fin.student_fee_accounts WHERE student_id = :s"), {"s": sid}
        ).scalar_one()
    return sid, uuid.UUID(str(raw))


def _addon(school: Any, head: uuid.UUID | None = None, amount: str = "1500.00") -> Any:
    h = _setup(school)
    return F.call(
        school,
        "accountant",
        fees.create_addon,
        AddonCreate(
            academic_year_id=school.ids["year"],
            head_id=head or h.van,
            label=f"Band {next(_labels)} (5-10 km)",
            amounts={1: D(amount), 2: D(amount)},
        ),
    )


def _assign(school: Any, addon_id: uuid.UUID, sid: uuid.UUID, start: int = 1, **kw: Any) -> Any:
    return F.call(
        school,
        "accountant",
        fees.assign_addon,
        addon_id,
        AddonAssignIn(student_ids=[sid], start_instalment=start, **kw),
    )


def _line(school: Any, account_id: uuid.UUID, no: int, head: uuid.UUID | None) -> Any:
    out = F.call(school, "accountant", fees.get_account, account_id)
    return next((x for x in out.lines if x.instalment_no == no and x.head_id == head), None)


def _pending_removals(admin: Engine, account_id: uuid.UUID) -> list[Any]:
    with admin.connect() as c:
        return list(
            c.execute(
                text(
                    "SELECT id, instalment_no, amount, status, addon_assignment_id "
                    "FROM fin.adjustments WHERE account_id = :a AND reason_code = 'addon_removal' "
                    "ORDER BY instalment_no"
                ),
                {"a": account_id},
            )
        )


# --- templates (FR-FEE-081..083) -----------------------------------------------------------------


def test_FR_FEE_081_template_needs_a_collectable_head_of_an_allowed_kind(
    addon_school: Any, admin_engine: Engine
) -> None:
    h = _setup(addon_school)
    for head, code in ((h.exm, "head_kind_not_allowed"), (h.tax, "head_not_collectable")):
        with pytest.raises(ValidationFailed) as exc:
            _addon(addon_school, head)
        assert exc.value.errors[0]["code"] == code
    out = _addon(addon_school)
    assert out.amounts == {1: D("1500.00"), 2: D("1500.00")}
    assert out.total == D("3000.00")
    assert out.head_name == "Transport fee"
    hostel = _addon(addon_school, h.hst, "9000.00")
    assert hostel.head_kind == "other"
    with pytest.raises(Conflict) as dup:
        F.call(
            addon_school,
            "accountant",
            fees.create_addon,
            AddonCreate(
                academic_year_id=addon_school.ids["year"],
                head_id=h.van,
                label=out.label,
                amounts={1: D("1.00")},
            ),
        )
    assert dup.value.code == "addon_label_taken"
    events = W.audit_events(admin_engine, addon_school.tenant_id, "fee.addon.created")
    mine = [e for e in events if e["resource_id"] == out.id]
    assert mine
    assert mine[0]["summary"]["total"] == "3000.00"
    listed = F.call(addon_school, "accountant", fees.list_addons)
    assert out.id in {i.id for i in listed.items}
    assert listed.amounts_shown is True
    assert "GST" in listed.gst_note


def test_FR_FEE_082_only_fee_manage_creates(addon_school: Any) -> None:
    h = _setup(addon_school)
    data = AddonCreate(
        academic_year_id=addon_school.ids["year"],
        head_id=h.van,
        label="Refused band",
        amounts={1: D("10.00")},
    )
    for role in ("office_admin", "office_staff", "class_teacher"):
        with pytest.raises(Forbidden):
            F.call(addon_school, role, fees.create_addon, data)


def test_FR_FEE_083_amounts_freeze_once_anyone_was_assigned(addon_school: Any) -> None:
    addon = _addon(addon_school)
    patched = F.call(
        addon_school,
        "accountant",
        fees.update_addon,
        addon.id,
        AddonPatch(amounts={1: D("1600.00"), 2: D("1600.00")}),
        expected_version=addon.version,
    )
    assert patched.total == D("3200.00")
    sid, _ = _account(addon_school)
    _assign(addon_school, addon.id, sid)
    with pytest.raises(Conflict) as exc:
        F.call(
            addon_school,
            "accountant",
            fees.update_addon,
            addon.id,
            AddonPatch(amounts={1: D("1.00")}),
            expected_version=patched.version,
        )
    assert exc.value.code == "addon_in_use"
    off = F.call(
        addon_school,
        "accountant",
        fees.update_addon,
        addon.id,
        AddonPatch(active=False, label=f"{addon.label} (old)"),
        expected_version=patched.version,
    )
    assert off.active is False
    assert off.assigned == 1
    other, _ = _account(addon_school)
    with pytest.raises(Conflict) as inactive:
        _assign(addon_school, addon.id, other)
    assert inactive.value.code == "addon_inactive"


# --- assign (FR-FEE-084..088) --------------------------------------------------------------------


def test_FR_FEE_084_assign_posts_charges_shown_as_normal_lines(
    addon_school: Any, admin_engine: Engine
) -> None:
    h = _setup(addon_school)
    addon = _addon(addon_school)
    sid, account = _account(addon_school)
    out = _assign(addon_school, addon.id, sid)
    assert (out.assigned, out.charges_posted, out.skipped) == (1, 2, [])
    again = _assign(addon_school, addon.id, sid)
    assert again.charges_posted == 0
    assert [s.reason for s in again.skipped] == ["already_assigned"]
    # Counter: ordinary lines of the head, due with the plan's instalment dates.
    line = _line(addon_school, account, 1, h.van)
    assert line is not None
    assert (line.head_name, line.charged, line.due, line.collectable) == (
        "Transport fee",
        D("1500.00"),
        D("1500.00"),
        True,
    )
    totals = F.call(addon_school, "accountant", fees.get_account, account).totals
    assert totals.charged == D("9000.00")
    # Receipt: the add-on line is collected like any other.
    receipt = F.issue(
        addon_school,
        account,
        "1500.00",
        allocations=[AllocationIn(instalment_no=1, head_id=h.van, amount=D("1500.00"))],
    )
    assert receipt.amount == D("1500.00")
    assert _line(addon_school, account, 1, h.van).due == D("0.00")
    # Parent dues page: the term-2 add-on is part of what is due.
    with tenant_session(addon_school.tenant_id) as s:
        parent = fees.parent_dues(s, account)
    assert parent is not None
    assert parent.total_due == D("6000.00") + D("1500.00")
    # Dues list (reports) counts it.
    with admin_engine.connect() as c:
        source: object = c.execute(
            text(
                "SELECT count(*) FROM fin.ledger_entries WHERE account_id = :a "
                "AND addon_assignment_id IS NOT NULL AND entry_kind = 'charge'"
            ),
            {"a": account},
        ).scalar_one()
    assert source == 2
    # Audit in the same transaction: counts only, no names.
    events = W.audit_events(admin_engine, addon_school.tenant_id, "fee.addon.assigned")
    mine = [e for e in events if e["resource_id"] == addon.id]
    assert mine[0]["summary"]["charges_posted"] == 2
    assert "Synthetica" not in str(mine)


def test_FR_FEE_085_start_instalment_and_bulk_by_section(addon_school: Any) -> None:
    h = _setup(addon_school)
    addon = _addon(addon_school, h.hst, "4000.00")
    sid, account = _account(addon_school)
    out = _assign(addon_school, addon.id, sid, start=2)
    assert out.charges_posted == 1
    assert _line(addon_school, account, 1, h.hst) is None
    assert _line(addon_school, account, 2, h.hst).charged == D("4000.00")
    # A whole section; dry run first.
    bulk = AddonAssignIn(section_ids=[addon_school.ids["section_9a"]], start_instalment=2)
    dry = F.call(
        addon_school,
        "accountant",
        fees.assign_addon,
        addon.id,
        bulk.model_copy(update={"dry_run": True}),
    )
    real = F.call(addon_school, "accountant", fees.assign_addon, addon.id, bulk)
    assert dry.assigned == real.assigned
    assert real.students >= 1
    assert sid in {s.student_id for s in real.skipped}  # already on it
    # A student without a fee account is skipped, not refused.
    lone = F.student(addon_school, "section_9c")
    skipped = _assign(addon_school, addon.id, lone)
    assert [s.reason for s in skipped.skipped] == ["no_fee_account"]


def test_FR_FEE_086_band_change_replaces_and_credits_with_maker_checker(
    addon_school: Any, admin_engine: Engine
) -> None:
    h = _setup(addon_school)
    band2 = _addon(addon_school, amount="1500.00")
    band3 = _addon(addon_school, amount="2000.00")
    sid, account = _account(addon_school)
    _assign(addon_school, band2.id, sid)
    taken = _assign(addon_school, band3.id, sid, start=2)
    assert [s.reason for s in taken.skipped] == ["head_taken"]
    with pytest.raises(ValidationFailed):
        _assign(addon_school, band3.id, sid, start=2, replace=True)
    moved = _assign(addon_school, band3.id, sid, start=2, replace=True, reason=REASON)
    assert (moved.assigned, moved.replaced, moved.credits_proposed) == (1, 1, 1)
    # The increase is posted now; the reduction waits for approval (D85).
    assert _line(addon_school, account, 2, h.van).due == D("3500.00")
    [removal] = _pending_removals(admin_engine, account)
    assert (removal.instalment_no, removal.amount, removal.status) == (2, D("-1500.00"), "pending")
    with pytest.raises(Forbidden):  # the maker never approves
        F.call(
            addon_school,
            "accountant",
            fees.approve_adjustment,
            removal.id,
            DecisionIn(),
            expected_version=None,
        )
    F.call(
        addon_school,
        "principal",
        fees.approve_adjustment,
        removal.id,
        DecisionIn(),
        expected_version=None,
    )
    assert _line(addon_school, account, 2, h.van).due == D("2000.00")
    assert _line(addon_school, account, 1, h.van).due == D("1500.00")
    listed = F.call(addon_school, "accountant", fees.addon_students, band2.id, include_ended=True)
    mine = next(i for i in listed.items if i.student_id == sid)
    assert (mine.status, mine.end_instalment) == ("ended", 1)


def test_FR_FEE_089_unassign_credits_only_what_is_unpaid(
    addon_school: Any, admin_engine: Engine
) -> None:
    h = _setup(addon_school)
    addon = _addon(addon_school)
    sid, account = _account(addon_school)
    _assign(addon_school, addon.id, sid)
    F.issue(
        addon_school,
        account,
        "1000.00",
        allocations=[AllocationIn(instalment_no=1, head_id=h.van, amount=D("1000.00"))],
    )
    data = AddonUnassignIn(student_ids=[sid], from_instalment=1, reason=REASON)
    with pytest.raises(Forbidden):
        F.call(addon_school, "office_admin", fees.unassign_addon, addon.id, data)
    dry = F.call(
        addon_school,
        "accountant",
        fees.unassign_addon,
        addon.id,
        data.model_copy(update={"dry_run": True}),
    )
    assert (dry.ended, dry.credit_total) == (1, D("2000.00"))
    assert _pending_removals(admin_engine, account) == []
    out = F.call(addon_school, "accountant", fees.unassign_addon, addon.id, data)
    assert (out.ended, out.credits_proposed, out.credit_total) == (1, 2, D("2000.00"))
    removals = _pending_removals(admin_engine, account)
    assert [(r.instalment_no, r.amount) for r in removals] == [
        (1, D("-500.00")),
        (2, D("-1500.00")),
    ]
    # Nothing changes before approval; asking again proposes nothing more.
    assert _line(addon_school, account, 1, h.van).due == D("500.00")
    again = F.call(addon_school, "accountant", fees.unassign_addon, addon.id, data)
    assert (again.ended, again.not_assigned) == (0, 1)
    events = W.audit_events(admin_engine, addon_school.tenant_id, "fee.addon.unassigned")
    mine = [e for e in events if e["resource_id"] == addon.id]
    assert mine[0]["summary"]["credit_total"] == "2000.00"
    assert REASON not in str(mine)
    for r in removals:
        F.call(
            addon_school,
            "principal",
            fees.approve_adjustment,
            r.id,
            DecisionIn(),
            expected_version=None,
        )
    assert _line(addon_school, account, 1, h.van).due == D("0.00")
    assert _line(addon_school, account, 2, h.van).due == D("0.00")
    # Shown in the approvals list with its own reason code.
    statement = F.call(addon_school, "accountant", fees.statement, account)
    assert {e.reason_code for e in statement.entries if e.entry_kind == "adjustment"} == {
        "addon_removal"
    }


# --- plan versions and late fees (FR-FEE-093, FR-FEE-094) ----------------------------------------


def test_FR_FEE_093_plan_versions_compare_only_plan_charges(addon_school: Any) -> None:
    h = _setup(addon_school)
    addon = _addon(addon_school)
    sid, account = _account(addon_school, "section_10a")
    _assign(addon_school, addon.id, sid)
    draft = F.call(addon_school, "accountant", fees.new_version, h.plan_x)
    first, second = F.due_dates()
    amounts = {h.tui: D("3000.00"), h.van: D("100.00")}
    draft = F.call(
        addon_school,
        "accountant",
        fees.update_plan,
        draft.id,
        FeePlanPatch(
            lines=[
                PlanLineIn(head_id=h.tui, annual_amount=D("6000.00")),
                PlanLineIn(head_id=h.van, annual_amount=D("200.00")),
            ],
            instalments=[
                InstalmentIn(instalment_no=1, label="Term 1", due_on=first, amounts=amounts),
                InstalmentIn(instalment_no=2, label="Term 2", due_on=second, amounts=amounts),
            ],
        ),
        expected_version=draft.version,
    )
    active = F.call(
        addon_school, "accountant", fees.activate_plan, draft.id, expected_version=draft.version
    )
    diffs = F.call(addon_school, "accountant", fees.plan_differences, active.id)
    mine = [d for d in diffs.items if d.account_id == account]
    # The add-on's 1,500 per term is not "charged by the plan": only the new 100 differs.
    assert sorted((d.instalment_no, d.head_id, d.charged, d.difference) for d in mine) == sorted(
        [(1, h.van, D("0.00"), D("100.00")), (2, h.van, D("0.00"), D("100.00"))]
    )
    # A plan charge and an add-on charge of the same line both stand (separate unique indexes).
    new_sid = F.student(addon_school, "section_10a")
    F.call(
        addon_school,
        "accountant",
        fees.apply_plan,
        active.id,
        PlanApplyIn(section_ids=[addon_school.ids["section_10a"]], include_past_instalments=True),
    )
    with tenant_session(addon_school.tenant_id) as s:
        raw: object = s.execute(
            text("SELECT id FROM fin.student_fee_accounts WHERE student_id = :s"), {"s": new_sid}
        ).scalar_one()
    new_account = uuid.UUID(str(raw))
    _assign(addon_school, addon.id, new_sid)
    assert _line(addon_school, new_account, 1, h.van).charged == D("1600.00")
    # Re-applying the plan adds nothing, even though the add-on charged the line too.
    again = F.call(
        addon_school,
        "accountant",
        fees.apply_plan,
        active.id,
        PlanApplyIn(section_ids=[addon_school.ids["section_10a"]], include_past_instalments=True),
    )
    assert again.charges_posted == 0 or new_account not in {x.student_id for x in again.skipped}
    assert _line(addon_school, new_account, 1, h.van).charged == D("1600.00")


def test_FR_FEE_094_late_fee_counts_unpaid_add_on_charges(addon_school: Any) -> None:
    h = _setup(addon_school)
    addon = _addon(addon_school)
    sid, account = _account(addon_school)
    _assign(addon_school, addon.id, sid)
    F.issue(
        addon_school,
        account,
        "3000.00",
        allocations=[AllocationIn(instalment_no=1, head_id=h.tui, amount=D("3000.00"))],
    )
    with tenant_session(addon_school.tenant_id) as s:
        fees.post_late_fees(s, today=F.today() + dt.timedelta(days=1))
    late = _line(addon_school, account, 1, None)
    assert late is not None
    assert late.late_fee == D("50.00")
    # Once the add-on of term 1 is paid nothing more is posted (the key is per instalment).
    F.issue(
        addon_school,
        account,
        "1500.00",
        allocations=[AllocationIn(instalment_no=1, head_id=h.van, amount=D("1500.00"))],
    )
    with tenant_session(addon_school.tenant_id) as s:
        fees.post_late_fees(s, today=F.today() + dt.timedelta(days=2))
    assert _line(addon_school, account, 1, None).late_fee == D("50.00")


# --- who is on it (FR-FEE-095) and access (FR-FEE-096..098) --------------------------------------


def test_FR_FEE_095_incharge_sees_students_without_amounts(
    addon_school: Any, other_school: Any, api: Any
) -> None:
    addon = _addon(addon_school)
    sid, _ = _account(addon_school)
    _assign(addon_school, addon.id, sid)
    path = f"/api/v1/fees/addons/{addon.id}/students"
    res = api.call(addon_school.people["incharge"], "GET", path)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["amounts_shown"] is False
    row = next(i for i in body["items"] if i["student_id"] == str(sid))
    assert row["display_name"]
    assert row["charged"] is None
    listed = api.call(addon_school.people["incharge"], "GET", "/api/v1/fees/addons")
    assert listed.status_code == 200
    assert all(i["amounts"] is None and i["total"] is None for i in listed.json()["items"])
    res = api.call(addon_school.people["accountant"], "GET", path)
    assert next(i for i in res.json()["items"] if i["student_id"] == str(sid))["charged"] == (
        "3000.00"
    )
    assert api.call(addon_school.people["office_admin"], "GET", path).status_code == 200
    for who in ("class_teacher", "office_staff"):
        assert api.call(addon_school.people[who], "GET", path).status_code == 403
    # The in-charge only reads.
    res = api.call(
        addon_school.people["incharge"],
        "POST",
        f"/api/v1/fees/addons/{addon.id}/assign",
        json={"student_ids": [str(sid)], "start_instalment": 1},
    )
    assert res.status_code == 403
    # Another school's member gets 404.
    F.ensure_setup(other_school)
    assert api.call(other_school.people["accountant"], "GET", path).status_code == 404


def test_FR_FEE_096_http_create_patch_assign_unassign(addon_school: Any, api: Any) -> None:
    h = _setup(addon_school)
    who = addon_school.people["accountant"]
    res = api.call(
        who,
        "POST",
        "/api/v1/fees/addons",
        json={
            "academic_year_id": str(addon_school.ids["year"]),
            "head_id": str(h.van),
            "label": "Band HTTP",
            "amounts": {"1": "700.00", "2": "700.00"},
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert res.status_code == 201, res.text
    addon = res.json()
    res = api.call(
        who,
        "PATCH",
        f"/api/v1/fees/addons/{addon['id']}",
        json={"label": "Band HTTP renamed"},
        headers={"If-Match": res.headers["ETag"]},
    )
    assert res.status_code == 200, res.text
    sid, _ = _account(addon_school)
    res = api.call(
        who,
        "POST",
        f"/api/v1/fees/addons/{addon['id']}/assign",
        json={"student_ids": [str(sid)], "start_instalment": 1},
    )
    assert res.status_code == 200, res.text
    assert res.json()["charges_posted"] == 2
    res = api.call(
        who,
        "POST",
        f"/api/v1/fees/addons/{addon['id']}/unassign",
        json={"student_ids": [str(sid)], "from_instalment": 2, "reason": REASON},
    )
    assert res.status_code == 200, res.text
    assert res.json()["credit_total"] == "700.00"
    bad = api.call(
        who,
        "POST",
        f"/api/v1/fees/addons/{addon['id']}/assign",
        json={
            "class_id": str(addon_school.ids["class_ix"]),
            "student_ids": [str(sid)],
            "start_instalment": 1,
        },
    )
    assert bad.status_code == 422


def test_FR_FEE_097_database_keeps_add_on_sources_straight(
    addon_school: Any, admin_engine: Engine
) -> None:
    addon = _addon(addon_school)
    sid, account = _account(addon_school)
    _assign(addon_school, addon.id, sid)
    with admin_engine.connect() as c:
        assignment: object = c.execute(
            text("SELECT id FROM fin.fee_addon_assignments WHERE account_id = :a"), {"a": account}
        ).scalar_one()
    # Only a charge may name an assignment.
    with pytest.raises(DBAPIError, match="ledger_entries_addon_kind"), admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO fin.ledger_entries (id, tenant_id, account_id, entry_kind, amount, "
                "instalment_no, head_id, entry_date, late_fee_key, addon_assignment_id) "
                "VALUES (:i, :t, :a, 'late_fee', 5, 1, NULL, current_date, 'x', :s)"
            ),
            {"i": uuid.uuid4(), "t": addon_school.tenant_id, "a": account, "s": assignment},
        )
    # One charge per (assignment, instalment).
    with (
        pytest.raises(DBAPIError, match="ledger_entries_one_addon_charge"),
        admin_engine.begin() as c,
    ):
        c.execute(
            text(
                "INSERT INTO fin.ledger_entries (id, tenant_id, account_id, entry_kind, amount, "
                "instalment_no, head_id, entry_date, addon_assignment_id) "
                "SELECT :i, tenant_id, account_id, 'charge', 1, 1, head_id, current_date, id "
                "FROM fin.fee_addon_assignments WHERE id = :s"
            ),
            {"i": uuid.uuid4(), "s": assignment},
        )
    # An ended assignment never comes back.
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE fin.fee_addon_assignments SET status = 'ended', end_instalment = 2, "
                "ended_by = assigned_by, ended_at = now() WHERE id = :s"
            ),
            {"s": assignment},
        )
    with pytest.raises(DBAPIError, match="fee_addon_assignments_frozen"), admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE fin.fee_addon_assignments SET status = 'active', end_instalment = NULL, "
                "ended_by = NULL, ended_at = NULL WHERE id = :s"
            ),
            {"s": assignment},
        )
    # A manual adjustment request may not use the add-on reason.
    with (
        pytest.raises(DBAPIError, match="adjustments_addon_removal_shape"),
        admin_engine.begin() as c,
    ):
        c.execute(
            text(
                "INSERT INTO fin.adjustments (id, tenant_id, account_id, instalment_no, head_id, "
                "amount, reason_code, reason, requested_by) VALUES (:i, :t, :a, 1, :h, -5, "
                "'addon_removal', 'Synthetic removal reason', :b)"
            ),
            {
                "i": uuid.uuid4(),
                "t": addon_school.tenant_id,
                "a": account,
                "h": _setup(addon_school).van,
                "b": addon_school.people["accountant"].membership_id,
            },
        )


def test_FR_FEE_098_fees_off_hides_add_ons(
    addon_school: Any, admin_engine: Engine, api: Any
) -> None:
    _setup(addon_school)
    F.enable(admin_engine, addon_school.tenant_id, on=False)
    try:
        res = api.call(addon_school.people["accountant"], "GET", "/api/v1/fees/addons")
        assert res.status_code == 404
        assert res.json()["code"] == "module_hidden"
    finally:
        F.enable(admin_engine, addon_school.tenant_id)
