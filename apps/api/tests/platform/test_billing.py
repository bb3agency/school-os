"""Plans, subscriptions, GST invoices and payments (FR-PLT-010..019).

Invoice numbering is exercised in far-future financial years so each test owns its sequence.
"""

from __future__ import annotations

import datetime as dt
import threading
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session
from app.core.errors import Conflict
from app.platform import billing
from app.platform.common import config
from app.platform.schemas import InvoiceLineIn, PaymentIn

from .conftest import (
    Api,
    MakeOperator,
    Operator,
    billing_account_payload,
    letters,
    provision_payload,
)

pytestmark = pytest.mark.db

D = Decimal


# --- pure functions ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "rate", "supplier", "place", "expected"),
    [
        (
            "5000.00",
            "18",
            "37",
            "37",
            ("cgst_sgst", "5000.00", "450.00", "450.00", "0.00", "5900.00"),
        ),
        ("5000.00", "18", "37", "29", ("igst", "5000.00", "0.00", "0.00", "900.00", "5900.00")),
        # half-up to paise: 12.50 * 9% = 1.125 -> 1.13 (banker's rounding would give 1.12)
        ("12.50", "18", "37", "37", ("cgst_sgst", "12.50", "1.13", "1.13", "0.00", "14.76")),
        ("99.99", "18", "37", "36", ("igst", "99.99", "0.00", "0.00", "18.00", "117.99")),
        ("1000.00", "0", "37", "37", ("cgst_sgst", "1000.00", "0.00", "0.00", "0.00", "1000.00")),
        ("333.33", "12", "37", "37", ("cgst_sgst", "333.33", "20.00", "20.00", "0.00", "373.33")),
    ],
)
def test_FR_PLT_017_gst_split_and_rounding(
    amount: str, rate: str, supplier: str, place: str, expected: tuple[str, ...]
) -> None:
    tax = billing.compute_tax(
        [(D(amount), D(rate))], supplier_state=supplier, place_of_supply=place
    )
    got = (
        tax.tax_type,
        str(tax.taxable_value_inr),
        str(tax.cgst_inr),
        str(tax.sgst_inr),
        str(tax.igst_inr),
        str(tax.total_inr),
    )
    assert got == expected
    assert tax.total_inr == tax.taxable_value_inr + tax.cgst_inr + tax.sgst_inr + tax.igst_inr


@pytest.mark.parametrize(
    ("day", "fy", "short"),
    [
        (dt.date(2027, 3, 31), "2026-27", "26-27"),
        (dt.date(2027, 4, 1), "2027-28", "27-28"),
        (dt.date(2099, 12, 31), "2099-00", "99-00"),
    ],
)
def test_FR_PLT_016_financial_year(day: dt.date, fy: str, short: str) -> None:
    assert billing.financial_year(day) == (fy, short)


def test_FR_PLT_016_invoice_number_is_16_characters() -> None:
    number = billing.format_invoice_number("SOS", "26-27", 123)
    assert number == "SOS/26-27/000123"
    assert len(number) == 16
    with pytest.raises(Conflict):
        billing.format_invoice_number("SOS", "26-27", 1_000_000)


# --- helpers ----------------------------------------------------------------------------------


def _school(
    api: Api, op: Operator, plan_id: uuid.UUID, *, start_as: str = "active", state: str = "37"
) -> dict[str, Any]:
    body = provision_payload(plan_id, start_as=start_as)
    body["billing_account"] = billing_account_payload(state_code=state)
    res = api.call("POST", "/tenants", op, json=body)
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def _drafts(sub_id: str) -> list[dict[str, Any]]:
    with platform_session() as s:
        return [
            dict(r)
            for r in s.execute(
                text(
                    "SELECT * FROM platform.invoices WHERE subscription_id = :s "
                    "AND status = 'draft' "
                    "ORDER BY period_start"
                ),
                {"s": sub_id},
            ).mappings()
        ]


@pytest.fixture
def billing_admin(make_operator: MakeOperator) -> Operator:
    return make_operator("billing_admin")


# --- plans ------------------------------------------------------------------------------------


