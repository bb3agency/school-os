"""Payment-link providers for SchoolOS's own invoices (FA-13; ADR-0016): the only control-plane
code that talks to Razorpay (import-linter ``platform-link-provider-egress``).

:func:`get_provider` picks the implementation from ``SOS_PLATFORM_PAYMENTS_PROVIDER_MODE``
(``fake`` in local/CI, ``live`` in staging/prod; ``fake`` is refused in staging/prod). Tests may
install a provider with :func:`set_provider`.
"""

from __future__ import annotations

import threading

from app.core.config import PaymentsProviderMode, Settings, get_settings
from app.platform.link_providers.base import (
    InvoiceLinkProvider,
    InvoiceLinkRequest,
    LinkProviderError,
    ProviderLink,
    ProviderPayment,
)
from app.platform.link_providers.fake import FakeInvoiceLinks
from app.platform.link_providers.razorpay import RazorpayInvoiceLinks

_lock = threading.Lock()
_override: InvoiceLinkProvider | None = None


def get_provider(settings: Settings | None = None) -> InvoiceLinkProvider:
    """The configured provider (a test override first)."""
    with _lock:
        if _override is not None:
            return _override
    s = settings or get_settings()
    if s.effective_platform_payments_provider_mode is PaymentsProviderMode.FAKE:
        if s.is_production_like:
            raise RuntimeError(f"the fake payment-link provider is refused in {s.env}")
        return FakeInvoiceLinks()
    return RazorpayInvoiceLinks(s)


def set_provider(provider: InvoiceLinkProvider | None) -> None:
    """Install (or with ``None`` remove) a provider for this process (tests)."""
    global _override  # noqa: PLW0603 - process-wide test hook by design
    with _lock:
        _override = provider


__all__ = [
    "InvoiceLinkProvider",
    "InvoiceLinkRequest",
    "LinkProviderError",
    "ProviderLink",
    "ProviderPayment",
    "get_provider",
    "set_provider",
]
