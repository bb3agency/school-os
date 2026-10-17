"""Payment providers (ADR-0042 decision 3; FR-PAY-001): the only code that talks to Razorpay.

:func:`get_provider` picks the implementation from ``SOS_PAYMENTS_PROVIDER_MODE`` (``fake`` in
local/CI, ``live`` in staging/prod; ``fake`` is refused in staging/prod). Tests may install a
provider with :func:`set_provider`.
"""

from __future__ import annotations

import threading

from app.core.config import PaymentsProviderMode, Settings
from app.fees.payments_runtime import settings as payments_settings
from app.fees.providers.base import (
    Credentials,
    LinkRequest,
    Order,
    Payment,
    PaymentLink,
    PaymentProvider,
    ProviderError,
    Refund,
    SettlementItem,
    Tokens,
)
from app.fees.providers.fake import FakePaymentProvider
from app.fees.providers.razorpay import RazorpayProvider

_lock = threading.Lock()
_override: PaymentProvider | None = None


def get_provider(settings: Settings | None = None) -> PaymentProvider:
    """The configured provider (a test override first)."""
    with _lock:
        if _override is not None:
            return _override
    s = settings or payments_settings()
    if s.effective_payments_provider_mode is PaymentsProviderMode.FAKE:
        if s.is_production_like:
            raise RuntimeError(f"the fake payment provider is refused in {s.env}")
        return FakePaymentProvider()
    return RazorpayProvider(s)


def set_provider(provider: PaymentProvider | None) -> None:
    """Install (or with ``None`` remove) a provider for this process (tests)."""
    global _override  # noqa: PLW0603 - process-wide test hook by design
    with _lock:
        _override = provider


__all__ = [
    "Credentials",
    "LinkRequest",
    "Order",
    "Payment",
    "PaymentLink",
    "PaymentProvider",
    "ProviderError",
    "Refund",
    "SettlementItem",
    "Tokens",
    "get_provider",
    "set_provider",
]
