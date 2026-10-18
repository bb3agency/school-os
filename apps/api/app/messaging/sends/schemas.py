"""Pydantic v2 IO models for sending (docs/09 §4 "Messaging"; FR-MSG-012..017; R2-3G).

Inputs forbid unknown fields and NFC-normalise text. Responses carry ids, counts and reason codes
only: never a phone number, a name or a message text.
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

BroadcastStatus = Literal["previewed", "sending", "done"]
RunJob = Literal["absence_alerts", "fee_reminders"]


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


__all__ = [
    "AutomaticOut",
    "BroadcastConfirmIn",
    "BroadcastOut",
    "BroadcastPage",
    "BroadcastPreviewIn",
    "BroadcastPreviewOut",
    "BroadcastReportOut",
    "BroadcastStatus",
    "RevokeLinkOut",
    "SectionCountsOut",
    "SendRunOut",
]
