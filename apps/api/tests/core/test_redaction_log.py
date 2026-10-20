"""Log redaction of credentials and provider identifiers (R2-3I hardening; SEC-008, SEC-037,
invariant 5; docs/07 §11.2 "no token in any log line").

``core.redaction.redact_log`` is what every structured log value, local traceback and span name
passes through: :func:`redact` (Aadhaar, mobiles, emails) plus parent link handles, Razorpay route
values and ids, signatures, Meta access tokens, OAuth codes, bank UTRs and long numbers. The
second half logs through the loggers of the messaging, fees (UPI and payments) and public modules
the way their code does and checks the JSON lines. Every value is synthetic, generated here.
"""

from __future__ import annotations

import io
import json
import secrets
import string
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.config import Environment, Settings
from app.core.logging import clear_context, get_logger, setup_logging
from app.core.redaction import redact, redact_log
from app.core.telemetry import sanitize_attributes
from app.public import tokens

_ALNUM = string.ascii_letters + string.digits


def _alnum(n: int) -> str:
    return "".join(secrets.choice(_ALNUM) for _ in range(n))


def _mobile() -> str:
    return secrets.choice("6789") + "".join(secrets.choice(string.digits) for _ in range(9))


def _digits(n: int) -> str:
    rest = "".join(secrets.choice(string.digits) for _ in range(n - 1))
    return secrets.choice("123456789") + rest


MOBILE = _mobile()
PHONE_FORMS = [
    MOBILE,
    f"+91{MOBILE}",
    f"+91 {MOBILE}",
    f"+91 {MOBILE[:5]} {MOBILE[5:]}",
    f"+91-{MOBILE}",
    f"91{MOBILE}",
    f"0{MOBILE}",
    f"{MOBILE[:5]}-{MOBILE[5:]}",
    f"{MOBILE[:3]} {MOBILE[3:6]} {MOBILE[6:]}",
]
UTR_NUMERIC = _digits(12)  # UPI UTR / RRN
UTR_IMPS = _digits(16)
UTR_NEFT = "SYNB" + "R5" + _digits(16)  # bank prefix + R/N + digits (22 characters)
RAZORPAY_IDS = [
    "pay_" + _alnum(13) + "9",
    "order_" + _alnum(13) + "Q",
    "plink_" + _alnum(13) + "7",
    "rfnd_" + _alnum(13) + "X",
    "rzp_test_" + _alnum(13) + "1",
    "rzp_live_" + _alnum(13) + "Z",
]
RAZORPAY_SIGNATURE = secrets.token_hex(32)
META_TOKEN = "EAAG" + _alnum(160)
WABA_ID = _digits(15)
OAUTH_CODE = "4/0A" + _alnum(40) + "-_x"
PARENT_TOKEN = tokens.new_parent_token()
ROUTE_VALUE = tokens.new_route_token()
_CROCKFORD = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
CERT_CODE = "SYNT" + "".join(secrets.choice(_CROCKFORD) for _ in range(11)) + "7"
# Fixed draws with almost no digits (a random draw may have any mix; the rule must not need digits).
CERT_CODE_FEW_DIGITS = "SYNTJXGDNVC7NEZ7"
CERT_CODE_NO_DIGITS = "SYNTJXGDNVCPNEZQ"
CERT_CODE_PRINTED = "SYNT-JXGD-NVC7-NEZ7"
JWT = "eyJhbGciOiJSUzI1NiJ9." + _alnum(30) + "." + _alnum(40)

SECRETS = {
    "utr_numeric": UTR_NUMERIC,
    "utr_imps": UTR_IMPS,
    "utr_neft": UTR_NEFT,
    "razorpay_signature": RAZORPAY_SIGNATURE,
    "meta_token": META_TOKEN,
    "waba_id": WABA_ID,
    "oauth_code": OAUTH_CODE,
    "parent_token": PARENT_TOKEN,
    "route_value": ROUTE_VALUE,
    "cert_code": CERT_CODE,
    "cert_code_few_digits": CERT_CODE_FEW_DIGITS,
    "cert_code_no_digits": CERT_CODE_NO_DIGITS,
    "cert_code_printed": CERT_CODE_PRINTED,
    "jwt": JWT,
    **{f"razorpay_id_{i}": v for i, v in enumerate(RAZORPAY_IDS)},
}

