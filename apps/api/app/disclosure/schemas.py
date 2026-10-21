"""Request and response models of the disclosure routes (docs/09 "Disclosure"; FR-DISC-*).

The public models (:class:`PublicDisclosureOut` and its parts) are the explicit allowlist the
public page receives (FR-DISC-009, SEC-050): the school's name, when it was published, the format
version and, per item, its key, label and one of ``value``, ``table``, ``documents`` (number and
title) or ``link``. Nothing else of a snapshot reaches the public origin.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

FileStatus = Literal["queued", "ready", "failed"]
FileFormat = Literal["html", "pdf"]
ItemKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
DocumentIds = Annotated[list[uuid.UUID], Field(max_length=10)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# --- format ---------------------------------------------------------------------------------------


class FormatSourceOut(_Out):
    title: str
    url: str | None


class FormatItemOut(_Out):
    key: str
    label: str
    fill: Literal["school", "derived", "input", "document", "link"]
    type: Literal["text", "integer", "number", "yes_no", "email", "phone", "results"] | None
    required: bool
    contact: bool
    takes_documents: bool = Field(description="The item accepts school documents (PDFs).")
    max_length: int | None
    max_value: int | None
    max_rows: int | None


class FormatSectionOut(_Out):
    key: str
    title: str
    items: list[FormatItemOut]


class DisclosureFormatOut(_Out):
    key: str
    version: int
    title: str
    verified: bool = Field(
        description="false: built from public sources, not yet confirmed with a CBSE school (D3)."
    )
    sources: list[FormatSourceOut]
    sections: list[FormatSectionOut]


# --- shown values (draft preview, snapshots and the public page) ---------------------------------


class TableOut(_Out):
    columns: list[str]
    rows: list[list[str]]


class PublicDocumentOut(_Out):
    n: int = Field(ge=1, description="Number of the document in GET .../documents/{n}.")
    title: str


class PublicItemOut(_Out):
    key: str
    label: str
    value: str | None = None
    table: TableOut | None = None
    documents: list[PublicDocumentOut] = Field(default_factory=list)
    link: str | None = None


class PublicSectionOut(_Out):
    key: str
    title: str
    items: list[PublicItemOut]


class PublicDisclosureOut(_Out):
    """The published disclosure page (the explicit allowlist; FR-DISC-009)."""

    school_name: str
    title: str
    published_at: dt.datetime
    format_version: int
    sections: list[PublicSectionOut]


class PublicDocumentLinkOut(_Out):
    url: str
    expires_at: dt.datetime


# --- draft ----------------------------------------------------------------------------------------


class DerivedValueOut(_Out):
    """A value filled from SchoolOS records as it is now (school settings or derived)."""

    value: str | None
    table: TableOut | None


class DraftDocumentOut(_Out):
    id: uuid.UUID
    title: str | None = Field(description="null when the document is gone or hidden from you.")
    publishable: bool = Field(
        description="A clean PDF of a circular, policy or other school document (not C3)."
    )


class DisclosureDraftOut(_Out):
    format_key: str
    format_version: int
    inputs: dict[str, Any]
    documents: dict[str, list[DraftDocumentOut]]
    from_schoolos: dict[str, DerivedValueOut] = Field(
        description="School and derived items by item key, as they are now (not frozen)."
    )
    missing: list[str] = Field(description="Required items still empty (publishing refuses).")
    version: int = Field(description="0 until the draft is first saved; send it in If-Match.")
    updated_at: dt.datetime | None
    updated_by_name: str | None


class DisclosureDraftIn(_In):
    """The whole draft: typed values and chosen documents by item key (empty values clear)."""

    inputs: dict[ItemKey, Any] = Field(default_factory=dict, max_length=100)
    documents: dict[ItemKey, DocumentIds] = Field(default_factory=dict, max_length=100)


# --- publish, snapshots, link --------------------------------------------------------------------


class PublishIn(_In):
    confirm_no_student_data: bool = Field(
        description="The publisher confirms the chosen documents contain no student's personal "
        "data (FR-DISC-005); false or missing answers 422 confirmation_required."
    )


class WithdrawIn(_In):
    reason: str = Field(min_length=10, max_length=500)


class SnapshotOut(_Out):
    id: uuid.UUID
    format_key: str
    format_version: int
    published_at: dt.datetime
    published_by_name: str | None
    withdrawn_at: dt.datetime | None
    withdrawn_by_name: str | None
    withdraw_reason: str | None
    live: bool = Field(description="The page shows this snapshot now.")
    documents: int
    views_total: int = Field(description="Views counted in the last 90 days (no visitor data).")
    pdf_status: FileStatus
    html_status: FileStatus


class SnapshotDetailOut(SnapshotOut):
    content: PublicDisclosureOut
    views_by_day: dict[str, int]


class LinkOut(_Out):
    url: str | None = Field(description="The public page link; null before the first publish.")
    created_at: dt.datetime | None


class FileLinkOut(_Out):
    url: str
    expires_at: dt.datetime
    filename: str
    mime_type: str


__all__ = [
    "DerivedValueOut",
    "DisclosureDraftIn",
    "DisclosureDraftOut",
    "DisclosureFormatOut",
    "DraftDocumentOut",
    "FileFormat",
    "FileLinkOut",
    "FileStatus",
    "FormatItemOut",
    "FormatSectionOut",
    "FormatSourceOut",
    "LinkOut",
    "PublicDisclosureOut",
    "PublicDocumentLinkOut",
    "PublicDocumentOut",
    "PublicItemOut",
    "PublicSectionOut",
    "PublishIn",
    "SnapshotDetailOut",
    "SnapshotOut",
    "TableOut",
    "WithdrawIn",
]
