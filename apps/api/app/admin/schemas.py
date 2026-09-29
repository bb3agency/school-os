"""Pydantic IO for the admin routes (docs/09 Exports, audit, admin; US-1201, FR-ADM-001,
FR-ADM-002)."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.admin.config import CATEGORY_RE

TenantExportStatus = Literal["queued", "running", "ready", "failed", "expired"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


# --- full data export (FR-ADM-001) --------------------------------------------------------------


class TenantExportCreate(_In):
    """Request a full export of the school's data. Restricted (C3) values (health notes,
    category, guardian phone and address, the last four Aadhaar digits) are masked unless
    ``include_sensitive`` is true, which needs ``student.read_sensitive`` for the whole
    school."""

    include_sensitive: bool = False


class MemberOut(_Out):
    """A staff member shown next to a record: membership id and display name only (``null``
    when the account is no longer visible)."""

    membership_id: uuid.UUID
    display_name: str | None


class TenantExportCounts(_Out):
    """Row counts per table, stored documents copied, and audit events in the archive."""

    tables: dict[str, int] = Field(default_factory=dict)
    documents: int = 0
    document_bytes: int = 0
    audit_events: int = 0


class TenantExportOut(_Out):
    id: uuid.UUID
    status: TenantExportStatus
    include_sensitive: bool
    error_code: str | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    expires_at: dt.datetime | None = Field(
        description="When the archive is deleted (24 hours after it is ready)."
    )
    size_bytes: int | None
    counts: TenantExportCounts | None
    requested_by: MemberOut
    own: bool = Field(description="You requested this export.")
    can_download: bool = Field(
        description="The archive is ready, not expired, and your permissions allow a download "
        "link now (a 428 asking you to sign in again with MFA can still follow)."
    )


class TenantExportDownloadOut(_Out):
    url: str
    expires_at: dt.datetime
    filename: str
    content_type: str
    size_bytes: int


# --- retention settings (FR-ADM-002) ------------------------------------------------------------


class RetentionCategoryOut(_Out):
    key: str
    days: int = Field(description="Days this school keeps the data (its setting or the default).")
    default_days: int
    min_days: int
    max_days: int
    configurable: bool = Field(description="False: fixed by SchoolOS (law, security, feature).")
    enforced: bool = Field(description="A daily job deletes the data after ``days``.")
    is_default: bool


class RetentionOut(_Out):
    categories: list[RetentionCategoryOut]
    version: int = Field(description="Send as If-Match when changing (0: never changed).")
    updated_at: dt.datetime | None
    updated_by: MemberOut | None


class RetentionUpdate(_In):
    """The retention period in days per configurable category. A category left out goes back
    to its default."""

    rules: dict[str, int] = Field(default_factory=dict, max_length=50)

    @field_validator("rules")
    @classmethod
    def _rules(cls, value: dict[str, int]) -> dict[str, int]:

        for key, days in value.items():
            if not re.match(CATEGORY_RE, key):
                raise ValueError("categories are identifiers such as import_raw_files")
            if isinstance(days, bool) or not 1 <= days <= 3650:
                raise ValueError("days are whole numbers from 1 to 3650")
        return value