# Values a log line must keep readable (ids, codes and names the code itself writes).
KEPT = [
    str(uuid.uuid4()),
    "req_" + uuid.uuid4().hex,
    "messaging.webhook.unknown_waba",
    "security.webhook.rejected",
    "signature_mismatch",
    "public_lookup_fail",
    "order_received",
    "pay_online",
    "GET /api/v1/public/certificates/{code}",
    "POST /api/v1/webhooks/razorpay/{route}",
    "app.fees.tasks_online",
    "RequestValidationError",
    "WebhookUnknownRoute",
    "SOS/2026-27/000123",
    "RCT/2026-27/000042",
    "2026-10-21T10:00:00.000Z",
    "3500.00",
    "parent_dues",
]


# --- redact_log itself ---------------------------------------------------------------------------


@pytest.mark.parametrize("phone", PHONE_FORMS)
def test_SEC_008_indian_mobile_numbers_in_every_form_are_masked(phone: str) -> None:
    out = redact_log(f"sent to {phone} ok")
    assert MOBILE not in out
    assert MOBILE[-4:] in out  # the last four stay, as everywhere (XXXXXX1234)
    assert "[redacted-id]" not in out  # a masked mobile is not masked twice


@pytest.mark.parametrize("name", sorted(SECRETS))
def test_SEC_037_credentials_and_provider_ids_are_masked(name: str) -> None:
    value = SECRETS[name]
    for text in (value, f"x {value} y", f"({value})", f"id={value},"):
        out = redact_log(text)
        assert value not in out, (name, out)
        assert "[redacted" in out or "XXXX" in out, (name, out)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"Authorization: Bearer {JWT}", "Authorization: Bearer [redacted-token]"),
        (f"code={OAUTH_CODE}&state=abc123", "code=[redacted]&state=[redacted]"),
        ("?hub.verify_token=synthetic-verify&x=1", "?hub.verify_token=[redacted]&x=1"),
        (f"sha256={RAZORPAY_SIGNATURE}", "sha256=[redacted]"),
        (f"utr={UTR_NUMERIC}", "utr=[redacted]"),
        (f"razorpay_signature={RAZORPAY_SIGNATURE}", "razorpay_signature=[redacted]"),
    ],
)
def test_SEC_037_name_value_secrets_keep_the_name_only(text: str, expected: str) -> None:
    assert redact_log(text) == expected


@pytest.mark.parametrize("value", KEPT)
def test_SEC_008_ids_codes_and_names_stay_readable(value: str) -> None:
    assert redact_log(value) == value


def test_SEC_008_redact_log_includes_redact_and_redact_is_unchanged() -> None:
    text = f"mobile {MOBILE} mail a.b@example.org utr {UTR_NUMERIC}"
    assert MOBILE not in redact_log(text)
    assert "a.b@example.org" not in redact_log(text)
    # Content pipelines (notices, knowledge, statements) keep calling redact(), which leaves
    # UTRs and ids alone: only logs and traces drop them.
    assert UTR_NUMERIC in redact(text)


@settings(max_examples=200, deadline=None)
@given(st.text(alphabet=_ALNUM + " _-=:/.&", max_size=120))
def test_SEC_008_redact_log_is_idempotent(text: str) -> None:
    once = redact_log(text)
    assert redact_log(once) == once


@settings(max_examples=100, deadline=None)
@given(st.integers(min_value=0, max_value=10_000))
def test_SEC_037_random_parent_tokens_and_route_values_never_survive(seed: int) -> None:
    del seed
    for value in (tokens.new_parent_token(), tokens.new_route_token()):
        assert value not in redact_log(f"key {value}")


