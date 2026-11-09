"""Pydantic IO for legal holds (docs/09 R3 "Legal holds"; ADR-0059; FR-DPDP-039..FR-DPDP-052)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HoldScope = Literal["student", "record_class"]
HoldStatus = Literal["pending", "active", "lift_pending", "lifted", "rejected"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HoldCreate(_In):
    """Ask to place a hold. Exactly one of ``student_id`` (scope ``student``) or
    ``record_class`` (scope ``record_class``, a key of GET /privacy/record-classes)."""

    scope: HoldScope
    student_id: uuid.UUID | None = None
    record_class: str | None = Field(default=None, max_length=63, pattern=r"^[a-z][a-z0-9_]+$")
    reason_code: str = Field(max_length=63, pattern=r"^[a-z][a-z0-9_]+$")
    # C3: the case, FIR or letter reference; encrypted, never logged or shown in access reports.
    reference: str | None = Field(default=None, min_length=2, max_length=200)
    review_on: dt.date


class HoldOptionOut(BaseModel):
    code: str
    label: str


class HoldOut(BaseModel):
    id: uuid.UUID
    scope: HoldScope
    student_id: uuid.UUID | None
    # The student's name and admission number (caller's scope), else "Student".
    subject_label: str
    record_class: str | None
    reason_code: str
    reason_label: str
    reference: str | None
    review_on: dt.date
    review_overdue: bool
    status: HoldStatus
    effective: bool
    placed_by_name: str | None
    placed_at: dt.datetime
    approved_by_name: str | None
    approved_at: dt.datetime | None
    lift_requested_by_name: str | None
    lift_requested_at: dt.datetime | None
    lifted_by_name: str | None
    lifted_at: dt.datetime | None
    rejected_at: dt.datetime | None
    # Whether the caller may approve the pending step (not the person who asked for it).
    can_approve: bool
    version: int


class HoldListOut(BaseModel):
    items: list[HoldOut]
    reason_codes: list[HoldOptionOut]
    record_classes: list[HoldOptionOut]
    max_review_on: dt.date


__all__ = [
    "HoldCreate",
    "HoldListOut",
    "HoldOptionOut",
    "HoldOut",
    "HoldScope",
    "HoldStatus",
]
