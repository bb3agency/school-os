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
    include_earlier_years: bool = Field(
        default=False,
        description="Also remind about earlier-year dues (FM-01, FR-FEE-035; manual sends only): "
        "one message per student with the total of all its open accounts. Without it an "
        "earlier-year row answers 422 ``account_ids`` (``earlier_year``).",
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


# --- FP-01: opt-in coverage and reach follow-up (FR-MSG-039..046) -------------------------------

OptinCategoryName = Literal["notices", "attendance", "fees"]
FollowUpOutcome = Literal["not_sent", "failed", "not_opened"]


class SectionCoverageOut(_Out):
    """One section's coverage for a category: students with at least one guardian who can get
    WhatsApp messages (opted in, for the guardian's current mobile number)."""

    section_id: uuid.UUID
    label: str
    students: int
    students_reached: int
    guardians: int
    guardians_opted_in: int
    guardians_without_mobile: int


class CoverageOut(_Out):
    """Opt-in coverage per section of the current year (FR-MSG-039, FR-MSG-040; counts only; a
    class teacher sees their sections)."""

    category: OptinCategoryName
    students: int
    students_reached: int
    guardians: int
    guardians_opted_in: int
    sections: list[SectionCoverageOut]


class GuardianReachOut(_Out):
    """One guardian of a listed student: relation and why no message reaches them, as a code
    (no_optin, opted_out, no_mobile_number, optin_for_other_number; in a broadcast follow-up
    also the message's status or skip/failure reason). Never a phone number."""

    guardian_id: uuid.UUID
    relationship: str
    is_primary: bool
    reason: str


class NotReachedStudentOut(_Out):
    student_id: uuid.UUID
    name: str | None = Field(description="Null when the reader may not see student names.")
    class_section: str | None
    guardians: list[GuardianReachOut]


class NotReachedOut(_Out):
    """A section's students whom no WhatsApp message of the category reaches (FR-MSG-041,
    FR-MSG-042): the follow-up list for the class teacher and the office. Join slips for them
    are printed with ``POST /messaging/join-slips`` (``student_ids``; FR-MSG-043)."""

    section_id: uuid.UUID
    label: str
    category: OptinCategoryName
    students: int
    items: list[NotReachedStudentOut]
    truncated: bool


class FollowUpStudentOut(_Out):
    student_id: uuid.UUID
    name: str | None
    class_section: str | None
    outcome: FollowUpOutcome
    guardians: list[GuardianReachOut]


class BroadcastFollowUpOut(_Out):
    """Students of a broadcast's sections the notice did not reach (FR-MSG-044, FR-MSG-045):
    ``not_sent`` (no message to any guardian), ``failed`` (every message failed or was
    skipped) or ``not_opened`` (sent ``not_opened_hours`` ago, neither read on WhatsApp nor
    opened on the parent page). A list to act on, never an automatic resend."""

    broadcast_id: uuid.UUID
    notice_id: uuid.UUID
    not_opened_hours: int
    outcomes: dict[str, int] = Field(
        description="Students by outcome (opened, pending, not_sent, failed, not_opened)."
    )
    items: list[FollowUpStudentOut]
    truncated: bool


# --- FP-02: urgent closure message (FR-MSG-047..055) --------------------------------------------

ClosureReasonCode = Literal["weather", "government_order", "local_situation", "other"]


class ClosureIn(_In):
    """An urgent closure message: the whole school or chosen sections of the current year, the
    closure dates (``closed_to`` defaults to ``closed_from``) and a reason from the list."""

    whole_school: bool = True
    section_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=300)
    closed_from: dt.date
    closed_to: dt.date | None = None
    reason: ClosureReasonCode

    @model_validator(mode="after")
    def _audience(self) -> ClosureIn:
        if self.whole_school == (self.section_ids is not None):
            raise ValueError("choose the whole school or section_ids, not both or neither")
        return self


class ClosureConfirmIn(ClosureIn):
    """Send the previewed closure message (``Idempotency-Key``; step-up). The preview token pins
    who gets it (409 ``preview_stale`` when that changed)."""

    preview_token: str = Field(min_length=8, max_length=80)


class ClosurePreviewOut(_Out):
    preview_token: str
    guardians: int
    recipients: int
    skipped: dict[str, int] = Field(
        description="Guardians left out by reason (no_optin, opted_out, optin_for_other_number, "
        "no_mobile_number)."
    )
    languages: dict[str, int]
    language_fallbacks: int
    estimated_cost_inr: str
    utility_rate_inr: str
    gst_percent: str
    message_en: str = Field(description="The English message parents will get.")
    sends_at: dt.datetime | None = Field(
        description="When sending starts: null = now; otherwise the next opening of the closure "
        "window (06:00 IST by default)."
    )
    window_start: dt.time
    window_end: dt.time
    blocked_reason: str | None
    recent_closures: int = Field(description="Closure messages sent in the last uses_window_days.")


class ClosureOut(_Out):
    id: uuid.UUID
    whole_school: bool
    section_ids: list[uuid.UUID]
    closed_from: dt.date
    closed_to: dt.date
    reason: str
    recipients: int
    queued: int
    skipped: dict[str, int]
    languages: dict[str, int]
    estimated_cost_inr: str
    counts: dict[str, int] = Field(description="Messages by status now.")
    sent_by: uuid.UUID
    sent_at: dt.datetime


class ClosurePage(_Out):
    """Recent closure messages (the principal's count of uses, FR-MSG-054)."""

    items: list[ClosureOut]
    uses: int
    uses_window_days: int
    window_start: dt.time
    window_end: dt.time
    reasons: list[str]


__all__ = [
    "AutomaticOut",
    "BroadcastConfirmIn",
    "BroadcastFollowUpOut",
    "BroadcastOut",
    "BroadcastPage",
    "BroadcastPreviewIn",
    "BroadcastPreviewOut",
    "BroadcastReportOut",
    "BroadcastStatus",
    "ClosureConfirmIn",
    "ClosureIn",
    "ClosureOut",
    "ClosurePage",
    "ClosurePreviewOut",
    "CoverageOut",
    "FeeReminderPreviewOut",
    "FeeReminderSelectionIn",
    "FeeRemindersConfirmIn",
    "FeeRemindersOut",
    "FollowUpStudentOut",
    "GuardianReachOut",
    "NotReachedOut",
    "NotReachedStudentOut",
    "ReceiptMessageOut",
    "ReceiptWhatsAppOut",
    "RevokeLinkOut",
    "SectionCountsOut",
    "SectionCoverageOut",
    "SendRunOut",
]
