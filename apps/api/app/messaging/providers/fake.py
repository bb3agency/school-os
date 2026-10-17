"""Deterministic offline messaging provider for local development and CI (FR-MSG-001; refused in
staging and production by the settings guard and :func:`app.messaging.providers.get_provider`).

It behaves like the Meta Cloud API closely enough for the service and the tests: Embedded Signup
codes become tokens (a code starting with ``bad`` fails), every token except ones starting with
``bad`` is accepted, templates start ``PENDING`` and the next list call reports them
``APPROVED`` (unless a test set another status), sends return ``wamid.FAKE…`` ids. Everything
it was asked to do is kept in :data:`STATE` (per process) so tests can inspect it; numbers and
texts given to it are synthetic in tests (invariant 11). No network.
"""

from __future__ import annotations

import hashlib
import itertools
import threading
from dataclasses import dataclass, field
from typing import Any

from app.messaging.providers.base import (
    AccountInfo,
    PhoneNumberInfo,
    ProviderError,
    SendResult,
    TemplateInfo,
    TemplateMessage,
)


@dataclass
class FakeState:
    """What the fake provider was asked to do (tests read and set it)."""

    templates: dict[tuple[str, str, str], TemplateInfo] = field(default_factory=dict)
    sent: list[dict[str, Any]] = field(default_factory=list)
    registered: list[str] = field(default_factory=list)
    subscribed: list[str] = field(default_factory=list)
    quality: dict[str, str] = field(default_factory=dict)
    limit: dict[str, str] = field(default_factory=dict)
    currency: dict[str, str] = field(default_factory=dict)
    # Template status forced by a test: (waba_id, name, language) -> (status, category)
    forced: dict[tuple[str, str, str], tuple[str, str]] = field(default_factory=dict)
    # Recipients whose sends fail with this Meta code (permanent or transient per config).
    failing: dict[str, int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self) -> None:
        with self.lock:
            self.templates.clear()
            self.sent.clear()
            self.registered.clear()
            self.subscribed.clear()
            self.quality.clear()
            self.limit.clear()
            self.currency.clear()
            self.forced.clear()
            self.failing.clear()


STATE = FakeState()
_ids = itertools.count(1)


def _check(token: str) -> None:
    if not token or token.startswith("bad"):
        raise ProviderError("auth_failed", meta_code=190)


class FakeMessagingProvider:
    """``MessagingProvider`` that never leaves the process."""

    name = "fake"

    def __init__(self, state: FakeState | None = None) -> None:
        self.state = state or STATE

    def exchange_code(self, code: str) -> str:
        if not code or code.startswith("bad"):
            raise ProviderError("auth_failed", meta_code=100)
        return "fake-token-" + hashlib.sha256(code.encode()).hexdigest()[:24]

    def register_number(self, token: str, phone_number_id: str, pin: str) -> None:
        _check(token)
        if not (pin.isdigit() and len(pin) == 6):
            raise ProviderError("invalid_request", meta_code=100)
        with self.state.lock:
            self.state.registered.append(phone_number_id)

    def subscribe_app(self, token: str, waba_id: str) -> None:
        _check(token)
        with self.state.lock:
            self.state.subscribed.append(waba_id)

    def phone_number(self, token: str, phone_number_id: str) -> PhoneNumberInfo:
        _check(token)
        return PhoneNumberInfo(
            phone_number_id=phone_number_id,
            display_phone="+91 90000 0" + phone_number_id[-4:].rjust(4, "0"),
            display_name="Synthetic Model School",
            quality_rating=self.state.quality.get(phone_number_id, "GREEN"),
            messaging_limit=self.state.limit.get(phone_number_id, "TIER_250"),
            status="CONNECTED",
        )

    def account(self, token: str, waba_id: str) -> AccountInfo:
        _check(token)
        return AccountInfo(
            waba_id=waba_id,
            name="Synthetic Model School",
            currency=self.state.currency.get(waba_id, "INR"),
            review_status="APPROVED",
        )

    def create_template(
        self, token: str, waba_id: str, *, name: str, language: str, components: list[Any]
    ) -> TemplateInfo:
        _check(token)
        if not components:
            raise ProviderError("invalid_request", meta_code=100)
        info = TemplateInfo(
            name=name,
            language=language,
            status="PENDING",
            category="UTILITY",
            template_id=f"fake-tpl-{next(_ids)}",
        )
        with self.state.lock:
            self.state.templates[(waba_id, name, language)] = info
        return info

    def list_templates(self, token: str, waba_id: str) -> list[TemplateInfo]:
        _check(token)
        out: list[TemplateInfo] = []
        with self.state.lock:
            for (waba, name, language), info in sorted(self.state.templates.items()):
                if waba != waba_id:
                    continue
                status, category = self.state.forced.get(
                    (waba, name, language), ("APPROVED", "UTILITY")
                )
                out.append(
                    TemplateInfo(
                        name=name,
                        language=language,
                        status=status,
                        category=category,
                        template_id=info.template_id,
                        rejected_reason="INVALID_FORMAT" if status == "REJECTED" else None,
                    )
                )
        return out

    def _send(self, token: str, to: str, record: dict[str, Any]) -> SendResult:
        _check(token)
        if not (to.isdigit() and len(to) == 12 and to.startswith("91")):
            raise ProviderError("invalid_request", meta_code=131009)
        code = self.state.failing.get(to)
        if code is not None:
            raise ProviderError("meta_error", meta_code=code, transient=code in (130429, 131000))
        message_id = f"wamid.FAKE{next(_ids):010d}"
        with self.state.lock:
            self.state.sent.append({**record, "to": to, "id": message_id})
        return SendResult(provider_message_id=message_id)

    def send_template(
        self, token: str, phone_number_id: str, to: str, message: TemplateMessage
    ) -> SendResult:
        return self._send(
            token,
            to,
            {
                "kind": "template",
                "from": phone_number_id,
                "name": message.name,
                "language": message.language,
                "values": list(message.body_values),
                "url_suffix": message.url_suffix,
            },
        )

    def send_text(self, token: str, phone_number_id: str, to: str, body: str) -> SendResult:
        return self._send(token, to, {"kind": "text", "from": phone_number_id, "body": body})


__all__ = ["STATE", "FakeMessagingProvider", "FakeState"]
