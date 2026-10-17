"""Pydantic v2 IO models for WhatsApp messaging (docs/09 §4 "Messaging"; ADR-0043; FR-MSG-*).

Inputs forbid unknown fields and NFC-normalise text. Responses never carry a guardian's phone
number (only its last four digits, FR-MSG-017), a token, a PIN or a message text.
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)

from app.messaging.config import Language, OptinCategory

OptinStatus = Literal["opted_in", "opted_out"]
CurrentOptinStatus = Literal["opted_in", "opted_out", "not_asked"]
OptinMethod = Literal[
    "whatsapp_join", "signed_form", "in_person", "whatsapp_stop", "staff_recorded_stop"
]
StaffMethod = Literal["signed_form", "in_person", "staff_recorded_stop"]
AccountStatus = Literal["connecting", "active", "paused", "disconnected"]
CurrencyState = Literal["confirmed_inr", "not_inr", "unknown"]
TemplateStatus = Literal["not_created", "pending", "approved", "rejected", "paused", "disabled"]
MessageStatus = Literal["queued", "skipped", "sent", "delivered", "read", "failed"]
MessagePurpose = Literal[
    "optin_confirm",
    "school_notice",
    "absence_alert",
    "fee_reminder",
    "fee_receipt",
    "optin_stopped",
]


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


MetaId = Annotated[str, BeforeValidator(_nfc), StringConstraints(pattern=r"^[0-9]{1,32}$")]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


# --- account ----------------------------------------------------------------------------------


class EmbeddedSignupIn(_In):
    """What Meta's Embedded Signup window returned to the settings page (FR-MSG-002)."""

    code: Annotated[
        str, StringConstraints(min_length=8, max_length=1024, pattern=r"^[\x21-\x7e]+$")
    ]
    waba_id: MetaId
    phone_number_id: MetaId
    business_id: MetaId | None = None


class ManualConnectIn(_In):
    """Pilots until SchoolOS is a Meta Tech Provider (D13; FR-MSG-003): a system-user token
    created in the school's own Business Manager, its WABA id and phone number id."""

    token: SecretStr = Field(description="Never returned, logged or exported.")
    waba_id: MetaId
    phone_number_id: MetaId

    @model_validator(mode="after")
    def _token_shape(self) -> ManualConnectIn:
        value = self.token.get_secret_value()
        if not 20 <= len(value) <= 1024 or not all(0x21 <= ord(c) <= 0x7E for c in value):
            raise ValueError("token must be the access token text")
        return self


class MessagingTemplateOut(_Out):
    template_key: str
    template_version: int
    language: Language
    provider_name: str
    status: TemplateStatus
    provider_category: str | None
    paused_by_sos: bool
    rejected_reason: str | None
    reviewed: bool
    usable: bool
    last_synced_at: dt.datetime | None


class MessagingAccountOut(_Out):
    """The school's WhatsApp connection (FR-MSG-004). ``connected`` false: nothing below is set."""

    connected: bool
    available: bool = True
    manual_token_allowed: bool
    embedded_signup: dict[str, str] | None = Field(
        default=None, description="Public Embedded Signup values (app id, configuration id)."
    )
    id: uuid.UUID | None = None
    mode: Literal["embedded_signup", "manual_token"] | None = None
    status: AccountStatus | None = None
    pause_reason: str | None = None
    waba_id: str | None = None
    phone_number_id: str | None = None
    display_phone: str | None = None
    display_name: str | None = None
    quality_rating: str | None = None
    messaging_limit: str | None = None
    messaging_limit_count: int | None = None
    default_daily_cap: int | None = None
    currency: str | None = None
    currency_state: CurrencyState | None = None
    inr_required_from: dt.date
    connected_at: dt.datetime | None = None
    paused_at: dt.datetime | None = None
    last_synced_at: dt.datetime | None = None
    templates: list[MessagingTemplateOut] = Field(default_factory=list)
    unreviewed_texts: list[str] = Field(default_factory=list)
    version: int = 0


class MessagingTemplatesOut(_Out):
    items: list[MessagingTemplateOut]


# --- settings ---------------------------------------------------------------------------------


OfficePhone = Annotated[str, BeforeValidator(_nfc), StringConstraints(min_length=10, max_length=20)]


class MessagingSettingsIn(_In):
    """Switches and times (ADR-0043 decisions 10-11, owner decision D25). The switches are read
    by the sending wave (R2-3G); absence alerts stay off until the school turns them on."""

    notices_enabled: bool
    absence_alerts_enabled: bool
    fee_reminders_enabled: bool
    fee_receipts_enabled: bool
    absence_alert_time: dt.time
    quiet_start: dt.time
    quiet_end: dt.time
    daily_cap: int | None = Field(default=None, ge=1, le=1_000_000)
    office_phone: OfficePhone | None = Field(
        default=None, description="The school office's phone, given in automatic replies."
    )


