"""Bodies of the knowledge-gap routes (FA-06; FR-KB-081..FR-KB-095; docs/09 Knowledge gaps).

A gap carries one REDACTED sample question and counts; never the asker, a query id, an
answer or a prompt.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KnowledgeGapOut(BaseModel):
    """A group of repeated questions Ask could not answer (ETag = ``version``)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    question: str | None = Field(
        description="One sample question after redaction (numbers, phones, emails masked); "
        "null when it cannot be read."
    )
    language: Literal["en", "te"] | None
    occurrences: int = Field(description="Questions counted into this gap.")
    not_found_count: int = Field(description='Answered "not found in school records".')
    unchecked_count: int = Field(
        description="Came back search-only because the answer could not be checked."
    )
    not_helpful_count: int = Field(description='Marked "not helpful" by the asker.')
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
    status: Literal["open", "closed", "dismissed"]
    resolution: Literal["verified_answer", "document"] | None
    verified_answer_id: uuid.UUID | None
    document_id: uuid.UUID | None
    closed_by: uuid.UUID | None = Field(description="Membership id of who closed it.")
    closed_by_name: str | None
    closed_at: dt.datetime | None
    version: int


class KnowledgeGapMonthOut(BaseModel):
    month: str = Field(description="IST calendar month, YYYY-MM.")
    closed_with_answer: int
    closed_with_document: int
    dismissed: int


class KnowledgeGapSummaryOut(BaseModel):
    """Counts only (FR-KB-090)."""

    open_gaps: int
    not_found: int = Field(description="Not-found questions in open gaps.")
    unchecked: int = Field(description="Unchecked (search-only) questions in open gaps.")
    not_helpful: int = Field(description="Not-helpful answers in open gaps.")
    months: list[KnowledgeGapMonthOut] = Field(description="This month first, then two before.")
    retention_days: int = Field(description="Days a gap is kept after it was last seen or closed.")


class KnowledgeGapCloseIn(BaseModel):
    """Close a gap with an existing verified answer or the document that was missing, or
    dismiss it (a question the library should not answer)."""

    model_config = ConfigDict(extra="forbid")

    resolution: Literal["verified_answer", "document", "dismissed"]
    verified_answer_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one_link(self) -> KnowledgeGapCloseIn:
        wants_answer = self.resolution == "verified_answer"
        wants_document = self.resolution == "document"
        if wants_answer != (self.verified_answer_id is not None):
            raise ValueError("verified_answer_id goes with resolution verified_answer only")
        if wants_document != (self.document_id is not None):
            raise ValueError("document_id goes with resolution document only")
        return self


__all__ = [
    "KnowledgeGapCloseIn",
    "KnowledgeGapMonthOut",
    "KnowledgeGapOut",
    "KnowledgeGapSummaryOut",
]
