"""Request and response bodies of the knowledge routes (docs/09 Knowledge, docs/06 §5.1).

Questions and search text travel only in JSON bodies, never in URLs (SEC-008). Responses carry
ids, ``sos://`` sources, titles and passages the caller may read; never another user's
question or answer.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.knowledge.tools.documents import DocType

Locale = Literal["en", "te", "mixed"]
Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
SourceUri = Annotated[
    str,
    StringConstraints(
        pattern=r"^sos://(doc|student|finding|change|verified)/[0-9a-f-]{36}[#/?a-z0-9_=-]*$",
        max_length=300,
    ),
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AskIn(_In):
    """One question to the school's records and documents (docs/09 §5.4, docs/06 §5).

    Send ``conversation_id`` to continue one of your conversations; omit it (and
    ``session_id``) to start a new one and read its id from the ``meta`` event.
    ``session_id`` is the older name: it continues your conversation with that id or starts
    one. ``regenerate_of`` answers one of your latest messages again (``question`` is then
    optional and ignored); ``edit_of`` replaces one with ``question``. Either way the message
    and every later one become ``superseded``."""

    question: str | None = Field(default=None, min_length=1, max_length=1000)
    session_id: uuid.UUID | None = Field(
        default=None,
        description="Older name of conversation_id: your conversation with this id, or a new "
        "one. Context never crosses users (FR-KB-012).",
    )
    conversation_id: uuid.UUID | None = Field(
        default=None, description="One of your conversations (404 for anyone else's)."
    )
    regenerate_of: uuid.UUID | None = Field(
        default=None, description="Answer this message of yours again (never from the cache)."
    )
    edit_of: uuid.UUID | None = Field(
        default=None, description="Replace this message of yours with `question`."
    )

    @model_validator(mode="after")
    def _one_request(self) -> AskIn:
        if self.regenerate_of is not None and self.edit_of is not None:
            raise ValueError("send regenerate_of or edit_of, not both")
        if self.regenerate_of is None and self.question is None:
            raise ValueError("question is required")
        return self


class SearchIn(_In):
    """Search-only retrieval (ranked, cited passages without generated prose)."""

    query: str = Field(min_length=1, max_length=300)
    doc_types: list[DocType] | None = Field(default=None, min_length=1, max_length=10)
    from_date: dt.date | None = None
    limit: int = Field(default=12, ge=1, le=50)


class SearchResultOut(_Out):
    source: str = Field(description="sos://doc/{document_id}/v{n}#p{page}")
    document_id: uuid.UUID
    version_no: int
    page_from: int | None
    page_to: int | None
    doc_type: str
    title: str
    issued_on: dt.date | None
    snippet: str
    score: float


class SearchOut(_Out):
    data: list[SearchResultOut]


FeedbackReason = Literal[
    "wrong_source", "outdated", "incomplete", "not_found_but_exists", "wrong_language"
]
"""Why an answer did not help (docs/09 Knowledge): a fixed code the UI translates, never text."""


class FeedbackIn(_In):
    feedback: Literal["helpful", "not_helpful"]
    reason: FeedbackReason | None = Field(
        default=None,
        description="Why the answer did not help, as a code; never free text.",
    )


class FeedbackOut(_Out):
    query_id: uuid.UUID
    feedback: Literal["helpful", "not_helpful"]
    reason: FeedbackReason | None
    recorded_at: dt.datetime


class VerifiedCitationIn(_In):
    source: SourceUri
    cited_text: str = Field(min_length=1, max_length=2000)


class VerifiedAnswerIn(_In):
    """An approved answer to a recurring question (FR-KB-030). Citations must point at the
    current version of documents you can read and quote their text."""

    question: str = Field(min_length=1, max_length=500)
    language: Locale
    answer_text: str = Field(min_length=1, max_length=5000)
    citations: list[VerifiedCitationIn] = Field(min_length=1, max_length=20)
    review_due: dt.date | None = None


class VerifiedAnswerReviewIn(_In):
    """Confirm a verified answer (typically ``needs_review``) as it is, or with a corrected
    text, new citations or a new review date. Omitted fields keep their stored value."""

    answer_text: str | None = Field(default=None, min_length=1, max_length=5000)
    citations: list[VerifiedCitationIn] | None = Field(default=None, min_length=1, max_length=20)
    review_due: dt.date | None = None


class VerifiedCitationOut(_Out):
    source: str
    cited_text: str


class VerifiedAnswerOut(_Out):
    id: uuid.UUID
    question: str
    language: Locale
    answer_text: str
    citations: list[VerifiedCitationOut]
    status: Literal["active", "needs_review", "retired"]
    verified_by: uuid.UUID = Field(description="Membership id of the person who verified it.")
    verified_by_name: str | None = Field(
        default=None,
        description="Display name of that person in this school (null if no longer a member).",
    )
    verified_at: dt.datetime
    review_due: dt.date | None
    version: int
    created_at: dt.datetime


VerifiedStatus = Literal["active", "needs_review", "retired"]


# --- conversations (docs/09 Knowledge; ADR-0033) ---------------------------------------------

MessageStatus = Literal[
    "answered", "not_found", "refused", "search_only", "error", "cancelled", "streaming"
]
"""``streaming``: the answer is still being written (or its stream ended unrecorded)."""


class ConversationOut(_Out):
    """One of your Ask conversations (ETag = ``version``, which changes with the title, the
    pin or a deletion; ``updated_at`` = the last activity: a message, a rename or a pin)."""

    id: uuid.UUID
    title: str
    pinned: bool
    created_at: dt.datetime
    updated_at: dt.datetime
    message_count: int = Field(description="Current (not superseded) messages.")
    version: int


class CitationOut(_Out):
    index: int = Field(description="The [n] marker in the answer.")
    source: str = Field(description="sos:// source (ids only).")
    title: str | None
    snippet: str | None
    withheld: bool = Field(
        default=False,
        description="True when you can no longer see the source (title and snippet are "
        "withheld) or its details were not kept.",
    )


class MessageOut(_Out):
    query_id: uuid.UUID
    question: str
    answer: str | None = Field(
        description="The checked final text with [n] citation markers (null when none, or "
        "withheld)."
    )
    answer_withheld: bool = Field(
        default=False,
        description="True when a source the answer cited is no longer visible to you: the "
        "answer and its follow-ups are then withheld too.",
    )
    status: MessageStatus
    mode: Literal["full", "search_only"]
    language: Locale | None
    citations: list[CitationOut]
    feedback: Literal["helpful", "not_helpful"] | None
    followups: list[str]
    created_at: dt.datetime
    superseded: bool = Field(description="Replaced by a regenerate or an edit.")
    cached: bool = Field(default=False, description="An exact repeat answered from the cache.")


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class ConversationPatchIn(_In):
    """Rename and/or pin (send only what changes)."""

    title: str | None = Field(default=None, min_length=1, max_length=120)
    pinned: bool | None = None


# --- memory (ADR-0033) -------------------------------------------------------------------------


class MemoryOut(_Out):
    """One of your memory items: your own preferences and work context in this school."""

    id: uuid.UUID
    text: str
    source: Literal["explicit", "suggested"]
    status: Literal["active", "pending"] = Field(
        description="pending: suggested by Ask, used only after you confirm it."
    )
    created_at: dt.datetime
    updated_at: dt.datetime
    expires_at: dt.datetime | None = Field(
        description="When a pending suggestion is deleted unless confirmed."
    )
    version: int


class MemoryIn(_In):
    text: str = Field(min_length=1, max_length=200)


class MemoryPatchIn(_In):
    text: str = Field(min_length=1, max_length=200)


class MemorySettingsOut(_Out):
    enabled: bool = Field(description="Your own switch (on unless you turned it off).")
    school_enabled: bool = Field(description="The school's switch (ai_memory_enabled).")


class MemorySettingsIn(_In):
    enabled: bool


__all__ = [
    "AskIn",
    "CitationOut",
    "Code",
    "ConversationDetailOut",
    "ConversationOut",
    "ConversationPatchIn",
    "FeedbackIn",
    "FeedbackOut",
    "FeedbackReason",
    "Locale",
    "MemoryIn",
    "MemoryOut",
    "MemoryPatchIn",
    "MemorySettingsIn",
    "MemorySettingsOut",
    "MessageOut",
    "MessageStatus",
    "SearchIn",
    "SearchOut",
    "SearchResultOut",
    "VerifiedAnswerIn",
    "VerifiedAnswerOut",
    "VerifiedAnswerReviewIn",
    "VerifiedCitationIn",
    "VerifiedCitationOut",
    "VerifiedStatus",
]
