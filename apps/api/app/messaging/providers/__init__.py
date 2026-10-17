"""Messaging providers (ADR-0043 decision 1; FR-MSG-001): the only code that talks to Meta.

:func:`get_provider` picks the implementation from ``SOS_MESSAGING_PROVIDER_MODE`` (``fake`` in
local/CI, ``live`` in staging/prod; ``fake`` is refused in staging/prod). Tests may install a
provider with :func:`set_provider`.
"""

from __future__ import annotations

import threading

from app.core.config import MessagingProviderMode, Settings, get_settings
from app.messaging.providers.base import (
    AccountInfo,
    MessagingProvider,
    PhoneNumberInfo,
    ProviderError,
    SendResult,
    TemplateInfo,
    TemplateMessage,
)
from app.messaging.providers.fake import FakeMessagingProvider
from app.messaging.providers.meta_cloud import MetaCloudProvider

_lock = threading.Lock()
_override: MessagingProvider | None = None


def get_provider(settings: Settings | None = None) -> MessagingProvider:
    """The configured provider (a test override first)."""
    with _lock:
        if _override is not None:
            return _override
    s = settings or get_settings()
    if s.effective_messaging_provider_mode is MessagingProviderMode.FAKE:
        if s.is_production_like:
            raise RuntimeError(f"the fake messaging provider is refused in {s.env}")
        return FakeMessagingProvider()
    return MetaCloudProvider(s)


def set_provider(provider: MessagingProvider | None) -> None:
    """Install (or with ``None`` remove) a provider for this process (tests)."""
    global _override  # noqa: PLW0603 - process-wide test hook by design
    with _lock:
        _override = provider


__all__ = [
    "AccountInfo",
    "MessagingProvider",
    "PhoneNumberInfo",
    "ProviderError",
    "SendResult",
    "TemplateInfo",
    "TemplateMessage",
    "get_provider",
    "set_provider",
]
