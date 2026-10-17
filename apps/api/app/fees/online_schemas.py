"""Pydantic v2 IO models for online payments (docs/09 "Online payments"; FR-PAY-*; ADR-0042).

Inputs forbid unknown fields. Money is exact ``Decimal`` rupees (strings out). Secrets go in once
(manual key secret) and come out once (the webhook secret right after connecting or rotating);
no other response ever carries a secret, a token, a signature or a payer detail (SEC-035).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from app.fees.schemas import InstalmentNo, Money

ConnectionMode = Literal["oauth", "manual_keys"]
ConnectionStatus = Literal["active", "revoked", "disconnected"]
IntentStatus = Literal["created", "paid", "expired", "cancelled", "failed"]
IntentVia = Literal["staff", "parent_page", "reminder"]
ItemKind = Literal[
    "paid_not_receipted",
    "receipted_not_captured",
    "amount_mismatch",
    "refunded",
    "refund_failed",
    "settlement_missing",
    "unknown_intent",
]
ItemStatus = Literal["open", "explained", "resolved"]

KeyId = Annotated[str, StringConstraints(pattern=r"^rzp_(test|live)_[A-Za-z0-9]{6,30}$")]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# --- connection ---------------------------------------------------------------------------------


class ManualKeysIn(_In):
    """The key id and key secret from the school's own Razorpay dashboard (pilots, D13). The
    secret is checked with a read call, stored wrapped and never returned."""

    key_id: KeyId
    key_secret: SecretStr = Field(min_length=8, max_length=100)


class OAuthStartOut(_Out):
    authorize_url: str


class OAuthCallbackIn(_In):
    code: Annotated[str, StringConstraints(min_length=4, max_length=512, pattern=r"^[!-~]+$")]
    state: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{20,100}$")]


class WebhookSetupOut(_Out):
    """Shown once after connecting with manual keys or rotating the secret: the school adds this
    webhook (URL, secret, events) in its own Razorpay dashboard."""

    url: str
    secret: str
    events: list[str]
    previous_valid_until: dt.datetime | None = None


class PaymentConnectionOut(_Out):
    """The school's connection, never its secrets (FR-PAY-002/003)."""

    connected: bool
    provider: str = "razorpay"
    manual_keys_allowed: bool
    oauth_available: bool
    id: uuid.UUID | None = None
    mode: ConnectionMode | None = None
    environment: Literal["test", "live"] | None = None
    status: ConnectionStatus | None = None
    provider_account_id: str | None = None
    key_id_hint: str | None = None
    webhook_url: str | None = None
    webhook_events: list[str] = Field(default_factory=list)
    connected_at: dt.datetime | None = None
    connected_by_name: str | None = None
    access_expires_at: dt.datetime | None = None
    refresh_expires_at: dt.datetime | None = None
    secret_rotated_at: dt.datetime | None = None
    previous_valid_until: dt.datetime | None = None
    last_checked_at: dt.datetime | None = None
    last_error_code: str | None = None
    revoked_at: dt.datetime | None = None
    open_links: int = 0
    pending_events: int = 0
    failed_events: int = 0
    version: int | None = None


class ManualConnectOut(_Out):
    connection: PaymentConnectionOut
    webhook: WebhookSetupOut


class ConnectionTestOut(_Out):
    ok: bool
    error_code: str | None = None
    checked_at: dt.datetime


# --- intents ------------------------------------------------------------------------------------


class PaymentIntentCreate(_In):
    """A payment link for chosen instalments of one account (FR-PAY-005): the amount is what is
    still due on them now, never typed."""

    instalments: list[InstalmentNo] = Field(min_length=1, max_length=24)
    expires_in_days: int | None = Field(default=None, ge=1, le=180)


class PaymentIntentOut(_Out):
    id: uuid.UUID
    account_id: uuid.UUID
    intent_code: str
    amount: Money
    instalments: list[int]
    status: IntentStatus
    short_url: str
    expires_at: dt.datetime
    created_via: IntentVia
    created_by: uuid.UUID | None
    created_by_name: str | None
    created_at: dt.datetime
    paid_at: dt.datetime | None
    receipt_id: uuid.UUID | None
    receipt_number: str | None
    cancelled_at: dt.datetime | None
    reused: bool = False
    version: int


class PaymentLinkInfo(_Out):
    """What a parent page (R2-3H) needs to redirect to the provider's hosted page."""

    intent_id: uuid.UUID
    short_url: str
    expires_at: dt.datetime
    status: IntentStatus
    amount: Money


# --- reconciliation -----------------------------------------------------------------------------


class ReconciliationItemOut(_Out):
    id: uuid.UUID
    kind: ItemKind
    reason_code: str
    status: ItemStatus
    provider_payment_id: str | None
    provider_refund_id: str | None
    intent_id: uuid.UUID | None
    intent_code: str | None
    receipt_id: uuid.UUID | None
    receipt_number: str | None
    refund_id: uuid.UUID | None
    account_id: uuid.UUID | None
    amount: Money | None
    expected_amount: Money | None
    note: str | None
    explained_by: uuid.UUID | None
    explained_by_name: str | None
    explained_at: dt.datetime | None
    resolved_at: dt.datetime | None
    created_at: dt.datetime
    run_date: dt.date | None
    version: int


class ReconciliationPage(_Out):
    items: list[ReconciliationItemOut]
    next_cursor: str | None
    open_count: int


class ReconciliationRunOut(_Out):
    run_date: dt.date
    status: Literal["running", "done", "failed"]
    payments_seen: int
    exceptions_opened: int
    error_code: str | None
    finished_at: dt.datetime | None


class ReconciliationSummaryOut(_Out):
    connected: bool
    open_count: int
    pending_events: int
    failed_events: int
    runs: list[ReconciliationRunOut]


class ExplainIn(_In):
    note: Annotated[str, StringConstraints(max_length=500)]


__all__ = [
    "ConnectionTestOut",
    "ExplainIn",
    "ManualConnectOut",
    "ManualKeysIn",
    "OAuthCallbackIn",
    "OAuthStartOut",
    "PaymentConnectionOut",
    "PaymentIntentCreate",
    "PaymentIntentOut",
    "PaymentLinkInfo",
    "ReconciliationItemOut",
    "ReconciliationPage",
    "ReconciliationRunOut",
    "ReconciliationSummaryOut",
    "WebhookSetupOut",
]
