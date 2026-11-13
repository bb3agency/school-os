"""Online payment of SchoolOS's own invoices: Razorpay payment links and the signed webhook
(FA-13; FR-PAY-017..030; ADR-0016).

Offline: the fake provider (``app.platform.link_providers.fake``) stands in for Razorpay; the
webhook is signed with a synthetic secret exactly as Razorpay signs (hex HMAC-SHA256 of the raw
body). No network call is made.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.core import config as core_config
from app.core.db import platform_session
from app.platform import billing, payment_links, payment_webhook
from app.platform.link_providers.fake import FakeInvoiceLinks, webhook_body
from app.platform.link_providers.razorpay import signature

from .conftest import Api, MakeOperator, Operator, letters, provision_payload

pytestmark = pytest.mark.db

WEBHOOK_SECRET = "synthetic-schoolos-webhook-secret-0123"
WEBHOOK = "/api/v1/webhooks/schoolos-billing"


@pytest.fixture
def payments_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Online payment of SchoolOS invoices on, fake provider, synthetic webhook secret."""

    def settings() -> core_config.Settings:
        return core_config.get_settings().model_copy(
            update={
                "platform_payments_enabled": True,
                "platform_razorpay_webhook_secret": SecretStr(WEBHOOK_SECRET),
            }
        )

    monkeypatch.setattr(payment_links, "get_settings", settings)
    monkeypatch.setattr(payment_webhook, "get_settings", settings)
    FakeInvoiceLinks.reset()
    yield
    FakeInvoiceLinks.reset()


@pytest.fixture
def billing_admin(make_operator: MakeOperator) -> Operator:
    return make_operator("billing_admin")


def _issued_invoice(
    api: Api, owner: Operator, plan: uuid.UUID, admin: Engine | None = None
) -> tuple[str, str, str]:
    """(tenant id, invoice id, owner subject) of a school with one issued ₹5,900 invoice."""
    body = provision_payload(plan, start_as="active")
    out = api.call("POST", "/tenants", owner, json=body).json()
    tid = out["tenant_id"]
    if admin is not None:
        api.call("POST", f"/tenants/{tid}/activate", owner)
        with admin.begin() as c:
            c.execute(
                text("UPDATE core.memberships SET status = 'active' WHERE tenant_id = :t"),
                {"t": tid},
            )
    with platform_session() as s:
        inv: Any = s.execute(
            text("SELECT id FROM platform.invoices WHERE tenant_id = :t"), {"t": tid}
        ).scalar_one()
    billing.issue_invoice(owner.actor, inv)
    return tid, str(inv), body["owner"]["idp_subject"]


def _post_webhook(
    api: Api, body: bytes, *, event_id: str | None = None, sig: str | None = None
) -> httpx.Response:
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": sig or signature(WEBHOOK_SECRET.encode(), body),
        "X-Razorpay-Event-Id": event_id or f"evt_{letters(14)}",
    }
    res: httpx.Response = api.client.post(WEBHOOK, content=body, headers=headers)
    return res


def _actions(resource_id: str) -> list[str]:
    with platform_session() as s:
        return list(
            s.execute(
                text(
                    "SELECT action FROM platform.audit_events WHERE resource_id = :r ORDER BY seq"
                ),
                {"r": resource_id},
            ).scalars()
        )


def _payments(invoice_id: str) -> list[dict[str, Any]]:
    with platform_session() as s:
        return [
            dict(r)
            for r in s.execute(
                text("SELECT * FROM platform.payments WHERE invoice_id = :i"), {"i": invoice_id}
            ).mappings()
        ]


# --- link creation and authorization ----------------------------------------------------------


