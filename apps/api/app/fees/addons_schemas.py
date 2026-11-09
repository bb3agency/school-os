"""Pydantic v2 IO models of add-on fees (FM-02; FR-FEE-081..100; docs/09 "Fees").

Inputs forbid unknown fields and NFC-normalise + trim text. Money is :class:`decimal.Decimal`
rupees with at most two decimals, strings out. Amounts are left out (``None``) for a caller
without ``finance.read`` or ``fee.manage``: a transport in-charge holding only
``fee.addon.read`` sees who is on which add-on, never what they owe (FR-FEE-095).
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field, model_validator

from app.fees.schemas import InstalmentNo, Line, Money, Note, PositiveMoney, _In, _Out

AssignmentStatus = Literal["active", "ended"]
AddonSkipReason = Literal[
    "no_fee_account", "account_closed", "head_taken", "already_assigned", "no_instalments_left"
]


class AddonCreate(_In):
    """An add-on template of one academic year (FR-FEE-081): a collectable head of an allowed
    kind, a label such as "Band 2 (5-10 km)" and the amount per instalment number."""

    academic_year_id: uuid.UUID
    head_id: uuid.UUID
    label: Line
    amounts: dict[InstalmentNo, PositiveMoney] = Field(min_length=1, max_length=24)


class AddonPatch(_In):
    """Rename or switch off a template; its amounts change only while nobody is assigned
    (409 ``addon_in_use``; FR-FEE-083)."""

    label: Line | None = None
    active: bool | None = None
    amounts: dict[InstalmentNo, PositiveMoney] | None = Field(
        default=None, min_length=1, max_length=24
    )


class AddonOut(_Out):
    id: uuid.UUID
    academic_year_id: uuid.UUID
    head_id: uuid.UUID
    head_code: str
    head_name: str
    head_kind: str
    label: str
    active: bool
    assigned: int
    amounts: dict[int, Money] | None
    total: Money | None
    version: int


class AddonsOut(_Out):
    items: list[AddonOut]
    amounts_shown: bool
    gst_note: str


class AddonTarget(_In):
    """Whom: exactly one of a class (its sections of the template's year), some sections, or a
    picked list of students (FR-FEE-084)."""

    class_id: uuid.UUID | None = None
    section_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=50)
    student_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=300)
    dry_run: bool = False

    @model_validator(mode="after")
    def _one_target(self) -> AddonTarget:
        given = [v for v in (self.class_id, self.section_ids, self.student_ids) if v is not None]
        if len(given) != 1:
            raise ValueError("give exactly one of class_id, section_ids or student_ids")
        return self


class AddonAssignIn(AddonTarget):
    """Assign from ``start_instalment``: the template's amounts of that and later instalments
    (of the student's plan) are charged now. ``replace`` ends another add-on of the same head
    first (a band change); its posted charges from ``start_instalment`` are proposed as credits
    with ``reason`` (maker-checker, D85)."""

    start_instalment: InstalmentNo
    replace: bool = False
    reason: Note | None = None


class AddonSkipped(_Out):
    student_id: uuid.UUID
    reason: AddonSkipReason


class AddonAssignOut(_Out):
    dry_run: bool
    students: int
    assigned: int
    charges_posted: int
    replaced: int
    credits_proposed: int
    skipped: list[AddonSkipped]


class AddonUnassignIn(AddonTarget):
    """Take students off from ``from_instalment``: their assignment ends before it, and the
    charges already posted for it and later instalments that are still unpaid are proposed as
    credits (``addon_removal`` adjustments) for a holder of ``fee.concession.approve``. Money
    already paid is returned only through the refund flow (FR-FEE-089, FR-FEE-090)."""

    from_instalment: InstalmentNo
    reason: Note


class AddonUnassignOut(_Out):
    dry_run: bool
    students: int
    ended: int
    not_assigned: int
    credits_proposed: int
    credit_total: Money


class AddonStudentOut(_Out):
    assignment_id: uuid.UUID
    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None
    start_instalment: int
    end_instalment: int | None
    status: AssignmentStatus
    charged: Money | None


class AddonStudentsOut(_Out):
    addon_id: uuid.UUID
    label: str
    head_name: str
    amounts_shown: bool
    items: list[AddonStudentOut]


__all__ = [
    "AddonAssignIn",
    "AddonAssignOut",
    "AddonCreate",
    "AddonOut",
    "AddonPatch",
    "AddonSkipped",
    "AddonStudentOut",
    "AddonStudentsOut",
    "AddonTarget",
    "AddonUnassignIn",
    "AddonUnassignOut",
    "AddonsOut",
]
