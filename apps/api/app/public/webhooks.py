"""Webhook signature verifiers: the registry behind ``require_webhook_signature(provider)``
(ADR-0047 decision 4, ADR-0042, ADR-0043; SEC-034, SEC-040; docs/07 §11.2).

The guard is generic; what a provider's signature looks like is a pluggable
:class:`WebhookVerifier`, registered by the module that connects that provider: Meta by wave
R2-1C (``app.messaging``), Razorpay by wave R2-2D (``app.fees``). Until a verifier is
registered, the provider's webhook routes answer 404 (fail closed). A verifier:

- receives the raw body (already within the provider's ``max_body_bytes``), the headers and the
  path parameters, and a ``resolve`` callback that maps a provider key (a Razorpay route value,
  a WABA id) to a live school handle through ``core.resolve_public_handle``;
- authenticates in constant time (``hmac.compare_digest``) and raises
  :class:`WebhookRejected` (401, no detail) or :class:`WebhookUnknownRoute` (404);
- returns the event id (for replay detection by the guard) and the handles it routed to.

:class:`FakeWebhookVerifier` is the deterministic offline verifier for tests (HMAC-SHA256 hex of
the raw body under a test secret); it is refused in staging and production (SEC-040).
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from app.core.config import Settings, get_settings
from app.public.config import WEBHOOK_PROVIDERS, HandleKind, WebhookProvider
from app.public.service import ResolvedHandle

Resolve = Callable[[HandleKind, str], ResolvedHandle | None]


class WebhookRejected(Exception):
    """Signature missing, malformed or wrong (401 without detail). ``reason`` is a log code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class WebhookUnknownRoute(Exception):
    """The route value or account id names no live handle (404)."""


@dataclass(frozen=True, slots=True)
class WebhookRequest:
    provider: WebhookProvider
    headers: Mapping[str, str]
    body: bytes
    path_params: Mapping[str, str]
    resolve: Resolve


@dataclass(frozen=True, slots=True)
class VerifiedWebhook:
    """What a verifier proved: the provider's event id (``None`` when the provider has none) and
    the live handles (schools) the delivery is for."""

    event_id: str | None
    handles: tuple[ResolvedHandle, ...] = field(default_factory=tuple)


@runtime_checkable
class WebhookVerifier(Protocol):
    provider: WebhookProvider

    def verify(self, request: WebhookRequest) -> VerifiedWebhook: ...


class RegistryError(RuntimeError):
    """A verifier was registered twice, for an unknown provider, or (fake) outside local/CI."""


_lock = threading.Lock()
_verifiers: dict[WebhookProvider, WebhookVerifier] = {}


def register_webhook_verifier(
    verifier: WebhookVerifier, *, settings: Settings | None = None
) -> None:
    """Register the verifier of one provider (once per process)."""
    s = settings or get_settings()
    if verifier.provider not in WEBHOOK_PROVIDERS:
        raise RegistryError(f"unknown webhook provider {verifier.provider!r}")
    if isinstance(verifier, FakeWebhookVerifier) and s.is_production_like:
        raise RegistryError(f"the fake webhook verifier is refused in {s.env}")
    with _lock:
        if verifier.provider in _verifiers:
            raise RegistryError(f"a {verifier.provider} webhook verifier is already registered")
        _verifiers[verifier.provider] = verifier


def unregister_webhook_verifier(provider: WebhookProvider) -> None:
    """Remove a provider's verifier (tests; a provider switched off)."""
    with _lock:
        _verifiers.pop(provider, None)


def webhook_verifier(provider: WebhookProvider) -> WebhookVerifier | None:
    with _lock:
        return _verifiers.get(provider)


FAKE_SIGNATURE_HEADER: Final = "X-Fake-Signature"
FAKE_EVENT_HEADER: Final = "X-Fake-Event-Id"


class FakeWebhookVerifier:
    """Offline verifier for tests (SEC-040: refused in staging/prod).

    Signature: ``X-Fake-Signature: hex(HMAC-SHA256(secret, raw body))``; event id from
    ``X-Fake-Event-Id``; when ``route_kind`` is set, the path parameter ``route_param`` must
    resolve to a live handle of that kind (else :class:`WebhookUnknownRoute`), checked before
    the signature like Razorpay's per-school secret lookup."""

    def __init__(
        self,
        provider: WebhookProvider,
        secret: bytes,
        *,
        route_kind: HandleKind | None = None,
        route_param: str = "route",
    ) -> None:
        self.provider: WebhookProvider = provider
        self._secret = secret
        self._route_kind = route_kind
        self._route_param = route_param

    def sign(self, body: bytes) -> str:
        return hmac.new(self._secret, body, hashlib.sha256).hexdigest()

    def verify(self, request: WebhookRequest) -> VerifiedWebhook:
        handles: tuple[ResolvedHandle, ...] = ()
        if self._route_kind is not None:
            found = request.resolve(
                self._route_kind, request.path_params.get(self._route_param, "")
            )
            if found is None:
                raise WebhookUnknownRoute()
            handles = (found,)
        given = request.headers.get(FAKE_SIGNATURE_HEADER.lower()) or request.headers.get(
            FAKE_SIGNATURE_HEADER
        )
        if not given:
            raise WebhookRejected("signature_missing")
        if not hmac.compare_digest(
            given.strip().lower().encode(), self.sign(request.body).encode()
        ):
            raise WebhookRejected("signature_mismatch")
        event = request.headers.get(FAKE_EVENT_HEADER.lower()) or request.headers.get(
            FAKE_EVENT_HEADER
        )
        return VerifiedWebhook(event_id=event or None, handles=handles)


__all__ = [
    "FAKE_EVENT_HEADER",
    "FAKE_SIGNATURE_HEADER",
    "FakeWebhookVerifier",
    "RegistryError",
    "Resolve",
    "VerifiedWebhook",
    "WebhookRejected",
    "WebhookRequest",
    "WebhookUnknownRoute",
    "WebhookVerifier",
    "register_webhook_verifier",
    "unregister_webhook_verifier",
    "webhook_verifier",
]
