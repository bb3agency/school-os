"""Pydantic v2 IO models for the DPDP readiness checklist, the notice given at admission and the
privacy notice sent on WhatsApp (docs/09 "R3: privacy"; FC-03, FP-13; FR-DPDP-053..FR-DPDP-066).

Inputs forbid unknown fields. No free text is accepted: dates, channels, ids and item keys only.
Every response that a screen shows carries ``not_legal_advice: true`` (owner decision D30).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.messaging.sends.schemas import BroadcastPreviewOut
from app.privacy.readiness_config import ItemKey, MarkItem
from app.privacy.schemas import PrivacyNoticeOut

ItemStatus = Literal["done", "todo", "not_applicable"]
AdmissionChannel = Literal[
    "paper", "in_person", "school_whatsapp_group", "sos_whatsapp", "email", "other"
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# --- checklist --------------------------------------------------------------------------------


class ReadinessMarkOut(_Out):
    item: MarkItem
    done_on: dt.date
    document_id: uuid.UUID | None
    recorded_by_name: str | None
    recorded_at: dt.datetime


class ReadinessItemOut(_Out):
    key: ItemKey
    status: ItemStatus
    detail: dict[str, str | int | bool | None] = Field(
        description="What the status is based on (dates, versions, counts; never names)."
    )
    mark: ReadinessMarkOut | None = Field(
        description="For the items the school records here: the latest record."
    )
    source: str = Field(description="Where the item comes from (docs/08).")


class AdmissionCountsOut(_Out):
    since: dt.date | None = Field(
        description="Date of the latest parents' notice; null when none is recorded yet."
    )
    added_since: int = Field(description="Active students added on or after that date.")
    without_notice: int = Field(description="Of those, students without a notice record.")


class ReadinessOut(_Out):
    not_legal_advice: Literal[True] = True
    done: int
    applicable: int
    items: list[ReadinessItemOut]
    admission: AdmissionCountsOut


class ReadinessMarkIn(_In):
    item: MarkItem
    done_on: dt.date
    document_id: uuid.UUID | None = Field(
        default=None, description="Evidence uploaded first as a document (signed DPA, DPIA...)."
    )


# --- notice given at admission ----------------------------------------------------------------


class AdmissionNoticeOut(_Out):
    student_id: uuid.UUID
    given_on: dt.date
    channel: AdmissionChannel
    notice_id: uuid.UUID | None
    recorded_by_name: str | None
    recorded_at: dt.datetime


class AdmissionPendingRowOut(_Out):
    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None
    added_on: dt.date


class AdmissionPendingOut(_Out):
    not_legal_advice: Literal[True] = True
    since: dt.date | None
    without_notice: int = Field(description="Every such student of the school (a count).")
    hidden: int = Field(description="Of those, students outside your reach (not listed).")
    items: list[AdmissionPendingRowOut]


class AdmissionNoticeIn(_In):
    student_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    given_on: dt.date
    channel: AdmissionChannel
    notice_id: uuid.UUID | None = Field(
        default=None, description="The parents' notice issue the family got (default: the latest)."
    )


class AdmissionRecordedOut(_Out):
    recorded: int
    already_recorded: int


# --- the privacy notice sent on WhatsApp (FP-13) ------------------------------------------------


class WhatsAppPreviewIn(_In):
    section_ids: list[uuid.UUID] | None = Field(
        default=None,
        min_length=1,
        max_length=300,
        description="Sections of the current year; none = every section.",
    )
    parent_notice_id: uuid.UUID | None = Field(
        default=None,
        description="The parent notice an earlier preview made (reused instead of a new one).",
    )


class WhatsAppPreviewOut(_Out):
    not_legal_advice: Literal[True] = True
    parent_notice_id: uuid.UUID
    template_version: str
    title: str
    body: str
    preview: BroadcastPreviewOut


class WhatsAppSendIn(_In):
    parent_notice_id: uuid.UUID
    preview_token: str = Field(min_length=8, max_length=80)
    section_ids: list[uuid.UUID] = Field(min_length=1, max_length=300)


class NotReachedRowOut(_Out):
    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None


class NoticeDeliveryOut(_Out):
    not_legal_advice: Literal[True] = True
    notice: PrivacyNoticeOut
    broadcast_id: uuid.UUID
    guardians: dict[str, int] = Field(
        description="Guardians by the furthest status their message reached (read, delivered, "
        "sent, queued, failed, skipped) and ``left_out`` (no opt-in, no number...)."
    )
    students: int = Field(description="Students of the broadcast's sections now.")
    students_not_reached: int = Field(
        description="Students none of whose guardians got the message delivered or read."
    )
    hidden: int = Field(description="Of those, students outside your reach (not listed).")
    not_reached: list[NotReachedRowOut] = Field(
        description="Students to give a paper copy to (only those you may see)."
    )
