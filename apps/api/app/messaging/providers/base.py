"""The messaging provider interface (ADR-0043 decision 1; FR-MSG-001).

A provider talks to one WhatsApp Business account on behalf of one school, with that school's
business token. Implementations: :mod:`.meta_cloud` (the Meta Cloud API over httpx, no SDK) and
:mod:`.fake` (deterministic and offline; local and CI only, refused in staging/prod). Only this
package calls Meta (import-linter contract ``messaging-provider-egress``).

Errors are :class:`ProviderError` with a short code and, for Meta errors, Meta's numeric code;
``transient`` tells the caller whether a retry may help. Nothing here logs a token, a phone
number or a message.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class ProviderError(Exception):
    """A provider call failed. ``code`` is a short reason code (``auth_failed``,
    ``rate_limited``, ``invalid_request``, ``not_found``, ``unavailable``, ``meta_error``);
    ``meta_code`` Meta's numeric error code when there is one."""

    def __init__(self, code: str, *, meta_code: int | None = None, transient: bool = False) -> None:
        super().__init__(code if meta_code is None else f"{code}:{meta_code}")
        self.code = code
        self.meta_code = meta_code
        self.transient = transient


@dataclass(frozen=True, slots=True)
class PhoneNumberInfo:
    """What the account page shows about the school's number (FR-MSG-004)."""

    phone_number_id: str
    display_phone: str | None
    display_name: str | None
    quality_rating: str | None
    messaging_limit: str | None
    status: str | None


@dataclass(frozen=True, slots=True)
class AccountInfo:
    waba_id: str
    name: str | None
    currency: str | None
    review_status: str | None


@dataclass(frozen=True, slots=True)
class TemplateInfo:
    name: str
    language: str
    status: str
    category: str | None
    template_id: str | None
    rejected_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SendResult:
    provider_message_id: str


@dataclass(frozen=True, slots=True)
class TemplateMessage:
    """A template send: the catalogue template's provider name and language, its positional
    body values and (for a URL button) the parent link token as the dynamic URL suffix."""

    name: str
    language: str
    body_values: Sequence[str]
    url_suffix: str | None = None
    url_button_index: int | None = None
    stop_button_index: int | None = None
    stop_payload: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MessagingProvider(Protocol):
    """One school's view of its WhatsApp Business account (all calls with that school's token)."""

    name: str

    def exchange_code(self, code: str) -> str:
        """Embedded Signup: the business token for the code the signup window returned."""
        ...

    def register_number(self, token: str, phone_number_id: str, pin: str) -> None: ...

    def subscribe_app(self, token: str, waba_id: str) -> None: ...

    def phone_number(self, token: str, phone_number_id: str) -> PhoneNumberInfo: ...

    def account(self, token: str, waba_id: str) -> AccountInfo: ...

    def create_template(
        self, token: str, waba_id: str, *, name: str, language: str, components: list[Any]
    ) -> TemplateInfo: ...

    def list_templates(self, token: str, waba_id: str) -> list[TemplateInfo]: ...

    def send_template(
        self, token: str, phone_number_id: str, to: str, message: TemplateMessage
    ) -> SendResult: ...

    def send_text(self, token: str, phone_number_id: str, to: str, body: str) -> SendResult:
        """Free-form text, only inside the 24-hour window the recipient opened."""
        ...


__all__ = [
    "AccountInfo",
    "MessagingProvider",
    "PhoneNumberInfo",
    "ProviderError",
    "SendResult",
    "TemplateInfo",
    "TemplateMessage",
]
