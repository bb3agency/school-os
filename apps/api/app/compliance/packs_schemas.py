"""Pydantic IO for inspection readiness packs (docs/09 "Inspection readiness packs"; FC-07,
FO-12; FR-COMP-024..FR-COMP-045).

Names are module-specific (``InspectionPack…``) so the OpenAPI document has no qualified names.
Everything here is school-level: states, dates, counts and record ids, never a student or staff
list (registers are linked, the register print view itself is the audited view).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.compliance.packs_config import RegisterKey, Source

ItemState = Literal[
    "ready", "expiring", "no_copy", "incomplete", "expired", "missing", "left_out", "not_tracked"
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class InspectionPackItemOut(_Out):
    key: str
    label: str
    source: Source
    state: ItemState
    fix_by: str = Field(description="The role that usually fixes a gap (from the pack file).")
    note: str | None = None
    feature: str | None = Field(
        default=None, description="not_tracked: the research id that would add this evidence."
    )
    kind: str | None = Field(default=None, description="certificate: the catalogue kind.")
    certificate_id: uuid.UUID | None = None
    valid_until: dt.date | None = None
    has_copy: bool | None = Field(default=None, description="certificate: a copy in Documents.")
    register_view: RegisterKey | None = None
    included: bool | None = Field(
        default=None, description="register: chosen when the pack was made (None: live view)."
    )
    snapshot_id: uuid.UUID | None = None
    published_at: dt.datetime | None = None
    notice_id: uuid.UUID | None = None
    issued_on: dt.date | None = None
    done: int | None = None
    applicable: int | None = None


class InspectionPackCountsOut(_Out):
    ready: int = 0
    attention: int = Field(default=0, description="expiring, no copy or incomplete")
    missing: int = Field(default=0, description="expired or missing")
    not_tracked: int = 0
    left_out: int = Field(default=0, description="registers not chosen for this pack")


class InspectionPackReadinessOut(_Out):
    key: str
    label: str
    authority: str
    note: str
    items: list[InspectionPackItemOut]
    counts: InspectionPackCountsOut


class InspectionReadinessOut(_Out):
    verified: bool = Field(description="false: the lists are a SchoolOS proposal, unconfirmed.")
    source: str
    version: int
    packs: list[InspectionPackReadinessOut]


class InspectionPackCreate(_In):
    pack_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    academic_year_id: uuid.UUID | None = Field(
        default=None, description="The registers' academic year (default: the current one)."
    )
    visit_on: dt.date | None = Field(default=None, description="The inspection date, if known.")
    registers: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Item keys of the pack's registers to include (student lists: needs "
        "register.read). Registers not chosen print as left out.",
    )

    @field_validator("registers")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("registers repeat")
        return value


class InspectionPackOut(_Out):
    id: uuid.UUID
    pack_key: str
    pack_label: str
    authority: str
    pack_version: int
    verified: bool
    academic_year_id: uuid.UUID | None
    academic_year_label: str | None
    visit_on: dt.date | None
    registers: list[str]
    counts: InspectionPackCountsOut
    generated_at: dt.datetime
    generated_by_name: str | None


class InspectionPackDetailOut(InspectionPackOut):
    items: list[InspectionPackItemOut]


__all__ = [
    "InspectionPackCountsOut",
    "InspectionPackCreate",
    "InspectionPackDetailOut",
    "InspectionPackItemOut",
    "InspectionPackOut",
    "InspectionPackReadinessOut",
    "InspectionReadinessOut",
    "ItemState",
]
