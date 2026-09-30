"""Email provider interface, fake, SES adapter, templates and settings guards (docs/03 §5 Email
via SES, templates EN/TE; US-102; SEC-009; invariants 5, 10, 13). No database, no network."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.config import Config
from botocore.stub import Stubber
from pydantic import SecretStr, ValidationError

from app.core.config import EmailProviderKind, Environment, KeyWrapperKind, Settings
from app.notifications import email
from app.notifications.templates import TemplateError

SES_CT = "UTF-8"
TO = "synthetic.invitee@example.test"
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"


def _prod(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "env": Environment.PROD,
        "key_wrapper": KeyWrapperKind.KMS,
        "database_url": SecretStr("postgresql+psycopg://sos_app:x@db:5432/schoolos"),
        "platform_database_url": SecretStr("postgresql+psycopg://sos_platform:y@db:5432/schoolos"),
        "service_token_key": SecretStr("k" * 48),
        "billing_supplier_legal_name": "Example Technologies Private Limited",
        "billing_supplier_gstin": "37ABCDE1234F1Z5",
    }
    base.update(overrides)
    return Settings(**base)


def _message(**overrides: Any) -> email.EmailMessage:
    values: dict[str, Any] = {
        "to": TO,
        "subject": "Invitation",
        "text": "Hello\n",
        "template_key": "invitation.staff",
        "language": "en",
    }
    values.update(overrides)
    return email.EmailMessage(**values)


# --- settings -------------------------------------------------------------------------------


def test_email_is_off_by_default_and_nothing_is_sent() -> None:
    s = Settings(env=Environment.LOCAL)
    assert s.email_provider is EmailProviderKind.OFF
    assert s.email_enabled is False
    assert email.get_sender(s) is None


def test_SEC_009_email_on_needs_a_sender_and_a_link_target() -> None:
    with pytest.raises(ValidationError, match="SOS_EMAIL_FROM"):
        Settings(email_provider=EmailProviderKind.FAKE, email_app_url="http://localhost:3000")
    with pytest.raises(ValidationError, match="SOS_EMAIL_APP_URL"):
        Settings(email_provider=EmailProviderKind.FAKE, email_from="SchoolOS <n@example.test>")
    local = Settings(
        email_provider=EmailProviderKind.FAKE,
        email_from="SchoolOS <no-reply@example.test>",
        email_app_url="http://localhost:3000",
    )
    assert email.get_sender(local) is email.fake_sender()


def test_SEC_009_staging_and_prod_refuse_the_fake_and_non_https_links() -> None:
    sender = {"email_from": "SchoolOS <no-reply@example.test>"}
    with pytest.raises(ValidationError, match="SOS_EMAIL_PROVIDER=fake"):
        _prod(
            email_provider=EmailProviderKind.FAKE,
            email_app_url="https://app.example.test",
            **sender,
        )
    with pytest.raises(ValidationError, match="public https"):
        _prod(email_provider=EmailProviderKind.SES, email_app_url="http://localhost:3000", **sender)
    ok = _prod(
        email_provider=EmailProviderKind.SES, email_app_url="https://app.example.test", **sender
    )
    assert ok.email_enabled
    assert _prod().email_enabled is False  # off needs nothing


# --- templates ------------------------------------------------------------------------------


def test_invitation_template_renders_in_english_and_telugu(telugu_on: None) -> None:
    params = {
        "school": "Synthetic High School",
        "sign_in_url": "https://app.example.test/",
        "expires_on": "28/10/2026",
    }
    en = email.render_email("invitation.staff", params, "en")
    te = email.render_email("invitation.staff", params, "te")
    assert en.language == "en"
    assert te.language == "te"
    assert "Synthetic High School" in en.subject
    assert "Synthetic High School" in te.subject
    for content in (en, te):
        assert "https://app.example.test/" in content.text
        assert "28/10/2026" in content.text
        assert "{" not in content.subject + content.text
        assert content.text.endswith("\n")
    assert re.search(r"[ఀ-౿]", te.text), "Telugu script"
    assert email.render_email("invitation.staff", params, "fr").language == "en"


def test_ADR_0036_email_is_english_while_telugu_is_hidden() -> None:
    params = {
        "school": "Synthetic High School",
        "sign_in_url": "https://app.example.test/",
        "expires_on": "28/10/2026",
    }
    for template_key in email.email_catalog():
        te = email.render_email(template_key, params, "te")
        assert te.language == "en"
        assert te == email.render_email(template_key, params, "en")
        assert not re.search(r"[ఀ-౿]", te.subject + te.text), "no Telugu script"


def test_subject_is_one_line_whatever_the_school_name() -> None:
    params = {"school": "A\r\nBcc: x@example.test", "sign_in_url": "u", "expires_on": "d"}
    subject = email.render_email("invitation.staff", params, "en").subject
    assert "\r" not in subject
    assert "\n" not in subject


def test_template_params_must_match_exactly() -> None:
    with pytest.raises(TemplateError):
        email.render_email("invitation.staff", {"school": "x"}, "en")
    with pytest.raises(TemplateError):
        email.render_email("no.such", {}, "en")


def test_every_email_template_has_both_languages() -> None:
    catalog = email.email_catalog()
    assert "invitation.staff" in catalog
    for template in catalog.values():
        assert set(template.subjects) == set(template.bodies) == {"en", "te"}


def test_invitation_window_matches_the_acceptance_migration() -> None:
    sql = (MIGRATIONS / "0007_accept_invitations.py").read_text(encoding="utf-8")
    days = email.invitation_valid_days()
    assert f"interval '{days} days'" in sql
    assert email.resend_cooldown_s() >= 60


# --- providers ------------------------------------------------------------------------------


def test_fake_sender_keeps_messages_and_never_logs_the_address(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = email.FakeEmailSender()
    assert fake.send(_message()) == "fake-1"
    assert fake.sent[0].to == TO
    assert TO not in repr(fake.sent[0])
    out = capsys.readouterr()
    assert TO not in out.out + out.err


def _ses() -> tuple[Any, Stubber]:
    client = boto3.client(
        "sesv2",
        region_name="ap-south-1",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
        config=Config(retries={"max_attempts": 1}),
    )
    return client, Stubber(client)


def test_ses_sender_sends_plain_utf8_text_with_a_template_tag() -> None:
    client, stub = _ses()
    sender = email.SesEmailSender(
        client, sender="SchoolOS <no-reply@example.test>", configuration_set="sos-events"
    )
    stub.add_response(
        "send_email",
        {"MessageId": "m-1"},
        {
            "FromEmailAddress": "SchoolOS <no-reply@example.test>",
            "Destination": {"ToAddresses": [TO]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": "Invitation", "Charset": SES_CT},
                    "Body": {"Text": {"Data": "Hello\n", "Charset": SES_CT}},
                }
            },
            "EmailTags": [{"Name": "template", "Value": "invitation-staff"}],
            "ConfigurationSetName": "sos-events",
        },
    )
    with stub:
        assert sender.send(_message()) == "m-1"


@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("MessageRejected", email.EmailRejected),
        ("AccountSuspendedException", email.EmailRejected),
        ("TooManyRequestsException", email.EmailUnavailable),
        ("InternalFailure", email.EmailUnavailable),
    ],
)
def test_ses_errors_are_permanent_or_retryable_and_carry_no_address(
    code: str, error: type[email.EmailError]
) -> None:
    client, stub = _ses()
    sender = email.SesEmailSender(client, sender="SchoolOS <no-reply@example.test>")
    stub.add_client_error("send_email", service_error_code=code, http_status_code=400)
    with stub, pytest.raises(error) as caught:
        sender.send(_message())
    assert TO not in str(caught.value)
    assert caught.value.__cause__ is None
