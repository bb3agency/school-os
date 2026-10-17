"""The payment provider package without a database: paise exactness, the pinned endpoint list,
the Razorpay request shape (no payer data sent), webhook signatures and payload minimisation,
the configuration (FR-FEE-028; FR-PAY-001, FR-PAY-005, FR-PAY-007, FR-PAY-016; ADR-0042
decision 8). Synthetic fixtures only; httpx's MockTransport stands in for Razorpay."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Environment, PaymentsProviderMode, Settings
from app.fees.payments_config import load_payments_config
from app.fees.providers import get_provider
from app.fees.providers.base import Credentials, LinkRequest, ProviderError
from app.fees.providers.fake import FIXTURES, FakePaymentProvider, FakeState, fixture
from app.fees.providers.razorpay import (
    ALLOWED_ENDPOINTS,
    RazorpayProvider,
    from_paise,
    parse_webhook,
    signature,
    signature_matches,
    to_paise,
)

CREDS = Credentials(key_id="rzp_test_SYNTHABCDEF", key_secret="synthetic-key-secret")
PROVIDERS_DIR = Path(__file__).resolve().parents[2] / "app" / "fees" / "providers"


# --- money (FR-FEE-028) --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rupees", "paise"),
    [
        ("0.01", 1),
        ("1.00", 100),
        ("3500.00", 350000),
        ("1234567.89", 123456789),
        ("10000000.00", 1000000000),
    ],
)
def test_FR_FEE_028_paise_conversion_is_exact(rupees: str, paise: int) -> None:
    assert to_paise(Decimal(rupees)) == paise
    assert from_paise(paise) == Decimal(rupees)


def test_FR_FEE_028_paise_round_trip_for_every_paisa_in_a_range() -> None:
    for n in range(0, 100_001, 37):
        assert to_paise(from_paise(n)) == n


@pytest.mark.parametrize("bad", [Decimal("10.005"), Decimal("NaN"), Decimal("Infinity"), 10.5, 3])
def test_FR_FEE_028_no_float_no_rounding(bad: Any) -> None:
    with pytest.raises(ValueError, match=r"decimal|paise"):
        to_paise(bad)


@pytest.mark.parametrize("bad", [1.5, True, "100", None])
def test_FR_FEE_028_from_paise_takes_integers_only(bad: Any) -> None:
    with pytest.raises(ProviderError):
        from_paise(bad)


# --- endpoints (FR-PAY-016) ----------------------------------------------------------------------


def test_FR_PAY_016_pinned_endpoints_have_no_money_movement() -> None:
    paths = " ".join(p for _, _, p in ALLOWED_ENDPOINTS)
    for forbidden in ("payout", "transfer", "route", "fund_account", "contacts", "virtual"):
        assert forbidden not in paths.lower(), forbidden
    # Orders are only read (their receipt is our intent id), never created.
    assert not [(h, m, p) for h, m, p in ALLOWED_ENDPOINTS if "orders" in p and m != "GET"]
    assert {m for _, m, _ in ALLOWED_ENDPOINTS} <= {"GET", "POST", "PATCH", "DELETE"}


def test_FR_PAY_016_provider_code_names_no_other_razorpay_endpoint() -> None:
    """A code search over the provider package: no money-moving Razorpay resource is named, and
    every request goes through the one pinned ``_call``."""
    for path in PROVIDERS_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for word in ("/payouts", "/transfers", "/fund_accounts", '/v1/orders"', "linked_accounts"):
            assert word not in source, (path.name, word)
        if path.name == "razorpay.py":
            assert source.count("self._client.request(") == 1
            literals = re.findall(r'"(/v[12]/[a-z_/]+)', source)
            assert literals, "path literals found"
            for literal in literals:
                prefixes = [
                    p.split("[")[0].lstrip("^").rstrip("$") for _, _, p in ALLOWED_ENDPOINTS
                ]
                assert any(x.startswith(literal) or literal.startswith(x) for x in prefixes), (
                    literal
                )


def test_FR_PAY_016_client_refuses_anything_not_pinned() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = RazorpayProvider(Settings(env=Environment.CI), client=client)
    with pytest.raises(ProviderError) as caught:
        provider._call("api", "POST", "/v1/payouts", creds=CREDS)
    assert caught.value.code == "endpoint_not_allowed"
    with pytest.raises(ProviderError):
        provider._call("api", "POST", "/v1/orders", creds=CREDS)
    with pytest.raises(ProviderError):
        provider.fetch_payment(CREDS, "pay_../../v1/payouts")
    assert calls == []


# --- request shape (FR-PAY-005; ADR-0042 decision 8) ---------------------------------------------


def _link_body(amount: int, reference: str) -> dict[str, Any]:
    return {
        "id": "plink_SYNTHABC",
        "status": "created",
        "amount": amount,
        "amount_paid": 0,
        "currency": "INR",
        "reference_id": reference,
        "short_url": "https://rzp.example.test/abc",
        "order_id": None,
        "expire_by": 1790600000,
    }


def test_FR_PAY_005_link_carries_amount_reference_and_nothing_personal() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json=_link_body(body["amount"], body["reference_id"]))

    provider = RazorpayProvider(
        Settings(env=Environment.CI), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    reference = "0" * 32
    out = provider.create_link(
        CREDS,
        LinkRequest(
            amount=Decimal("3500.50"),
            reference_id=reference,
            description="School fees · Synthetic School · ABCD1234",
            expire_by=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
            notes={"intent": reference},
            callback_url="https://my.example.test/p/paid",
        ),
    )
    assert out.amount == Decimal("3500.50")
    request = seen[0]
    assert request.url.host == "api.razorpay.com"
    assert request.url.path == "/v1/payment_links"
    assert request.headers["authorization"].startswith("Basic ")
    body = json.loads(request.content)
    assert body["amount"] == 350050
    assert body["currency"] == "INR"
    assert body["accept_partial"] is False
    assert body["notify"] == {"sms": False, "email": False}
    assert body["reference_id"] == reference
    assert body["notes"] == {"intent": reference}
    assert body["callback_method"] == "get"
    assert "customer" not in body
    assert set(body) <= {
        "amount",
        "currency",
        "accept_partial",
        "reference_id",
        "description",
        "expire_by",
        "notify",
        "reminder_enable",
        "notes",
        "callback_url",
        "callback_method",
    }


def test_FR_PAY_002_oauth_token_calls_use_bearer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "pay_SYNTH",
                "status": "captured",
                "amount": 100,
                "currency": "INR",
                "order_id": "order_SYNTH",
                "method": "upi",
                "captured": True,
                "amount_refunded": 0,
                "fee": 2,
                "tax": 0,
                "created_at": 1790000000,
                "vpa": "synthetic@example",
            },
        )

    provider = RazorpayProvider(
        Settings(env=Environment.CI), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    payment = provider.fetch_payment(Credentials(access_token="synthetic-token"), "pay_SYNTH")
    assert seen[0].headers["authorization"] == "Bearer synthetic-token"
    assert payment.amount == Decimal("1.00")
    assert not hasattr(payment, "vpa")


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "auth_failed"),
        (404, "not_found"),
        (429, "rate_limited"),
        (503, "unavailable"),
        (400, "invalid_request"),
    ],
)
def test_FR_PAY_001_errors_are_codes_only(status: int, code: str) -> None:
    provider = RazorpayProvider(
        Settings(env=Environment.CI),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(status, json={"error": {"description": "x"}})
            )
        ),
    )
    with pytest.raises(ProviderError) as caught:
        provider.fetch_payment(CREDS, "pay_SYNTH")
    assert caught.value.code == code
    assert "description" not in str(caught.value)


# --- webhooks (FR-PAY-007) -----------------------------------------------------------------------


def test_FR_PAY_007_signature_is_hex_hmac_sha256_of_raw_body() -> None:
    body = b'{"event":"payment.captured"}'
    secret = b"synthetic-webhook-secret"
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    assert signature(secret, body) == expected
    assert signature_matches(secret, body, expected.upper())
    assert not signature_matches(secret, body + b" ", expected)
    assert not signature_matches(b"other", body, expected)


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_PRV_024_webhook_payload_is_minimised(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    parsed = parse_webhook(raw)
    text = json.dumps(parsed)
    for leaked in ("vpa", "email", "contact", "synthetic.payer", "+91", "acquirer", "card", "bank"):
        assert leaked not in text, (path.stem, leaked)
    assert parsed["event"] == raw["event"]


def test_FR_PAY_007_amounts_in_events_become_rupees() -> None:
    parsed = parse_webhook(fixture("payment_link.paid"))
    assert parsed["payment"]["amount"] == "3500.00"
    assert parsed["link"]["reference_id"] == "0" * 32
    assert parsed["order"]["receipt"] == "0" * 32


def test_FR_PAY_007_not_an_event_is_refused() -> None:
    with pytest.raises(ProviderError):
        parse_webhook({"event": "Robert'); DROP TABLE"})
    with pytest.raises(ProviderError):
        parse_webhook({"payload": {}})


# --- configuration and mode (FR-PAY-001) ---------------------------------------------------------


def test_FR_PAY_001_config_loads_and_pins_hosts() -> None:
    cfg = load_payments_config()
    assert str(cfg.razorpay.api_base_url).startswith("https://api.razorpay.com")
    assert cfg.links.default_expiry_days == 7
    assert cfg.webhook.previous_secret_days == 7
    assert "payment_link.paid" in cfg.razorpay.webhook_events


def test_FR_PAY_001_fake_provider_refused_in_production() -> None:
    with pytest.raises(ValueError, match="SOS_PAYMENTS_PROVIDER_MODE=fake"):
        Settings(
            env=Environment.PROD,
            payments_provider_mode=PaymentsProviderMode.FAKE,
        )


def test_FR_PAY_001_partner_secret_must_be_real_in_staging() -> None:
    with pytest.raises(ValueError, match="RAZORPAY_PARTNER_CLIENT_SECRET"):
        Settings(
            env=Environment.STAGING,
            payments_provider_mode=PaymentsProviderMode.LIVE,
            razorpay_partner_client_secret=SecretStr("dev-only-secret-value"),
        )


def test_FR_PAY_001_webhook_origin_has_no_path() -> None:
    with pytest.raises(ValueError, match="SOS_WEBHOOK_BASE_URL"):
        Settings(env=Environment.CI, webhook_base_url="https://api.example.test/hooks")


def test_FR_PAY_001_local_mode_is_the_fake() -> None:
    assert isinstance(get_provider(Settings(env=Environment.CI)), FakePaymentProvider)


def test_FR_PAY_011_fake_refunds_are_idempotent_by_receipt() -> None:
    state = FakeState()
    fake = FakePaymentProvider(state)
    plink = fake.create_link(
        CREDS,
        LinkRequest(
            amount=Decimal("100.00"),
            reference_id="1" * 32,
            description="x",
            expire_by=dt.datetime(2026, 12, 1, tzinfo=dt.UTC),
            notes={},
        ),
    )
    payment = state.pay(plink.id)
    first = fake.create_refund(CREDS, payment.id, Decimal("40.00"), receipt="r1", notes={})
    again = fake.create_refund(CREDS, payment.id, Decimal("40.00"), receipt="r1", notes={})
    assert first.id == again.id
    with pytest.raises(ProviderError):
        fake.fetch_payment(Credentials(key_id="rzp_test_OTHERX", key_secret="s"), payment.id)