class MessagingSettingsOut(_Out):
    notices_enabled: bool
    absence_alerts_enabled: bool
    fee_reminders_enabled: bool
    fee_receipts_enabled: bool
    absence_alert_time: dt.time
    quiet_start: dt.time
    quiet_end: dt.time
    daily_cap: int | None
    default_daily_cap: int | None
    office_phone: str | None
    utility_rate_inr: str
    gst_percent: str
    version: int


# --- opt-ins ----------------------------------------------------------------------------------


class OptinIn(_In):
    """A decision recorded by staff (FR-MSG-008). ``signed_form`` needs the signed form as an
    evidence document; ``in_person`` names the recorder (the caller); ``staff_recorded_stop``
    records a parent's request to stop."""

    category: OptinCategory
    status: OptinStatus
    method: StaffMethod
    decided_on: dt.date
    evidence_document_id: uuid.UUID | None = None


class OptinEntryOut(_Out):
    id: uuid.UUID
    category: OptinCategory
    seq: int
    status: OptinStatus
    method: OptinMethod
    wording_version: str
    decided_on: dt.date
    evidence_document_id: uuid.UUID | None
    recorded_by_name: str | None
    recorded_at: dt.datetime


class CategoryStateOut(_Out):
    category: OptinCategory
    status: CurrentOptinStatus
    method: OptinMethod | None
    decided_on: dt.date | None
    stopped_by_parent: bool
    for_current_number: bool = Field(
        description="False when the guardian's number changed after the opt-in (a new opt-in "
        "is needed) or there is no number."
    )


class GuardianOptinsOut(_Out):
    id: uuid.UUID = Field(description="The guardian's id (same as guardian_id).")
    guardian_id: uuid.UUID
    has_mobile: bool
    phone_last4: str | None
    preferred_language: Language | None
    effective_language: Language
    categories: list[CategoryStateOut]
    history: list[OptinEntryOut]
    version: int


class JoinSlipsIn(_In):
    """Students or one section of the current year; the categories the slip opts into."""

    student_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=400)
    section_id: uuid.UUID | None = None
    categories: list[OptinCategory] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _one_audience(self) -> JoinSlipsIn:
        if (self.student_ids is None) == (self.section_id is None):
            raise ValueError("give student_ids or section_id")
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("categories repeat")
        return self


class CategoryCounts(_Out):
    opted_in: int
    opted_out: int
    not_asked: int


class SectionOptinSummaryOut(_Out):
    section_id: uuid.UUID
    students: int
    guardians: int
    guardians_without_mobile: int
    categories: dict[OptinCategory, CategoryCounts]


# --- messages ---------------------------------------------------------------------------------


class MessagingSearchIn(_In):
    """Delivery log filters (POST: no personal data in URLs). Scoped callers (class teachers)
    must name a student or guardian in their sections."""

    student_id: uuid.UUID | None = None
    guardian_id: uuid.UUID | None = None
    purpose: MessagePurpose | None = None
    status: MessageStatus | None = None
    since: dt.date | None = None
    until: dt.date | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=512)


class MessagingMessageOut(_Out):
    id: uuid.UUID
    purpose: MessagePurpose
    kind: Literal["template", "text"]
    template_key: str | None
    template_version: int | None
    language: Language
    language_fallback: bool
    guardian_id: uuid.UUID | None
    student_id: uuid.UUID | None
    source_type: str
    source_id: uuid.UUID
    phone_last4: str | None
    status: MessageStatus
    skip_reason: str | None
    error_code: str | None
    error_reason: str | None
    attempts: int
    queued_at: dt.datetime
    sent_at: dt.datetime | None
    delivered_at: dt.datetime | None
    read_at: dt.datetime | None
    failed_at: dt.datetime | None


class MessagingMessagePage(_Out):
    items: list[MessagingMessageOut]
    next_cursor: str | None


__all__ = [
    "CategoryCounts",
    "CategoryStateOut",
    "CurrencyState",
    "EmbeddedSignupIn",
    "GuardianOptinsOut",
    "JoinSlipsIn",
    "ManualConnectIn",
    "MessagingAccountOut",
    "MessagingMessageOut",
    "MessagingMessagePage",
    "MessagingSearchIn",
    "MessagingSettingsIn",
    "MessagingSettingsOut",
    "MessagingTemplateOut",
    "MessagingTemplatesOut",
    "OptinEntryOut",
    "OptinIn",
    "SectionOptinSummaryOut",
]
