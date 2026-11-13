"""SchoolOS invoice payment-link providers and settings guards, without a database
(FA-13; FR-PAY-025, FR-PAY-026, FR-PAY-028)."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import DeploymentMode, Environment, PaymentsProviderMode, Settings
from app.platform.link_providers import get_provider
from app.platform.link_providers.base import InvoiceLinkRequest, LinkProviderError
from app.platform.link_providers.fake import FakeInvoiceLinks
from app.platform.link_providers.razorpay import (
    ALLOWED_ENDPOINTS,
    RazorpayInvoiceLinks,
    from_paise,
    parse_payment,
    signature,
    signature_matches,
    to_paise,
)

REAL = "x" * 24


def _guard_error(**fields: Any) -> str:
    """Run only the FA-13 guard on settings built without validation of the others."""
    s = Settings.model_construct(**{**Settings().model_dump(), **fields})
    try:
        s._guard_platform_payments()
    except ValueError as exc:
        return str(exc)
    return ""


def test_FR_PAY_026_off_by_default_and_fake_locally() -> None:
    s = Settings()
    assert s.platform_payments_enabled is False
    assert s.effective_platform_payments_provider_mode is PaymentsProviderMode.FAKE
    assert isinstance(get_provider(s), FakeInvoiceLinks)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"platform_payments_provider_mode": PaymentsProviderMode.FAKE}, "fake is not allowed"),
        ({"platform_razorpay_key_id": None}, "KEY_ID is required"),
        ({"platform_razorpay_key_id": "rzp_test_abcdef123"}, "live key in prod"),
        ({"platform_razorpay_key_secret": SecretStr("dev-only-secret-value")}, "KEY_SECRET"),
        ({"platform_razorpay_webhook_secret": None}, "WEBHOOK_SECRET"),
        ({"platform_razorpay_webhook_secret": SecretStr("short")}, "WEBHOOK_SECRET"),
        ({"deployment_mode": DeploymentMode.DEDICATED}, "shared tier only"),
    ],
)
def test_FR_PAY_026_production_guards(fields: dict[str, Any], message: str) -> None:
    base = {
        "env": Environment.PROD,
        "platform_payments_enabled": True,
        "platform_razorpay_key_id": "rzp_live_abcdef123",
        "platform_razorpay_key_secret": SecretStr(REAL),
        "platform_razorpay_webhook_secret": SecretStr(REAL),
    }
    assert _guard_error(**base) == ""
    assert message in _guard_error(**{**base, **fields})
    # staging may use a test key; when off nothing is required
    assert (
        _guard_error(
            **{**base, "env": Environment.STAGING, "platform_razorpay_key_id": "rzp_test_abcdef123"}
        )
        == ""
    )
    assert _guard_error(env=Environment.PROD, platform_payments_enabled=False) == ""


def test_FR_PAY_026_key_id_shape_is_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(platform_razorpay_key_id="not-a-key")


def test_FR_PAY_025_money_is_exact_paise() -> None:
    assert to_paise(Decimal("5898.82")) == 589882
    assert from_paise(589882) == Decimal("5898.82")
    with pytest.raises(ValueError, match="paise"):
        to_paise(Decimal("1.005"))
    with pytest.raises(LinkProviderError):
        from_paise(5898.82)


def test_FR_PAY_025_only_link_and_payment_endpoints() -> None:
    assert {(m, p.split("/")[2]) for m, p in ALLOWED_ENDPOINTS} == {
        ("POST", "payment_links$"),
        ("GET", "payment_links"),
        ("POST", "payment_links"),
        ("GET", "payments"),
    }
    # no refunds, payouts, mandates, subscriptions or customers
    assert not [
        p
        for _, p in ALLOWED_ENDPOINTS
        if any(w in p for w in ("refund", "payout", "token", "subscription", "customer", "order"))
    ]


def test_FR_PAY_028_webhook_signature_is_hmac_sha256_hex() -> None:
    body = b'{"event":"payment_link.paid"}'
    sig = signature(b"secret-value", body)
    assert len(sig) == 64
    assert signature_matches(b"secret-value", body, sig.upper())
    assert not signature_matches(b"other-secret", body, sig)


def test_FR_PAY_025_razorpay_requests_are_minimal_and_answers_minimised() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/payment_links":
            return httpx.Response(
                200,
                json={
                    "id": "plink_ABC123",
                    "status": "created",
                    "amount": 589882,
                    "amount_paid": 0,
                    "currency": "INR",
                    "short_url": "https://rzp.io/i/abc",
                    "reference_id": "ref",
                    "order_id": "order_X1",
                    "customer": {"email": "payer@example.test", "contact": "+919999999999"},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "pay_P1",
                "status": "captured",
                "amount": 589882,
                "currency": "INR",
                "order_id": "order_X1",
                "method": "card",
                "captured": True,
                "created_at": 1_800_000_000,
                "email": "payer@example.test",
                "contact": "+919999999999",
                "card": {"last4": "1111"},
            },
        )

    settings = Settings(
        platform_razorpay_key_id="rzp_test_abcdef123",
        platform_razorpay_key_secret=SecretStr(REAL),
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = RazorpayInvoiceLinks(settings, client=client)
    link = provider.create_link(
        InvoiceLinkRequest(
            amount=Decimal("5898.82"),
            reference_id="0192a0de-0000-7000-8000-00000000a001",
            description="SchoolOS invoice SOS/26-27/000123",
            expire_by=dt.datetime(2026, 12, 1, tzinfo=dt.UTC),
            notes={"invoice_number": "SOS/26-27/000123"},
        )
    )
    assert (link.id, link.amount, link.order_id) == ("plink_ABC123", Decimal("5898.82"), "order_X1")
    sent = json.loads(seen[0].content)
    assert sent["notify"] == {"sms": False, "email": False}
    assert sent["accept_partial"] is False
    assert "customer" not in sent
    payment = provider.fetch_payment("pay_P1")
    assert payment.method == "online"
    assert not hasattr(payment, "email")
    with pytest.raises(LinkProviderError, match="invalid_request"):
        provider.fetch_payment("../v1/payouts")
    assert (
        parse_payment(
            {
                "id": "pay_U",
                "status": "captured",
                "amount": 100,
                "currency": "INR",
                "method": "upi",
                "captured": True,
                "created_at": 1,
            }
        ).method
        == "upi"
    )


def test_FR_PAY_026_fake_provider_is_refused_in_production() -> None:
    s = Settings.model_construct(
        **{
            **Settings().model_dump(),
            "env": Environment.PROD,
            "platform_payments_provider_mode": PaymentsProviderMode.FAKE,
        }
    )
    with pytest.raises(RuntimeError, match="refused"):
        get_provider(s)
