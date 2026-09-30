"""Email delivery: provider interface, local fake and Amazon SES (docs/03 §5 "Email (AWS SES)":
invites, templates EN/TE; docs/14 "Email delivery"; invariants 5 and 10).

Producers never send mail inside a request. :func:`app.notifications.service.request_email`
queues an outbox event with IDs only (``user_id``, ``template_key``) in the producer's
transaction; the worker task ``notifications.send_email`` resolves the address and language
from the person's profile, renders the bilingual template (``email_templates.yaml``) and calls
the configured :class:`EmailSender`. Addresses, subjects and bodies are never logged, traced or
put in an outbox payload or audit summary: log lines carry the template key and IDs only.

Providers (``SOS_EMAIL_PROVIDER``): ``off`` (default: nothing is queued or sent), ``fake``
(:class:`FakeEmailSender`, in memory; local/CI only, refused in staging/prod by the settings
guard) and ``ses`` (:class:`SesEmailSender`, Amazon SES v2 in ``AWS_REGION`` with the task
role; no keys in code, invariant 10).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any, Final, Protocol

import boto3
import yaml
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import EmailProviderKind, Settings, get_settings
from app.core.languages import output_language
from app.core.logging import get_logger
from app.notifications.templates import (
    LANGUAGES,
    TemplateError,
    format_message,
    placeholders,
)

log = get_logger(__name__)

_KEY_RE: Final = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
_NAME_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_HEADER_BREAK: Final = re.compile(r"[\r\n]+")
# SES errors that retrying will not fix (bad address, sender not verified, account paused).
PERMANENT_SES_ERRORS: Final = frozenset(
    {
        "MessageRejected",
        "MailFromDomainNotVerifiedException",
        "AccountSuspendedException",
        "SendingPausedException",
        "BadRequestException",
        "NotFoundException",
    }
)


class EmailError(RuntimeError):
    """Base class; ``code`` is safe to log (never the address or the message)."""

    code = "email_error"


class EmailUnavailable(EmailError):
    """Temporary provider failure: the task retries with backoff."""

    code = "email_unavailable"


class EmailRejected(EmailError):
    """The provider refused this message for good: the task gives up."""

    code = "email_rejected"


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """One plain-text message to one recipient. ``template_key`` is only for logs and tags."""

    to: str = field(repr=False)
    subject: str = field(repr=False)
    text: str = field(repr=False)
    template_key: str
    language: str


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> str:
        """Send ``message``; return the provider's message id. Raises :class:`EmailError`."""
        ...