def test_FR_PLT_010_plans_are_versioned_and_frozen(api: Api, billing_admin: Operator) -> None:
    code = f"std-{uuid.uuid4().hex[:8]}"
    body = {
        "code": code,
        "name": "Synthetic Standard",
        "base_price_inr": "4000.00",
        "limits": {"students": 800, "ai_budget_inr": "1500.00"},
    }
    v1 = api.call("POST", "/plans", billing_admin, json=body).json()
    assert (v1["version"], v1["status"], v1["sac_code"]) == (1, "draft", "998314")
    patched = api.call(
        "PATCH", f"/plans/{v1['id']}", billing_admin, json={"base_price_inr": "4200.00"}
    )
    assert patched.json()["base_price_inr"] == "4200.00"
    assert (
        api.call("POST", f"/plans/{v1['id']}/publish", billing_admin).json()["status"]
        == "published"
    )
    frozen = api.call("PATCH", f"/plans/{v1['id']}", billing_admin, json={"base_price_inr": "1.00"})
    assert (frozen.status_code, frozen.json()["code"]) == (409, "plan_published")
    v2 = api.call("POST", "/plans", billing_admin, json=body).json()
    assert v2["version"] == 2
    assert (
        api.call("POST", f"/plans/{v1['id']}/retire", billing_admin).json()["status"] == "retired"
    )
    per_student_missing_price = dict(body, pricing_model="per_student")
    assert (
        api.call("POST", "/plans", billing_admin, json=per_student_missing_price).status_code == 422
    )


def test_FR_PLT_010_draft_edit_checks_the_row_version(api: Api, billing_admin: Operator) -> None:
    """Owner decision 2026-10-04: PATCH a draft plan takes If-Match (optional, like every
    platform edit); a stale ETag gets 412 and changes nothing. ``row_version`` is the edit
    counter, separate from the catalogue ``version`` (code + version)."""
    body = {"code": f"etag-{uuid.uuid4().hex[:8]}", "name": "Synthetic", "base_price_inr": "10.00"}
    created = api.call("POST", "/plans", billing_admin, json=body)
    plan = created.json()
    assert (plan["version"], plan["row_version"]) == (1, 1)
    path = f"/plans/{plan['id']}"
    read = api.call("GET", path, billing_admin)
    assert read.headers["ETag"] == '"1"'

    first = api.call(
        "PATCH", path, billing_admin, json={"name": "First"}, headers={"If-Match": '"1"'}
    )
    assert first.status_code == 200, first.text
    assert (first.json()["row_version"], first.json()["version"]) == (2, 1)
    assert first.headers["ETag"] == '"2"'

    stale = api.call(
        "PATCH", path, billing_admin, json={"name": "Stale"}, headers={"If-Match": '"1"'}
    )
    assert (stale.status_code, stale.json()["code"]) == (412, "precondition_failed")
    assert api.call("GET", path, billing_admin).json()["name"] == "First"

    weak = api.call(
        "PATCH", path, billing_admin, json={"name": "Second"}, headers={"If-Match": 'W/"2"'}
    )
    assert weak.json()["row_version"] == 3
    # Without If-Match the edit still applies (the house pattern) and the version still moves.
    blind = api.call("PATCH", path, billing_admin, json={"name": "Third"})
    assert (blind.status_code, blind.json()["row_version"]) == (200, 4)
    bad = api.call("PATCH", path, billing_admin, json={"name": "X"}, headers={"If-Match": "abc"})
    assert (bad.status_code, bad.json()["code"]) == (400, "bad_if_match")
    # Publishing freezes the plan; a stale or current ETag cannot reopen it.
    assert api.call("POST", f"{path}/publish", billing_admin).status_code == 200
    frozen = api.call(
        "PATCH", path, billing_admin, json={"name": "Late"}, headers={"If-Match": '"4"'}
    )
    assert (frozen.status_code, frozen.json()["code"]) == (409, "plan_published")


# --- invoices ---------------------------------------------------------------------------------


