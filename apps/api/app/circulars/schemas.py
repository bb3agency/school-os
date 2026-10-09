"""Pydantic IO for circulars, tasks and parent notices (docs/09 Circulars, tasks, notices;
US-1601..US-1606; FR-CIR-*, FR-TASK-*, FR-NOTICE-*). Text is NFC-normalised and trimmed on input.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.core.textnorm import nfc

ReadingStatus = Literal["not_read", "queued", "running", "ready", "needs_review"]
SuggestionStatus = Literal["suggested", "confirmed", "dismissed"]
TaskStatus = Literal["open", "in_progress", "done", "cancelled"]
TaskSource = Literal["manual", "circular"]
TaskView = Literal["mine", "all"]
DueWindow = Literal["overdue", "week", "later"]
NoticeStatus = Literal["drafting", "draft", "draft_failed", "approved"]
NoticeSource = Literal["circular", "staff_text", "blank"]
RenderStatus = Literal["queued", "ready", "failed"]
NoticeFileFormat = Literal["pdf", "png"]


def _clean(value: Any) -> Any:
    return nfc(value).strip() if isinstance(value, str) else value


Text = Annotated[str, BeforeValidator(_clean)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class MemberOut(_Out):
    """A staff member next to a record: membership id and display name only."""

    membership_id: uuid.UUID
    display_name: str | None


# --- circulars (FR-CIR-*) -----------------------------------------------------------------------


class CitationOut(_Out):
    """Where a suggestion or summary comes from: a page of one circular version (docs/06 §8).
    ``quote`` is the exact sentence of the circular (for a summary: the start of the passage)."""

    source: str = Field(description="sos://doc/{document_id}/v{n}#p{page}")
    passage: int
    page: int | None
    quote: str


class SuggestionOut(_Out):
    id: uuid.UUID
    position: int
    title: str
    details: str | None
    due_on: dt.date
    citation: CitationOut
    status: SuggestionStatus
    task_id: uuid.UUID | None
    decided_at: dt.datetime | None
    version: int


class ReadingOut(_Out):
    """The AI reading of one circular version: suggestions only, until a person confirms."""

    id: uuid.UUID
    version_no: int
    status: Literal["queued", "running", "ready", "needs_review"]
    error_code: str | None = Field(
        description="Why it needs manual review: ai_disabled, ai_budget_exhausted, "
        "ai_rate_limited, ai_unavailable, ai_request_rejected, ai_invalid_output, no_text, "
        "document_gone."
    )
    issuer: str | None
    reference_no: str | None
    issued_on: dt.date | None
    subject: str | None
    summary_en: str | None
    summary_te: str | None
    summary_sources: list[CitationOut]
    suggestions: list[SuggestionOut]
    suggestions_dropped: int = Field(
        description="Suggestions the checks refused (date or quote not in the circular)."
    )
    passages_sent: int | None
    passages_total: int | None
    attempts: int
    can_retry: bool
    ai_generated: Literal[True] = True
    reviewed_at: dt.datetime | None
    reviewed_by: MemberOut | None
    completed_at: dt.datetime | None
    version: int


class CircularOut(_Out):
    """A circular (document of type ``circular``) and where its reading stands."""

    document_id: uuid.UUID
    title: str
    issuer: str | None = Field(description="As entered by the office when uploading.")
    issued_on: dt.date | None
    current_version_no: int | None
    reading_status: ReadingStatus
    reading_error: str | None
    reviewed: bool
    open_suggestions: int
    tasks: int
    created_at: dt.datetime


class CircularDetail(CircularOut):
    sensitivity: Literal["C1", "C2", "C3"]
    reading: ReadingOut | None


class ReviewIn(_In):
    """Mark the current reading reviewed (every suggestion decided, or none were right)."""


class SuggestionConfirmIn(_In):
    """Turn a suggestion into a task. An unset due date keeps the suggestion's; an unset title
    is a neutral one ("Follow up circular") and unset details stay empty: the AI summary is never
    copied into the task (audit DL-08)."""

    owner_membership_id: uuid.UUID
    title: Text | None = Field(default=None, min_length=1, max_length=200)
    details: Text | None = Field(default=None, max_length=2000)
    due_on: dt.date | None = None


class SuggestionDismissIn(_In):
    """Dismiss a suggestion (no task is created)."""


# --- tasks (FR-TASK-*) ----------------------------------------------------------------------------


class TaskOut(_Out):
    id: uuid.UUID
    title: str
    details: str | None
    owner: MemberOut
    due_on: dt.date
    status: TaskStatus
    overdue: bool
    source: TaskSource
    document_id: uuid.UUID | None
    citation: CitationOut | None = Field(
        description="Where the task comes from; hidden when you cannot see that circular."
    )
    created_by: MemberOut | None
    created_at: dt.datetime
    completed_at: dt.datetime | None
    cancelled_at: dt.datetime | None
    version: int


class TaskCreate(_In):
    title: Text = Field(min_length=1, max_length=200)
    details: Text | None = Field(default=None, max_length=2000)
    owner_membership_id: uuid.UUID
    due_on: dt.date
    document_id: uuid.UUID | None = Field(
        default=None, description="Link the task to a circular you can see (optional)."
    )


class TaskUpdate(_In):
    title: Text | None = Field(default=None, min_length=1, max_length=200)
    details: Text | None = Field(default=None, max_length=2000)
    owner_membership_id: uuid.UUID | None = None
    due_on: dt.date | None = None


class TaskStatusIn(_In):
    status: TaskStatus


class AssigneeOut(_Out):
    membership_id: uuid.UUID
    display_name: str
    roles: list[str]


# --- parent notices (FR-NOTICE-*) -----------------------------------------------------------------


class NoticeCreate(_In):
    """Start a notice: drafted by AI from a circular (``document_id``) or from your text
    (``text``), or ``blank`` to write it yourself."""

    source: NoticeSource
    document_id: uuid.UUID | None = None
    text: Text | None = Field(default=None, max_length=4000)


class NoticeUpdate(_In):
    title_en: Text | None = Field(default=None, max_length=120)
    body_en: Text | None = Field(default=None, max_length=1500)
    title_te: Text | None = Field(default=None, max_length=120)
    body_te: Text | None = Field(default=None, max_length=1500)


class NoticeApproveIn(_In):
    """Approve the notice as it is now (both languages filled, no personal numbers)."""


class NoticeRenderIn(_In):
    """Render the A4 PDF and the image again (files are kept for a few days only)."""


class NoticeRedraftIn(_In):
    """Ask the AI to draft a notice again after it could not (``draft_failed``)."""


class NoticeOut(_Out):
    id: uuid.UUID
    source: NoticeSource
    document_id: uuid.UUID | None
    status: NoticeStatus = Field(
        description="drafting (the AI is drafting it in the background: ask again shortly), "
        "draft, draft_failed (see draft_error: try again or write it yourself) or approved."
    )
    ai_drafted: bool
    draft_error: str | None = Field(
        description="Why the AI did not draft it (ai_disabled, ai_budget_exhausted, "
        "ai_unavailable, no_text, source_unavailable, notice_source_personal, worker_error, "
        "...): try again or write it."
    )
    title_en: str
    body_en: str
    title_te: str
    body_te: str
    created_by: MemberOut | None
    approved_by: MemberOut | None
    approved_at: dt.datetime | None
    render_status: RenderStatus | None
    render_error: str | None
    files_available: bool
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int


class NoticeDownloadOut(_Out):
    url: str
    expires_at: dt.datetime
    filename: str
    mime_type: str


__all__ = [
    "AssigneeOut",
    "CircularDetail",
    "CircularOut",
    "CitationOut",
    "DueWindow",
    "MemberOut",
    "NoticeApproveIn",
    "NoticeCreate",
    "NoticeDownloadOut",
    "NoticeFileFormat",
    "NoticeOut",
    "NoticeRedraftIn",
    "NoticeRenderIn",
    "NoticeUpdate",
    "ReadingOut",
    "ReviewIn",
    "SuggestionConfirmIn",
    "SuggestionDismissIn",
    "SuggestionOut",
    "TaskCreate",
    "TaskOut",
    "TaskStatus",
    "TaskStatusIn",
    "TaskUpdate",
    "TaskView",
]
