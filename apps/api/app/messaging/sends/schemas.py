"""Pydantic v2 IO models for sending (docs/09 §4 "Messaging"; FR-MSG-012..017; R2-3G).

Inputs forbid unknown fields and NFC-normalise text. Responses carry ids, counts and reason codes
only: never a phone number, a name or a message text.
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
    StringConstraints,
    model_validator,
)

BroadcastStatus = Literal["previewed", "sending", "done"]
RunJob = Literal["absence_alerts", "fee_reminders"]
FeeMessageKind = Literal["reminder", "payment_link"]


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFC", value).split())
    return value


ResendReason = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(min_length=10, max_length=300)
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class BroadcastPreviewIn(_In):
    """Who would get the notice (FR-MSG-012): sections of the current academic year. ``resend``
    asks for a "Send again" (guardians who already got this notice are counted again; a reason
    is then required when confirming)."""

    section_ids: list[uuid.UUID] = Field(min_length=1, max_length=300)
    resend: bool = False


class BroadcastConfirmIn(_In):
    """Confirm a preview (``Idempotency-Key``). The sections must be the previewed ones; the
    preview token pins the counts shown (409 ``preview_stale`` when they changed)."""

    preview_token: str = Field(min_length=8, max_length=80)
    section_ids: list[uuid.UUID] = Field(min_length=1, max_length=300)
    resend_reason: ResendReason | None = None


class BroadcastPreviewOut(_Out):
    preview_token: str
    notice_id: uuid.UUID
    section_ids: list[uuid.UUID]
    guardians: int = Field(description="Guardians of the students in the chosen sections.")
    recipients: int = Field(description="Messages that will be queued.")
    skipped: dict[str, int] = Field(
        description="Guardians left out by reason code (no_optin, opted_out, "
        "optin_for_other_number, no_mobile_number, already_sent)."
    )
    languages: dict[str, int] = Field(description="Messages per language (en, te).")
    language_fallbacks: int = Field(
        description="Guardians who read Telugu but get English (Telugu text or template missing)."
    )
    already_sent: int
    estimated_cost_inr: str = Field(description="Rupees incl. GST, paid by the school to Meta.")
    utility_rate_inr: str
    gst_percent: str
    blocked_reason: str | None = Field(
        description="Why nothing can be sent now (messaging_not_connected, messaging_paused, "
        "template_not_approved, template_paused, messaging_currency_not_inr, notices_off); "
        "null when it can."
    )
    expires_at: dt.datetime


class SectionCountsOut(_Out):
    section_id: uuid.UUID | None
    label: str | None
    counts: dict[str, int]


class BroadcastOut(_Out):
    id: uuid.UUID
    notice_id: uuid.UUID
    notice_title: str | None
    status: BroadcastStatus
    section_ids: list[uuid.UUID]
    resend: bool
    recipients: int
    skipped: dict[str, int]
    languages: dict[str, int]
    language_fallbacks: int
    estimated_cost_inr: str
    queued: int
    counts: dict[str, int] = Field(
        description="Messages by status now (queued, sent, delivered, read, failed, skipped)."
    )
    requested_by: uuid.UUID
    confirmed_at: dt.datetime | None
    completed_at: dt.datetime | None
    created_at: dt.datetime


class BroadcastReportOut(_Out):
    broadcast: BroadcastOut
    sections: list[SectionCountsOut]
    failure_reasons: dict[str, int] = Field(description="Failed and skipped messages by reason.")


class BroadcastPage(_Out):
    items: list[BroadcastOut]
    next_cursor: str | None


class SendRunOut(_Out):
    job: RunJob
    run_on: dt.date
    started_at: dt.datetime
    finished_at: dt.datetime | None
    considered: int
    queued: int
    counts: dict[str, int]


class AutomaticOut(_Out):
    """The automatic messages: switches and times, and the recent runs (US-2205, US-2207)."""

    absence_alerts_enabled: bool
    absence_alert_time: dt.time
    fee_reminders_enabled: bool
    fee_reminder_time: dt.time
    fee_receipts_enabled: bool
    no_message_dates: list[dt.date]
    queued_now: int
    runs: list[SendRunOut]


class RevokeLinkOut(_Out):
    message_id: uuid.UUID
    revoked: bool


# --- R2-3J: manual fee reminders from the dues list (FR-MSG-025) --------------------------------


class FeeReminderSelectionIn(_In):
    """Which fee accounts get a reminder: the rows chosen on the dues list (``account_ids``,
    current year), or ``all_matching`` the dues list's filter (``section_id`` / ``class_id``,
    none = the whole school). Exactly one of the two (422 ``selection``)."""

    account_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=5000)
    all_matching: bool = False
    section_id: uuid.UUID | None = None
    class_id: uuid.UUID | None = None
    kind: FeeMessageKind = Field(
        default="reminder",
        description='``reminder`` (FR-MSG-025) or ``payment_link`` ("Send payment link", '
        'FR-MSG-027): the same fee message, whose dues page offers "Pay online"; needs online '
        "payments connected (``payments_not_connected``) and is sent at most once a day per "
        "account and guardian, apart from reminders.",
    )

    @model_validator(mode="after")
    def _one_selection(self) -> FeeReminderSelectionIn:
        if (self.account_ids is None) == (not self.all_matching):
            raise ValueError("choose rows (account_ids) or all_matching, not both or neither")
        if self.account_ids is not None and (self.section_id or self.class_id):
            raise ValueError("a filter applies only to all_matching")
        return self


class FeeRemindersConfirmIn(FeeReminderSelectionIn):
    """Confirm a preview (``Idempotency-Key``): the same selection and the preview token, which
    pins who gets a reminder (409 ``preview_stale`` when that changed)."""

    preview_token: str = Field(min_length=8, max_length=80)


class FeeReminderPreviewOut(_Out):
    preview_token: str
    accounts: int = Field(description="Fee accounts chosen.")
    accounts_reminded: int = Field(description="Accounts with at least one message to queue.")
    guardians: int = Field(description="Different guardians who will get a message.")
    recipients: int = Field(
        description="Messages that will be queued (one per account and guardian)."
    )
    skipped_accounts: dict[str, int] = Field(
        description="Accounts left out by reason code (nothing_due, below_minimum, no_guardian)."
    )
    skipped_guardians: dict[str, int] = Field(
        description="Guardians of chosen accounts left out by reason code (no_optin, opted_out, "
        "optin_for_other_number, no_mobile_number, already_sent: reminded today already)."
    )
    estimated_cost_inr: str = Field(description="Rupees incl. GST, paid by the school to Meta.")
    utility_rate_inr: str
    gst_percent: str
    blocked_reason: str | None = Field(
        description="Why nothing can be sent now (messaging_not_connected, messaging_paused, "
        "template_not_approved, template_paused, messaging_currency_not_inr; for a payment link "
        "also payments_not_connected); null when it can."
    )


class FeeRemindersOut(_Out):
    accounts: int
    recipients: int
    queued: int
    skipped_accounts: dict[str, int]
    skipped_guardians: dict[str, int]


# --- R2-3J: "Send on WhatsApp" for one receipt (FR-MSG-026) --------------------------------------


class ReceiptMessageOut(_Out):
    """One receipt message: its status now (no phone number, no text)."""

    id: uuid.UUID
    status: str
    skip_reason: str | None
    failure_reason: str | None
    language: str
    queued_at: dt.datetime
    status_at: dt.datetime


class ReceiptWhatsAppOut(_Out):
    """Whether the receipt can go on WhatsApp now and what was queued for it (FR-MSG-026).
    ``can_send``: connected, not paused, the receipt template approved, the receipt not
    cancelled and at least one guardian opted in for fee messages who has none yet."""

    receipt_id: uuid.UUID
    can_send: bool
    blocked_reason: str | None = Field(
        description="messaging_not_connected, messaging_paused, template_not_approved, "
        "template_paused, messaging_currency_not_inr, receipt_cancelled, no_opted_in_guardian "
        "or already_queued; null when it can be sent."
    )
    opted_in_guardians: int
    messages: list[ReceiptMessageOut]


__all__ = [
    "AutomaticOut",
    "BroadcastConfirmIn",
    "BroadcastOut",
    "BroadcastPage",
    "BroadcastPreviewIn",
    "BroadcastPreviewOut",
    "BroadcastReportOut",
    "BroadcastStatus",
    "FeeReminderPreviewOut",
    "FeeReminderSelectionIn",
    "FeeRemindersConfirmIn",
    "FeeRemindersOut",
    "ReceiptMessageOut",
    "ReceiptWhatsAppOut",
    "RevokeLinkOut",
    "SectionCountsOut",
    "SendRunOut",
]