def test_SEC_037_span_attributes_of_instrumentation_are_masked() -> None:
    attrs = sanitize_attributes(
        {"messaging.destination.name": f"pay {RAZORPAY_IDS[0]}", "http.route": "/x/{code}"}
    )
    assert RAZORPAY_IDS[0] not in str(attrs)
    assert attrs["http.route"] == "/x/{code}"


# --- structured logs of the messaging, fees and public modules -----------------------------------


class _Captured:
    def __init__(self) -> None:
        self.stream = io.StringIO()

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(x) for x in self.stream.getvalue().splitlines() if x.strip()]

    @property
    def text(self) -> str:
        return self.stream.getvalue()


@pytest.fixture
def capture() -> Iterator[_Captured]:
    cap = _Captured()
    setup_logging(Settings(env=Environment.CI, version="2026.10.1"), stream=cap.stream)
    clear_context()
    yield cap
    clear_context()
    setup_logging(Settings(env=Environment.CI, version="2026.10.1"))


MODULE_LOGGERS = [
    "app.messaging.webhook",
    "app.messaging.service",
    "app.messaging.sends.service",
    "app.fees.online",
    "app.fees.upi",
    "app.fees.statements",
    "app.fees.webhook",
    "app.public.guards",
    "app.public.service",
    "app.identity.oidc",
]
# Allowlisted fields that take free-ish strings (the others are ids, numbers or generated).
TEXT_FIELDS = ["action", "outcome", "error_code", "resource_type", "policy", "route"]


@pytest.mark.parametrize("logger", MODULE_LOGGERS)
def test_SEC_008_module_log_lines_never_carry_phones_utrs_ids_or_tokens(
    capture: _Captured, logger: str
) -> None:
    log = get_logger(logger)
    values = [*PHONE_FORMS, *SECRETS.values()]
    for value in values:
        for field in TEXT_FIELDS:
            log.warning("hardening.probe", **{field: f"x {value} y"})
        log.warning("hardening.probe", resource_id=value)  # not a UUID: redacted as text
        log.warning("hardening.probe", count=value)
    try:
        raise ValueError(f"provider said {RAZORPAY_IDS[0]} for {MOBILE} with {PARENT_TOKEN}")
    except ValueError:
        log.exception("hardening.failed")
    text = capture.text
    assert MOBILE not in text
    for name, value in SECRETS.items():
        assert value not in text, name
    lines = capture.lines()
    assert all(line["event"].startswith("hardening.") for line in lines)
    assert lines[-1]["error_type"] == "ValueError"


def test_SEC_008_stdlib_records_from_provider_clients_never_render_arguments(
    capture: _Captured,
) -> None:
    import logging

    logging.getLogger("app.fees.providers.razorpay").warning(
        "razorpay call failed for %s with key %s", RAZORPAY_IDS[0], RAZORPAY_IDS[4]
    )
    logging.getLogger("app.messaging.providers.meta_cloud").error(
        "graph error token=%s phone=%s", META_TOKEN, MOBILE
    )
    text = capture.text
    for value in (RAZORPAY_IDS[0], RAZORPAY_IDS[4], META_TOKEN, MOBILE):
        assert value not in text


def test_SEC_008_local_tracebacks_are_masked_too() -> None:
    cap = _Captured()
    setup_logging(Settings(env=Environment.LOCAL, version="dev"), stream=cap.stream)
    try:
        try:
            raise RuntimeError(f"route {ROUTE_VALUE} sig {RAZORPAY_SIGNATURE} utr {UTR_NUMERIC}")
        except RuntimeError:
            get_logger("app.fees.webhook").exception("hardening.failed")
    finally:
        setup_logging(Settings(env=Environment.CI, version="2026.10.1"))
    line = cap.lines()[0]
    assert "traceback" in line
    for value in (ROUTE_VALUE, RAZORPAY_SIGNATURE, UTR_NUMERIC):
        assert value not in cap.text
