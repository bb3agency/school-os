"""Pydantic IO for the safety check log and the policy library (docs/09 "Safety checks and
policies"; FC-09, FC-10; FR-COMP-046..FR-COMP-070).

Inputs forbid unknown fields and trim text. Check entries hold counts and a short note, never
student names. Policy acknowledgements name staff members (display name only).
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.compliance.schemas import ComplianceOwnerOut, DocumentRefOut

CheckStateOut = Literal["ok", "due_soon", "overdue", "never", "logged"]
ReviewStateOut = Literal["ok", "due_soon", "overdue"]
CheckResult = Literal["ok", "action_needed"]
PolicyStatus = Literal["active", "retired"]
RoleKey = Annotated[str, Field(max_length=63, pattern=r"^[a-z][a-z0-9_]{1,62}$")]


def _nfc(value: str | None) -> str | None:
    if value is None:
        return None
    out = unicodedata.normalize("NFC", value).strip()
    return out or None


Text120 = Annotated[str | None, Field(default=None, max_length=120), AfterValidator(_nfc)]
Text500 = Annotated[str | None, Field(default=None, max_length=500), AfterValidator(_nfc)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- safety checks (FC-09) ----------------------------------------------------------------------


class CheckCreate(_In):
    """Log a drill or periodic check. Counts and a short note only: never type a student's
    name."""

    kind: str = Field(max_length=63, pattern=r"^[a-z][a-z0-9_]{1,62}$")
    label: Text120 = Field(
        default=None, description="Only for kind 'other': the check's name (3-120)."
    )
    done_on: dt.date = Field(description="The day it was done (not in the future).")
    led_by_membership_id: uuid.UUID | None = Field(
        default=None, description="The staff member who led it (an active member)."
    )
    participants_count: int | None = Field(
        default=None, ge=0, le=20000, description="Drills only: how many people took part."
    )
    duration_minutes: int | None = Field(
        default=None, ge=0, le=1440, description="Drills only: minutes to evacuate or finish."
    )
    result: CheckResult = Field(
        default="ok", description="'action_needed' when something must be fixed."
    )
    observations: Text500 = Field(
        default=None,
        description="What was found (1-500). Do not type the names of students or staff.",
    )
    document_id: uuid.UUID | None = Field(
        default=None,
        description="A report in Documents you can see. Do not upload photos that identify "
        "children.",
    )


class CheckKindOut(BaseModel):
    kind: str
    label: str
    every_months: int | None
    drill: bool
    note: str
    applies: bool = Field(description="It applies to this school's boards.")


class ComplianceCheckOut(BaseModel):
    id: uuid.UUID
    kind: str
    kind_label: str
    label: str | None
    done_on: dt.date
    led_by: ComplianceOwnerOut | None
    participants_count: int | None
    duration_minutes: int | None
    result: CheckResult
    observations: str | None
    document: DocumentRefOut | None
    status: Literal["recorded", "removed"]
    recorded_by_name: str | None
    created_at: dt.datetime
    version: int


class CheckStatusOut(BaseModel):
    """One expected check kind and when it is next due."""

    kind: str
    kind_label: str
    every_months: int | None
    drill: bool
    state: CheckStateOut
    last_done_on: dt.date | None
    next_due: dt.date | None
    days_left: int | None = Field(description="Days until it is due (negative once overdue).")
    last_entry_id: uuid.UUID | None


class CheckCountsOut(BaseModel):
    overdue: int
    due_soon: int
    never: int
    ok: int


class ChecksOverviewOut(BaseModel):
    statuses: list[CheckStatusOut] = Field(description="Most urgent first.")
    counts: CheckCountsOut
    recent: list[ComplianceCheckOut] = Field(description="The latest entries, newest first.")
    kinds: list[CheckKindOut] = Field(description="The catalogue (verified: false).")
    due_soon_days: int
    verified: bool
    today: dt.date
    leaders: list[ComplianceOwnerOut] = Field(
        default_factory=list,
        description="Active members who can be named as having led a check (compliance.manage).",
    )


class ChecksLogOut(BaseModel):
    items: list[ComplianceCheckOut]


# --- policies (FC-10) ---------------------------------------------------------------------------


class PolicyCreate(_In):
    """Add a policy to the library: a clean PDF in Documents of purpose 'policy', its next review
    date, and the roles whose members must acknowledge it."""

    title: str = Field(min_length=3, max_length=120)
    document_id: uuid.UUID
    review_due: dt.date | None = Field(
        default=None, description="Next review date; default 12 months from today (config)."
    )
    ack_roles: list[RoleKey] = Field(default_factory=list, max_length=50)
    notify: bool = Field(default=True, description="Ask those members to acknowledge it now.")


class PolicyUpdate(_In):
    """Correct a policy (send every field). ``status: retired`` takes it out of the staff's
    list; acknowledgements are kept."""

    title: str = Field(min_length=3, max_length=120)
    document_id: uuid.UUID
    review_due: dt.date
    ack_roles: list[RoleKey] = Field(max_length=50)
    status: PolicyStatus = "active"


class PolicyReview(_In):
    """Mark the policy reviewed today."""

    next_review_due: dt.date | None = Field(
        default=None, description="Next review date; default 12 months from today (config)."
    )


class PolicyRoleOut(BaseModel):
    role: str = Field(description="The role key.")
    name: str


class CompliancePolicyOut(BaseModel):
    id: uuid.UUID
    title: str
    document: DocumentRefOut | None
    current_version_id: uuid.UUID | None = Field(
        description="The clean PDF version staff acknowledge (null: not ready or removed)."
    )
    ack_roles: list[PolicyRoleOut]
    review_due: dt.date
    review_state: ReviewStateOut
    days_left: int
    last_reviewed_on: dt.date | None
    status: PolicyStatus
    targeted: int = Field(description="Active members asked to acknowledge it.")
    acknowledged: int = Field(description="How many of them acknowledged the current version.")
    ack_requested_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int


class PolicyMemberOut(BaseModel):
    membership_id: uuid.UUID
    display_name: str
    roles: list[str]
    acknowledged_at: dt.datetime | None = Field(description="For the current version.")
    acknowledged_earlier: bool = Field(description="Acknowledged an earlier version only.")


class PolicyDetailOut(CompliancePolicyOut):
    members: list[PolicyMemberOut] = Field(description="Who has not acknowledged comes first.")


class PolicyCountsOut(BaseModel):
    overdue: int
    due_soon: int
    pending_acks: int = Field(description="Acknowledgements still missing across active policies.")


class PoliciesOut(BaseModel):
    items: list[CompliancePolicyOut]
    counts: PolicyCountsOut
    roles: list[PolicyRoleOut] = Field(description="Roles a policy can ask to acknowledge.")
    review_months: int
    verified: bool
    today: dt.date


class PolicyAckRequestOut(BaseModel):
    notified: int = Field(description="Members who were asked (those still to acknowledge).")


class MyPolicyOut(BaseModel):
    id: uuid.UUID
    title: str
    current_version_id: uuid.UUID | None
    acknowledged_at: dt.datetime | None = Field(description="For the current version.")
    needs_ack: bool


class MyPoliciesOut(BaseModel):
    items: list[MyPolicyOut]
    pending: int


class PolicyAcknowledge(_In):
    document_version_id: uuid.UUID = Field(description="The version you opened and read.")


class PolicyFileOut(BaseModel):
    url: str
    expires_at: dt.datetime
    document_version_id: uuid.UUID


__all__ = [
    "CheckCountsOut",
    "CheckCreate",
    "CheckKindOut",
    "CheckResult",
    "CheckStatusOut",
    "ChecksLogOut",
    "ChecksOverviewOut",
    "ComplianceCheckOut",
    "CompliancePolicyOut",
    "MyPoliciesOut",
    "MyPolicyOut",
    "PoliciesOut",
    "PolicyAckRequestOut",
    "PolicyAcknowledge",
    "PolicyCountsOut",
    "PolicyCreate",
    "PolicyDetailOut",
    "PolicyFileOut",
    "PolicyMemberOut",
    "PolicyReview",
    "PolicyRoleOut",
    "PolicyStatus",
    "PolicyUpdate",
]
