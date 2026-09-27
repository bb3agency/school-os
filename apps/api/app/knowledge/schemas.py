"""Request and response bodies of the knowledge routes (docs/09 Knowledge, docs/06 §5.1).

Questions and search text travel only in JSON bodies, never in URLs (SEC-008). Responses carry
ids, ``sos://`` sources, titles and passages the caller may read; never another user's
question or answer.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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
    """One question to the school's records and documents (docs/09 §5.4)."""

    question: str = Field(min_length=1, max_length=1000)
    session_id: uuid.UUID = Field(
        description="The browser's Ask session: context never crosses users (FR-KB-012)."
    )


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


class FeedbackIn(_In):
    feedback: Literal["helpful", "not_helpful"]
    reason: Code | None = Field(
        default=None,
        description="A reason code (e.g. wrong_source, outdated, not_found_but_exists); "
        "never free text.",
    )


class FeedbackOut(_Out):
    query_id: uuid.UUID
    feedback: Literal["helpful", "not_helpful"]
    reason: str | None
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
    verified_at: dt.datetime
    review_due: dt.date | None
    version: int
    created_at: dt.datetime


VerifiedStatus = Literal["active", "needs_review", "retired"]

__all__ = [
    "AskIn",
    "Code",
    "FeedbackIn",
    "FeedbackOut",
    "Locale",
    "SearchIn",
    "SearchOut",
    "SearchResultOut",
    "VerifiedAnswerIn",
    "VerifiedAnswerOut",
    "VerifiedCitationIn",
    "VerifiedCitationOut",
    "VerifiedStatus",
]