class FakeEmailSender:
    """Local/CI provider: keeps messages in memory (tests read :attr:`sent`)."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self._lock = threading.Lock()

    def send(self, message: EmailMessage) -> str:
        with self._lock:
            self.sent.append(message)
            number = len(self.sent)
        log.info("notifications.email.faked", action=message.template_key, count=number)
        return f"fake-{number}"

    def clear(self) -> None:
        with self._lock:
            self.sent.clear()


class SesEmailSender:
    """Amazon SES v2 ``SendEmail`` (simple content, UTF-8, plain text)."""

    def __init__(self, client: Any, *, sender: str, configuration_set: str | None = None) -> None:
        self._client = client
        self._sender = sender
        self._configuration_set = configuration_set

    def send(self, message: EmailMessage) -> str:
        request: dict[str, Any] = {
            "FromEmailAddress": self._sender,
            "Destination": {"ToAddresses": [message.to]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": message.text, "Charset": "UTF-8"}},
                }
            },
            "EmailTags": [{"Name": "template", "Value": message.template_key.replace(".", "-")}],
        }
        if self._configuration_set:
            request["ConfigurationSetName"] = self._configuration_set
        try:
            response = self._client.send_email(**request)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in PERMANENT_SES_ERRORS:
                raise EmailRejected(code) from None
            raise EmailUnavailable(code or "client_error") from None
        except BotoCoreError as exc:
            raise EmailUnavailable(type(exc).__name__) from None
        return str(response.get("MessageId", ""))


def _ses_client(settings: Settings) -> Any:
    config = Config(
        retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=15
    )
    return boto3.client("sesv2", region_name=settings.aws_region, config=config)


_fake = FakeEmailSender()


def fake_sender() -> FakeEmailSender:
    """The process-wide fake (``SOS_EMAIL_PROVIDER=fake``)."""
    return _fake


@lru_cache(maxsize=1)
def _ses_sender() -> SesEmailSender:
    settings = get_settings()
    return SesEmailSender(
        _ses_client(settings),
        sender=settings.email_from or "",
        configuration_set=settings.email_ses_configuration_set,
    )


def get_sender(settings: Settings | None = None) -> EmailSender | None:
    """The configured provider, or ``None`` when email is off."""
    s = settings or get_settings()
    if s.email_provider is EmailProviderKind.FAKE:
        return _fake
    if s.email_provider is EmailProviderKind.SES:
        return _ses_sender()
    return None


# --- templates ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EmailTemplate:
    key: str
    params: frozenset[str]
    subjects: dict[str, str]
    bodies: dict[str, str]


@dataclass(frozen=True, slots=True)
class EmailContent:
    subject: str
    text: str
    language: str


def _parse(key: str, spec: Any) -> EmailTemplate:
    if not _KEY_RE.match(key):
        raise TemplateError(f"bad email template key {key!r}")
    if not isinstance(spec, dict):
        raise TemplateError(f"{key}: expected a mapping")
    params = spec.get("params", [])
    if not isinstance(params, list) or not all(
        isinstance(p, str) and _NAME_RE.match(p) for p in params
    ):
        raise TemplateError(f"{key}: params must be a list of names")
    subjects: dict[str, str] = {}
    bodies: dict[str, str] = {}
    used: dict[str, set[str]] = {}
    for lang in LANGUAGES:
        msg = spec.get(lang)
        if not isinstance(msg, dict) or set(msg) != {"subject", "body"}:
            raise TemplateError(f"{key}.{lang}: needs exactly subject and body")
        subject, body = msg["subject"], msg["body"]
        if not isinstance(subject, str) or not isinstance(body, str) or not subject or not body:
            raise TemplateError(f"{key}.{lang}: subject and body must be text")
        if "\n" in subject.strip():
            raise TemplateError(f"{key}.{lang}: the subject is one line")
        subjects[lang], bodies[lang] = subject.strip(), body.strip()
        used[lang] = placeholders(subject) | placeholders(body)
    if not used["en"] <= set(params) or not used["te"] <= set(params):
        raise TemplateError(f"{key}: undeclared placeholders")
    if used["en"] != used["te"]:
        raise TemplateError(f"{key}: en and te use different placeholders")
    return EmailTemplate(key=key, params=frozenset(params), subjects=subjects, bodies=bodies)


def _raw() -> dict[str, Any]:
    text = resources.files("app.notifications").joinpath("email_templates.yaml").read_text("utf-8")
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise TemplateError("email_templates.yaml: expected a mapping")
    return raw


@lru_cache(maxsize=1)
def email_catalog() -> dict[str, EmailTemplate]:
    items = _raw().get("templates")
    if not isinstance(items, dict) or not items:
        raise TemplateError("email_templates.yaml: 'templates' must be a non-empty mapping")
    return {key: _parse(key, spec) for key, spec in items.items()}


@lru_cache(maxsize=1)
def invitation_valid_days() -> int:
    days = (_raw().get("invitation") or {}).get("valid_days")
    if not isinstance(days, int) or days < 1:
        raise TemplateError("email_templates.yaml: invitation.valid_days must be a positive int")
    return days


@lru_cache(maxsize=1)
def resend_cooldown_s() -> int:
    seconds = (_raw().get("invitation") or {}).get("resend_cooldown_s")
    if not isinstance(seconds, int) or seconds < 1:
        raise TemplateError("email_templates.yaml: invitation.resend_cooldown_s must be positive")
    return seconds


def render_email(key: str, params: dict[str, Any], language: str) -> EmailContent:
    """Subject and plain-text body in ``language`` (``en`` or ``te``; default ``en``). Always
    English while Telugu is hidden (ADR-0036)."""
    template = email_catalog().get(key)
    if template is None:
        raise TemplateError(f"unknown email template {key!r}")
    if set(params) != template.params:
        raise TemplateError(f"{key}: params must be exactly {sorted(template.params)}")
    lang = output_language(language)
    subject = _HEADER_BREAK.sub(" ", format_message(template.subjects[lang], params)).strip()
    text = format_message(template.bodies[lang], params).strip() + "\n"
    return EmailContent(subject=subject, text=text, language=lang)


__all__ = [
    "EmailContent",
    "EmailError",
    "EmailMessage",
    "EmailRejected",
    "EmailSender",
    "EmailUnavailable",
    "FakeEmailSender",
    "SesEmailSender",
    "email_catalog",
    "fake_sender",
    "get_sender",
    "invitation_valid_days",
    "render_email",
    "resend_cooldown_s",
]