@pytest.mark.usefixtures("payments_on")
def test_FR_PAY_017_billing_admin_creates_one_link_for_the_balance(
    api: Api, owner: Operator, billing_admin: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    _, inv, _ = _issued_invoice(api, owner, make_plan(base_price_inr="5000.00"))
    res = api.call("POST", f"/invoices/{inv}/payment-links", billing_admin)
    assert res.status_code == 201, res.text
    link = res.json()
    assert (link["status"], link["amount_inr"], link["provider"]) == ("active", "5900.00", "fake")
    assert link["short_url"].startswith("https://rzp.example.test/")
    # audited in the same transaction as the row
    assert _actions(link["id"]) == ["payment_link.created"]
    again = api.call("POST", f"/invoices/{inv}/payment-links", billing_admin)
    assert (again.status_code, again.json()["code"]) == (409, "link_exists")
    listed = api.call("GET", f"/invoices/{inv}/payment-links", billing_admin).json()
    assert listed["enabled"] is True
    assert [x["id"] for x in listed["data"]] == [link["id"]]


@pytest.mark.usefixtures("payments_on")
def test_FR_PAY_017_disallowed_roles_get_403_and_unknown_invoice_404(
    api: Api,
    owner: Operator,
    make_operator: MakeOperator,
    billing_admin: Operator,
    make_plan: Callable[..., uuid.UUID],
) -> None:
    _, inv, _ = _issued_invoice(api, owner, make_plan())
    agent = make_operator("support_agent")
    assert api.call("POST", f"/invoices/{inv}/payment-links", agent).status_code == 403
    assert api.call("GET", f"/invoices/{inv}/payment-links", agent).status_code == 403
    missing = uuid.uuid4()
    assert api.call("GET", f"/invoices/{missing}/payment-links", billing_admin).status_code == 404
    assert api.call("POST", f"/invoices/{missing}/payment-links", billing_admin).status_code == 404
    assert api.call("POST", f"/payment-links/{missing}/cancel", billing_admin).status_code == 404


@pytest.mark.usefixtures("payments_on")
def test_FR_PAY_018_only_issued_invoices_with_a_balance_get_a_link(
    api: Api, owner: Operator, billing_admin: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    body = provision_payload(make_plan(), start_as="active")
    tid = api.call("POST", "/tenants", owner, json=body).json()["tenant_id"]
    with platform_session() as s:
        draft: Any = s.execute(
            text("SELECT id FROM platform.invoices WHERE tenant_id = :t"), {"t": tid}
        ).scalar_one()
    res = api.call("POST", f"/invoices/{draft}/payment-links", billing_admin)
    assert (res.status_code, res.json()["code"]) == (409, "invalid_state")


def test_FR_PAY_026_links_are_refused_while_online_payment_is_off(
    api: Api, owner: Operator, billing_admin: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    _, inv, _ = _issued_invoice(api, owner, make_plan())
    res = api.call("POST", f"/invoices/{inv}/payment-links", billing_admin)
    assert (res.status_code, res.json()["code"]) == (409, "online_payments_off")
    assert api.call("GET", f"/invoices/{inv}/payment-links", billing_admin).json() == {
        "enabled": False,
        "data": [],
    }
    # the webhook fails closed (404) while off
    assert _post_webhook(api, b"{}").status_code == 404


@pytest.mark.usefixtures("payments_on")
def test_FR_PAY_017_provider_failure_stores_nothing(
    api: Api, owner: Operator, billing_admin: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    _, inv, _ = _issued_invoice(api, owner, make_plan())
    FakeInvoiceLinks.fail_next("unavailable", transient=True)
    assert api.call("POST", f"/invoices/{inv}/payment-links", billing_admin).status_code == 503
    FakeInvoiceLinks.fail_next("auth_failed", transient=False)
    res = api.call("POST", f"/invoices/{inv}/payment-links", billing_admin)
    assert (res.status_code, res.json()["code"]) == (502, "payment_link_failed")
    assert api.call("GET", f"/invoices/{inv}/payment-links", billing_admin).json()["data"] == []


# --- the webhook ----------------------------------------------------------------------------


@pytest.mark.usefixtures("payments_on")
def test_FR_PAY_019_signed_webhook_refetches_and_marks_the_invoice_paid_once(
    api: Api, owner: Operator, billing_admin: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    tid, inv, _ = _issued_invoice(api, owner, make_plan(base_price_inr="5000.00"))
    link = api.call("POST", f"/invoices/{inv}/payment-links", billing_admin).json()
    pay_id = FakeInvoiceLinks.pay(link["provider_link_id"])
    body = webhook_body(link["provider_link_id"], pay_id)
    first = _post_webhook(api, body, event_id="evt_firstdelivery")
    assert first.status_code == 200, first.text
    invoice = api.call("GET", f"/invoices/{inv}", billing_admin).json()
    assert (invoice["status"], invoice["balance_due_inr"]) == ("paid", "0.00")
    [payment] = _payments(inv)
    assert (payment["provider"], payment["method"], payment["reference"]) == ("fake", "upi", pay_id)
    assert payment["recorded_by"] is None
    assert payment["amount_inr"] == Decimal("5900.00")
    assert _actions(str(payment["id"])) == ["payment.online_received"]
    listed = api.call("GET", f"/invoices/{inv}/payment-links", billing_admin).json()["data"]
    assert (listed[0]["status"], listed[0]["payment_id"]) == ("paid", str(payment["id"]))
    # a replay of the same event and a second event for the same payment change nothing
    assert _post_webhook(api, body, event_id="evt_firstdelivery").status_code == 200
    assert _post_webhook(api, body).status_code == 200
    assert len(_payments(inv)) == 1
    with platform_session() as s:
        paid_events: Any = s.execute(
            text(
                "SELECT count(*) FROM platform.audit_events "
                "WHERE subject_tenant_id = :t AND action = 'invoice.paid'"
            ),
            {"t": tid},
        ).scalar_one()
    assert paid_events == 1
    # the operator's list shows the online payment without an operator name
    rows = api.call("GET", f"/invoices/{inv}/payments", billing_admin).json()
    assert (rows[0]["provider"], rows[0]["recorded_by"]) == ("fake", None)


@pytest.mark.usefixtures("payments_on")
def test_FR_PAY_022_bad_signature_401_and_forged_amounts_are_never_trusted(
    api: Api, owner: Operator, billing_admin: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    _, inv, _ = _issued_invoice(api, owner, make_plan())
    link = api.call("POST", f"/invoices/{inv}/payment-links", billing_admin).json()
    body = webhook_body(link["provider_link_id"], "pay_doesnotexist")
    assert _post_webhook(api, body, sig="0" * 64).status_code == 401
    assert _post_webhook(api, body, sig="not-hex").status_code == 401
    # validly signed, but Razorpay knows no such payment: nothing is recorded
    assert _post_webhook(api, body).status_code == 200
    # the link not paid yet (payment created, not captured): nothing is recorded
    pending = FakeInvoiceLinks.pay(link["provider_link_id"], status="authorized")
    assert _post_webhook(api, webhook_body(link["provider_link_id"], pending)).status_code == 200
    assert _payments(inv) == []
    invoice = api.call("GET", f"/invoices/{inv}", billing_admin).json()
    assert invoice["status"] == "issued"


@pytest.mark.usefixtures("payments_on")
def test_FR_PAY_022_unreachable_provider_answers_503_and_the_retry_applies(
    api: Api, owner: Operator, billing_admin: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    _, inv, _ = _issued_invoice(api, owner, make_plan())
    link = api.call("POST", f"/invoices/{inv}/payment-links", billing_admin).json()
    pay_id = FakeInvoiceLinks.pay(link["provider_link_id"])
    body = webhook_body(link["provider_link_id"], pay_id)
    FakeInvoiceLinks.fail_next("unavailable", transient=True)
    assert _post_webhook(api, body, event_id="evt_retriedone").status_code == 503
    # the event id was released, so Razorpay's retry of the same event is processed
    assert _post_webhook(api, body, event_id="evt_retriedone").status_code == 200
    assert len(_payments(inv)) == 1


@pytest.mark.usefixtures("payments_on")
def test_FR_PAY_023_a_payment_that_no_longer_fits_needs_review(
    api: Api, owner: Operator, billing_admin: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    _, inv, _ = _issued_invoice(api, owner, make_plan(base_price_inr="5000.00"))
    link = api.call("POST", f"/invoices/{inv}/payment-links", billing_admin).json()
    manual = {
        "method": "bank_transfer",
        "amount_inr": "1000.00",
        "received_on": "2026-11-10",
        "reference": f"UTR-{letters(10)}",
    }
    assert (
        api.call("POST", f"/invoices/{inv}/payments", billing_admin, json=manual).status_code == 201
    )
    pay_id = FakeInvoiceLinks.pay(link["provider_link_id"])
    assert _post_webhook(api, webhook_body(link["provider_link_id"], pay_id)).status_code == 200
    listed = api.call("GET", f"/invoices/{inv}/payment-links", billing_admin).json()["data"]
    assert (listed[0]["status"], listed[0]["review_reason"]) == (
        "needs_review",
        "amount_exceeds_balance",
    )
    assert [p["provider"] for p in _payments(inv)] == ["manual"]
    assert _actions(link["id"]) == ["payment_link.created", "payment_link.needs_review"]


@pytest.mark.usefixtures("payments_on")
def test_FR_PAY_024_check_applies_a_missed_payment_and_cancel_closes_a_link(
    api: Api, owner: Operator, billing_admin: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    _, inv, _ = _issued_invoice(api, owner, make_plan())
    link = api.call("POST", f"/invoices/{inv}/payment-links", billing_admin).json()
    # nothing paid: the check changes nothing
    checked = api.call("POST", f"/payment-links/{link['id']}/check", billing_admin)
    assert (checked.status_code, checked.json()["status"]) == (200, "active")
    cancelled = api.call("POST", f"/payment-links/{link['id']}/cancel", billing_admin)
    assert (cancelled.status_code, cancelled.json()["status"]) == (200, "cancelled")
    assert _actions(link["id"]) == ["payment_link.created", "payment_link.cancelled"]
    again = api.call("POST", f"/payment-links/{link['id']}/cancel", billing_admin)
    assert (again.status_code, again.json()["code"]) == (409, "invalid_state")
    # a new link; paid, the webhook lost: "Check payment" applies it
    second = api.call("POST", f"/invoices/{inv}/payment-links", billing_admin).json()
    FakeInvoiceLinks.pay(second["provider_link_id"], method="online")
    applied = api.call("POST", f"/payment-links/{second['id']}/check", billing_admin).json()
    assert applied["status"] == "paid"
    assert [p["method"] for p in _payments(inv)] == ["online"]
    assert api.call("GET", f"/invoices/{inv}", billing_admin).json()["status"] == "paid"


# --- the school's billing page --------------------------------------------------------------


@pytest.mark.usefixtures("payments_on")
def test_FR_PAY_027_school_sees_pay_online_only_for_its_own_active_link(
    api: Api,
    owner: Operator,
    billing_admin: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
) -> None:
    plan = make_plan()
    _, inv, subject = _issued_invoice(api, owner, plan, admin_engine)
    _, other_inv, _ = _issued_invoice(api, owner, plan)

    def page_now() -> list[dict[str, Any]]:
        res = api.client.get(
            "/api/v1/tenant/billing/invoices", headers=api.headers(None, tenant_subject=subject)
        )
        assert res.status_code == 200, res.text
        data: list[dict[str, Any]] = res.json()["data"]
        return data

    page = page_now()
    assert page[0]["payment_url"] is None
    link = api.call("POST", f"/invoices/{inv}/payment-links", billing_admin).json()
    other = api.call("POST", f"/invoices/{other_inv}/payment-links", billing_admin).json()
    page = page_now()
    assert [i["payment_url"] for i in page] == [link["short_url"]]
    assert other["short_url"] not in str(page)
    api.call("POST", f"/payment-links/{link['id']}/cancel", billing_admin)
    page = page_now()
    assert page[0]["payment_url"] is None