def test_FR_PLT_017_first_invoice_cgst_sgst_in_ap_and_igst_elsewhere(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    plan = make_plan(base_price_inr="5000.00")
    ap = _school(api, owner, plan, state="37")
    ka = _school(api, owner, plan, state="29")
    ap_draft = _drafts(ap["subscription_id"])[0]
    ka_draft = _drafts(ka["subscription_id"])[0]
    assert (ap_draft["tax_type"], ap_draft["cgst_inr"], ap_draft["sgst_inr"]) == (
        "cgst_sgst",
        D("450.00"),
        D("450.00"),
    )
    assert (ka_draft["tax_type"], ka_draft["igst_inr"], ka_draft["total_inr"]) == (
        "igst",
        D("900.00"),
        D("5900.00"),
    )


def test_FR_PLT_016_numbers_are_sequential_and_gap_free_under_concurrency(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    sub = _school(api, owner, make_plan())["subscription_id"]
    ids = [
        billing.create_manual_draft(
            owner.actor, uuid.UUID(sub), dt.date(2070, 1, 1) + dt.timedelta(days=40 * i)
        ).id
        for i in range(20)
    ]
    issue_day = dt.date(2081, 5, 10)
    numbers: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(len(ids))

    def issue(invoice_id: uuid.UUID) -> None:
        try:
            barrier.wait()
            out = billing.issue_invoice(owner.actor, invoice_id, today=issue_day)
            with lock:
                numbers.append(out.invoice_number or "")
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=issue, args=(i,)) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(numbers) == [f"SOS/81-82/{n:06d}" for n in range(1, 21)]
    with platform_session() as s:
        last: Any = s.execute(
            text(
                "SELECT last_number FROM platform.invoice_sequences "
                "WHERE financial_year = '2081-82'"
            )
        ).scalar_one()
    assert last == 20


def test_FR_PLT_016_numbering_restarts_on_1_april(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    sub = uuid.UUID(_school(api, owner, make_plan())["subscription_id"])
    a = billing.create_manual_draft(owner.actor, sub, dt.date(2071, 1, 1)).id
    b = billing.create_manual_draft(owner.actor, sub, dt.date(2071, 2, 1)).id
    march = billing.issue_invoice(owner.actor, a, today=dt.date(2083, 3, 31))
    april = billing.issue_invoice(owner.actor, b, today=dt.date(2083, 4, 1))
    assert (march.invoice_number, march.financial_year) == ("SOS/82-83/000001", "2082-83")
    assert (april.invoice_number, april.financial_year) == ("SOS/83-84/000001", "2083-84")
    assert april.due_date == dt.date(2083, 4, 16)


def test_FR_PLT_015_draft_edit_issue_and_immutability(
    api: Api, billing_admin: Operator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    sub = _school(api, owner, make_plan())["subscription_id"]
    draft = _drafts(sub)[0]
    lines = {
        "lines": [
            {"kind": "subscription", "description": "Synthetic plan", "unit_price_inr": "1000.00"},
            {"kind": "discount", "description": "Pilot discount", "unit_price_inr": "100.00"},
        ]
    }
    res = api.call("PATCH", f"/invoices/{draft['id']}", billing_admin, json=lines)
    assert res.status_code == 200, res.text
    assert (res.json()["taxable_value_inr"], res.json()["total_inr"]) == ("900.00", "1062.00")
    issued = api.call("POST", f"/invoices/{draft['id']}/issue", billing_admin)
    assert issued.status_code == 200, issued.text
    assert issued.json()["status"] == "issued"
    again = api.call("PATCH", f"/invoices/{draft['id']}", billing_admin, json=lines)
    assert (again.status_code, again.json()["code"]) == (409, "invoice_issued")
    assert api.call("DELETE", f"/invoices/{draft['id']}", billing_admin).status_code == 409


def test_FR_PLT_018_partial_payments_tds_and_reversal(
    api: Api, billing_admin: Operator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    sub = _school(api, owner, make_plan(base_price_inr="5000.00"))["subscription_id"]
    inv_id = _drafts(sub)[0]["id"]
    api.call("POST", f"/invoices/{inv_id}/issue", billing_admin)
    pay = {
        "method": "upi",
        "amount_inr": "2000.00",
        "received_on": "2026-09-20",
        "reference": f"UPI-{letters(10)}",
    }
    first = api.call("POST", f"/invoices/{inv_id}/payments", billing_admin, json=pay)
    assert first.status_code == 201, first.text
    inv = api.call("GET", f"/invoices/{inv_id}", billing_admin).json()
    assert (inv["status"], inv["amount_paid_inr"], inv["balance_due_inr"]) == (
        "issued",
        "2000.00",
        "3900.00",
    )
    over = dict(pay, amount_inr="4000.00", reference="UTR-OVER-1")
    assert (
        api.call("POST", f"/invoices/{inv_id}/payments", billing_admin, json=over).status_code
        == 422
    )
    rest = dict(
        pay, method="bank_transfer", amount_inr="3500.00", tds_inr="400.00", reference="UTR-REST-1"
    )
    assert (
        api.call("POST", f"/invoices/{inv_id}/payments", billing_admin, json=rest).status_code
        == 201
    )
    inv = api.call("GET", f"/invoices/{inv_id}", billing_admin).json()
    assert (inv["status"], inv["tds_inr"], inv["balance_due_inr"]) == ("paid", "400.00", "0.00")
    rev = api.call(
        "POST",
        f"/payments/{first.json()['id']}/reverse",
        billing_admin,
        json={"reason": "Recorded against the wrong school"},
    )
    assert rev.json()["status"] == "reversed"
    inv = api.call("GET", f"/invoices/{inv_id}", billing_admin).json()
    assert (inv["status"], inv["balance_due_inr"]) == ("issued", "2000.00")
    void = api.call(
        "POST", f"/invoices/{inv_id}/void", billing_admin, json={"reason": "Wrong period"}
    )
    assert void.status_code == 409  # partly paid invoices are not voided


def test_FR_PLT_018_invoice_payments_list_newest_first_with_reversal_fields(
    api: Api,
    billing_admin: Operator,
    owner: Operator,
    make_operator: MakeOperator,
    make_plan: Callable[..., uuid.UUID],
) -> None:
    sub = _school(api, owner, make_plan(base_price_inr="5000.00"))["subscription_id"]
    inv_id = _drafts(sub)[0]["id"]
    api.call("POST", f"/invoices/{inv_id}/issue", billing_admin)
    empty = api.call("GET", f"/invoices/{inv_id}/payments", billing_admin)
    assert (empty.status_code, empty.json()) == (200, [])

    early = {
        "method": "cheque",
        "amount_inr": "1000.00",
        "received_on": "2026-09-10",
        "reference": f"CHQ-{letters(8)}",
    }
    late = {
        "method": "upi",
        "amount_inr": "2000.00",
        "tds_inr": "100.00",
        "received_on": "2026-09-20",
        "reference": f"UPI-{letters(10)}",
        "notes": "Second instalment",
    }
    first = api.call("POST", f"/invoices/{inv_id}/payments", billing_admin, json=early)
    second = api.call("POST", f"/invoices/{inv_id}/payments", billing_admin, json=late)
    assert (first.status_code, second.status_code) == (201, 201), first.text + second.text
    rev = api.call(
        "POST",
        f"/payments/{first.json()['id']}/reverse",
        billing_admin,
        json={"reason": "Cheque bounced at the bank"},
    )
    assert rev.status_code == 200, rev.text
    reversed_out = rev.json()
    assert reversed_out["status"] == "reversed"
    assert reversed_out["reversed_by"] == str(billing_admin.id)
    assert reversed_out["reversed_by_name"] == "Synthetic Operator"
    assert reversed_out["reversal_reason"] == "Cheque bounced at the bank"
    assert reversed_out["reversed_at"] is not None
    again = api.call(
        "POST",
        f"/payments/{first.json()['id']}/reverse",
        billing_admin,
        json={"reason": "Cheque bounced at the bank"},
    )
    assert (again.status_code, again.json()["code"]) == (409, "invalid_state")

    viewer = make_operator("platform_viewer")
    res = api.call("GET", f"/invoices/{inv_id}/payments", viewer)
    assert res.status_code == 200, res.text
    rows = res.json()
    assert [r["id"] for r in rows] == [second.json()["id"], first.json()["id"]]
    newest, oldest = rows
    assert newest["status"] == "recorded"
    assert (newest["amount_inr"], newest["tds_inr"], newest["method"]) == (
        "2000.00",
        "100.00",
        "upi",
    )
    assert (newest["received_on"], newest["reference"]) == ("2026-09-20", late["reference"])
    assert newest["notes"] == "Second instalment"
    assert newest["recorded_by"] == str(billing_admin.id)
    assert newest["recorded_by_name"] == "Synthetic Operator"
    assert newest["recorded_at"] is not None
    assert (newest["reversed_at"], newest["reversed_by"], newest["reversal_reason"]) == (
        None,
        None,
        None,
    )
    assert newest["reversed_by_name"] is None
    assert oldest["status"] == "reversed"
    assert oldest["reversed_by"] == str(billing_admin.id)
    assert oldest["reversed_by_name"] == "Synthetic Operator"
    assert oldest["reversal_reason"] == "Cheque bounced at the bank"
    assert oldest["reversed_at"] is not None

    inv = api.call("GET", f"/invoices/{inv_id}", viewer).json()
    assert (inv["status"], inv["amount_paid_inr"], inv["balance_due_inr"]) == (
        "issued",
        "2000.00",
        "3800.00",
    )


def test_FR_PLT_018_invoice_payments_unknown_invoice_and_forbidden_role(
    api: Api, billing_admin: Operator, make_operator: MakeOperator
) -> None:
    missing = api.call("GET", f"/invoices/{uuid.uuid4()}/payments", billing_admin)
    assert (missing.status_code, missing.json()["code"]) == (404, "not_found")
    support = make_operator("support_agent")  # no platform.invoices.read
    denied = api.call("GET", f"/invoices/{uuid.uuid4()}/payments", support)
    assert denied.status_code == 403


def test_FR_PLT_019_void_keeps_number(
    api: Api, billing_admin: Operator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    sub = _school(api, owner, make_plan())["subscription_id"]
    inv_id = _drafts(sub)[0]["id"]
    number = api.call("POST", f"/invoices/{inv_id}/issue", billing_admin).json()["invoice_number"]
    void = api.call(
        "POST", f"/invoices/{inv_id}/void", billing_admin, json={"reason": "Duplicate invoice"}
    )
    assert (void.json()["status"], void.json()["invoice_number"]) == ("void", number)
    assert billing.create_manual_draft(
        owner.actor, uuid.UUID(sub), dt.date.fromisoformat(void.json()["period_start"])
    )


def test_FR_PLT_015_monthly_generation_is_idempotent(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    sub = _school(api, owner, make_plan())["subscription_id"]
    with platform_session() as s:
        end: Any = s.execute(
            text("SELECT current_period_end FROM platform.subscriptions WHERE id = :s"), {"s": sub}
        ).scalar_one()
    month = end.strftime("%Y-%m")
    job = billing.generate_invoices(month)
    assert job.status == "succeeded"
    assert [d["period_start"] for d in _drafts(sub)] == [
        end - (end - _drafts(sub)[0]["period_start"]),
        end,
    ]
    again = billing.generate_invoices(month)
    assert again.id == job.id
    assert len(_drafts(sub)) == 2


# --- lifecycle --------------------------------------------------------------------------------


def _issue_overdue(owner: Operator, sub: str, issue_day: dt.date) -> str:
    inv_id = _drafts(sub)[0]["id"]
    billing.issue_invoice(owner.actor, inv_id, today=issue_day)
    return str(inv_id)


def test_FR_PLT_014_overdue_sweep_marks_past_due_and_never_suspends(
    api: Api,
    owner: Operator,
    billing_admin: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
) -> None:
    school = _school(api, owner, make_plan())
    sub, tid = school["subscription_id"], school["tenant_id"]
    api.call("POST", f"/tenants/{tid}/activate", owner)
    inv = _issue_overdue(owner, sub, dt.date(2026, 1, 5))  # due 2026-01-20
    assert billing.mark_past_due(today=dt.date(2026, 2, 1)) >= 1
    for later in (dt.date(2026, 3, 1), dt.date(2026, 6, 1)):
        billing.mark_past_due(today=later)
        state = billing.get_subscription(uuid.UUID(sub))
        assert (state.status, state.past_due_since, state.grace_ends_on) == (
            "past_due",
            dt.date(2026, 2, 1),
            dt.date(2026, 2, 16),
        )
    with pytest.raises(Conflict, match="grace"):
        billing.suspend_subscription(
            billing_admin.actor,
            uuid.UUID(sub),
            "Unpaid for two months",
            actor_is_owner=False,
            exam_window_override=False,
            today=dt.date(2026, 2, 10),
        )
    out = billing.suspend_subscription(
        billing_admin.actor,
        uuid.UUID(sub),
        "Unpaid for two months",
        actor_is_owner=False,
        exam_window_override=False,
        today=dt.date(2026, 2, 16),
    )
    assert out.status == "suspended"
    with admin_engine.connect() as c:
        assert (
            c.execute(text("SELECT status FROM core.tenants WHERE id = :t"), {"t": tid}).scalar()
            == "suspended"
        )
    with pytest.raises(Conflict, match="Overdue"):
        billing.reactivate_subscription(
            billing_admin.actor, uuid.UUID(sub), today=dt.date(2026, 2, 20)
        )
    billing.record_payment(
        billing_admin.actor,
        uuid.UUID(inv),
        PaymentIn(
            method="cheque",
            amount_inr=D("5900.00"),
            received_on=dt.date(2026, 2, 19),
            reference="CHQ-000777",
        ),
        today=dt.date(2026, 2, 20),
    )
    assert billing.get_subscription(uuid.UUID(sub)).status == "suspended"  # reactivation is manual
    back = billing.reactivate_subscription(
        billing_admin.actor, uuid.UUID(sub), today=dt.date(2026, 2, 20)
    )
    assert back.status == "active"
    with admin_engine.connect() as c:
        assert (
            c.execute(text("SELECT status FROM core.tenants WHERE id = :t"), {"t": tid}).scalar()
            == "active"
        )


def test_FR_PLT_014_payment_clears_past_due_automatically(
    api: Api, owner: Operator, billing_admin: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    sub = _school(api, owner, make_plan())["subscription_id"]
    inv = _issue_overdue(owner, sub, dt.date(2026, 1, 6))
    billing.mark_past_due(today=dt.date(2026, 2, 2))
    assert billing.get_subscription(uuid.UUID(sub)).status == "past_due"
    billing.record_payment(
        billing_admin.actor,
        uuid.UUID(inv),
        PaymentIn(
            method="bank_transfer",
            amount_inr=D("5900.00"),
            received_on=dt.date(2026, 2, 3),
            reference="UTR-AUTO-1",
        ),
        today=dt.date(2026, 2, 3),
    )
    state = billing.get_subscription(uuid.UUID(sub))
    assert (state.status, state.past_due_since) == ("active", None)


def test_FR_PLT_014_exam_window_needs_platform_owner(
    api: Api,
    owner: Operator,
    billing_admin: Operator,
    make_plan: Callable[..., uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    school = _school(api, owner, make_plan())
    sub = uuid.UUID(school["subscription_id"])
    _issue_overdue(owner, school["subscription_id"], dt.date(2026, 1, 7))
    billing.mark_past_due(today=dt.date(2026, 2, 3))
    monkeypatch.setitem(
        config()["billing"],
        "protected_windows",
        [
            {
                "name": "SSC public exams",
                "boards": ["SSC"],
                "start": "2026-03-01",
                "end": "2026-04-10",
            }
        ],
    )
    day = dt.date(2026, 3, 15)
    with pytest.raises(Conflict, match="exam window"):
        billing.suspend_subscription(
            billing_admin.actor,
            sub,
            "Unpaid",
            actor_is_owner=False,
            exam_window_override=True,
            today=day,
        )
    with pytest.raises(Conflict, match="exam window"):
        billing.suspend_subscription(
            owner.actor, sub, "Unpaid", actor_is_owner=True, exam_window_override=False, today=day
        )
    out = billing.suspend_subscription(
        owner.actor, sub, "Unpaid", actor_is_owner=True, exam_window_override=True, today=day
    )
    assert out.status == "suspended"


def test_FR_PLT_012_plan_change_applies_next_period_and_cancel_at_period_end(
    api: Api, owner: Operator, billing_admin: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    sub = uuid.UUID(_school(api, owner, make_plan())["subscription_id"])
    bigger = make_plan(base_price_inr="9000.00")
    res = api.call(
        "POST", f"/subscriptions/{sub}/change-plan", billing_admin, json={"plan_id": str(bigger)}
    )
    assert res.json()["pending_plan_id"] == str(bigger)
    end = billing.get_subscription(sub).current_period_end
    billing.roll_periods(today=end)
    rolled = billing.get_subscription(sub)
    assert (rolled.plan_id, rolled.pending_plan_id, rolled.current_period_start) == (
        bigger,
        None,
        end,
    )
    cancel = api.call(
        "POST",
        f"/subscriptions/{sub}/cancel",
        billing_admin,
        json={"reason": "School is closing down"},
    )
    assert (cancel.json()["status"], cancel.json()["cancel_at_period_end"]) == ("active", True)
    billing.roll_periods(today=rolled.current_period_end)
    assert billing.get_subscription(sub).status == "cancelled"


def test_FR_PLT_011_trial_activate_and_extend(
    api: Api, owner: Operator, billing_admin: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    sub = _school(api, owner, make_plan(), start_as="trial")["subscription_id"]
    assert _drafts(sub) == []  # trials are not invoiced
    later = (dt.datetime.now(dt.UTC) + dt.timedelta(days=60)).isoformat()
    ext = api.call(
        "POST", f"/subscriptions/{sub}/extend-trial", billing_admin, json={"trial_ends_at": later}
    )
    assert ext.status_code == 200, ext.text
    act = api.call("POST", f"/subscriptions/{sub}/activate", billing_admin)
    assert act.json()["status"] == "active"
    assert len(_drafts(sub)) == 1
    assert api.call("POST", f"/subscriptions/{sub}/activate", billing_admin).status_code == 409


def test_FR_PLT_013_negotiated_price_returns_its_reason(
    api: Api,
    owner: Operator,
    billing_admin: Operator,
    make_operator: MakeOperator,
    make_plan: Callable[..., uuid.UUID],
) -> None:
    """docs/16 §5.3: the subscription shows the negotiated price and its reason (operators)."""
    sub = _school(api, owner, make_plan())["subscription_id"]
    assert api.call("GET", f"/subscriptions/{sub}", billing_admin).json()["override_reason"] is None
    reason = "Pilot school, price agreed in writing (ref SS/2026/3)"
    res = api.call(
        "PUT",
        f"/subscriptions/{sub}/price-override",
        billing_admin,
        json={"price_override_inr": "3999.00", "reason": reason},
    )
    assert res.status_code == 200, res.text
    assert (res.json()["price_override_inr"], res.json()["override_reason"]) == ("3999.00", reason)
    viewer = make_operator("platform_viewer")
    read = api.call("GET", f"/subscriptions/{sub}", viewer).json()
    assert (read["price_override_inr"], read["override_reason"]) == ("3999.00", reason)
    listed = api.call("GET", "/subscriptions", viewer).json()["data"]
    assert next(row for row in listed if row["id"] == sub)["override_reason"] == reason
    cleared = api.call("DELETE", f"/subscriptions/{sub}/price-override", billing_admin).json()
    assert (cleared["price_override_inr"], cleared["override_reason"]) == (None, None)


def test_FR_PLT_013_billing_account_gstin_validation_and_etag(
    api: Api, owner: Operator, billing_admin: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    tid = _school(api, owner, make_plan())["tenant_id"]
    got = api.call("GET", f"/tenants/{tid}/billing-account", billing_admin)
    etag = got.headers["ETag"]
    bad = billing_account_payload(state_code="37", gstin="29ABCDE1234F1Z5")
    assert (
        api.call("PUT", f"/tenants/{tid}/billing-account", billing_admin, json=bad).status_code
        == 422
    )
    good = billing_account_payload(state_code="37", gstin="37ABCDE1234F1Z5")
    res = api.call(
        "PUT",
        f"/tenants/{tid}/billing-account",
        billing_admin,
        json=good,
        headers={"If-Match": etag},
    )
    assert res.status_code == 200, res.text
    assert res.json()["gstin"] == "37ABCDE1234F1Z5"
    stale = api.call(
        "PUT",
        f"/tenants/{tid}/billing-account",
        billing_admin,
        json=good,
        headers={"If-Match": etag},
    )
    assert stale.status_code == 412


def test_FR_PLT_015_invoice_lines_use_plan_sac_and_rate() -> None:
    rows = billing._line_rows(
        [
            InvoiceLineIn(
                kind="addon",
                description="Extra storage",
                quantity=D("2.5"),
                unit_price_inr=D("99.99"),
            )
        ],
        {"sac_code": "998314", "gst_rate": D("18.00")},
    )
    assert (rows[0]["amount_inr"], rows[0]["sac_code"]) == (D("249.98"), "998314")
