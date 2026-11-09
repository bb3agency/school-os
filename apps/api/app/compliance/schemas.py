"""Pydantic IO for the compliance certificate register (docs/09 "Compliance certificates";
FC-01; FR-COMP-001..FR-COMP-030).

Inputs forbid unknown fields and trim text. These are the school's own certificates: no
personal data (the renewal owner is a member id and display name, as on tasks).
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

State = Literal["valid", "expiring", "expired", "no_expiry", "missing"]
Status = Literal["current", "superseded", "removed"]


def _nfc(value: str | None) -> str | None:
    if value is None:
        return None
    out = unicodedata.normalize("NFC", value).strip()
    return out or None


Text80 = Annotated[str | None, Field(default=None, max_length=80), AfterValidator(_nfc)]
Text120 = Annotated[str | None, Field(default=None, max_length=120), AfterValidator(_nfc)]
Text200 = Annotated[str | None, Field(default=None, max_length=200), AfterValidator(_nfc)]
Text500 = Annotated[str | None, Field(default=None, max_length=500), AfterValidator(_nfc)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class _Fields(_In):
    reference_no: Text80 = Field(
        default=None, description="The number printed on the certificate (1-80 characters)."
    )
    issued_by: Text200 = Field(default=None, description="The office that issued it.")
    valid_from: dt.date | None = None
    valid_until: dt.date | None = Field(
        default=None,
        description="The last day it is valid, as printed. Leave empty when it does not expire.",
    )
    document_id: uuid.UUID | None = Field(
        default=None,
        description="A copy in Documents (a clean PDF or scan you can see). Upload it as an "
        "'other' document of type certificate to also publish it on the CBSE disclosure.",
    )
    owner_membership_id: uuid.UUID | None = Field(
        default=None, description="Who renews it: an active member (reminders go to them)."
    )
    notes: Text500 = None

    @model_validator(mode="after")
    def _dates(self) -> _Fields:
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until must not be before valid_from")
        return self


class CertificateCreate(_Fields):
    """Record a certificate. When the school already has a current one of the same catalogue
    kind, this is its renewal: the old one is kept as history (``superseded``). For ``other``,
    send ``label`` and, to renew one, ``renews_id``."""

    kind: str = Field(max_length=63, pattern=r"^[a-z][a-z0-9_]{1,62}$")
    label: Text120 = Field(
        default=None, description="Only for kind 'other': the certificate's name (3-120)."
    )
    renews_id: uuid.UUID | None = Field(
        default=None, description="Only for kind 'other': the current certificate it renews."
    )


class CertificateUpdate(_Fields):
    """Correct the current certificate (the whole set of editable fields; send them all)."""

    label: Text120 = Field(default=None, description="Only for kind 'other'.")


class OwnerOut(BaseModel):
    membership_id: uuid.UUID
    display_name: str | None = Field(description="null when the member has left.")
    active: bool


class DocumentRefOut(BaseModel):
    id: uuid.UUID
    title: str | None = Field(description="null when the document is gone or hidden from you.")


class CertificateOut(_Out):
    id: uuid.UUID
    kind: str
    kind_label: str = Field(description="The catalogue label, or the school's own for 'other'.")
    label: str | None
    reference_no: str | None
    issued_by: str | None
    valid_from: dt.date | None
    valid_until: dt.date | None
    state: State
    days_left: int | None = Field(description="Days until it lapses (negative once expired).")
    document: DocumentRefOut | None
    owner: OwnerOut | None
    notes: str | None
    status: Status
    superseded_by: uuid.UUID | None
    recorded_by_name: str | None
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int


class KindOut(BaseModel):
    kind: str
    label: str
    authority: str
    typical_validity_months: int | None
    note: str
    disclosure_items: list[str] = Field(
        description="The CBSE Appendix IX items this certificate backs (CBSE schools)."
    )
    applies: bool = Field(description="It applies to this school's boards (expected).")


class RegisterRowOut(BaseModel):
    """One line of the register: an expected catalogue kind (``certificate`` null when the
    school has none: state ``missing``) or a current certificate."""

    kind: str
    kind_label: str
    state: State
    certificate: CertificateOut | None


class RegisterCountsOut(BaseModel):
    expired: int
    expiring: int
    missing: int
    valid: int
    no_expiry: int


class RegisterOut(BaseModel):
    rows: list[RegisterRowOut]
    counts: RegisterCountsOut
    kinds: list[KindOut] = Field(description="The catalogue (verified: false; typical values).")
    expiring_days: int
    verified: bool = Field(description="false: the catalogue is not checked against the rules.")
    today: dt.date
    owners: list[OwnerOut] = Field(
        default_factory=list,
        description="Active members a renewal can be given to (only for compliance.manage).",
    )


class CertificateDetailOut(CertificateOut):
    history: list[CertificateOut] = Field(description="Earlier certificates of the same kind.")


class RegisterSummaryOut(BaseModel):
    """Counts only, for the school dashboard (FR-COMP-014)."""

    expired: int
    expiring: int
    missing: int


__all__ = [
    "CertificateCreate",
    "CertificateDetailOut",
    "CertificateOut",
    "CertificateUpdate",
    "DocumentRefOut",
    "KindOut",
    "OwnerOut",
    "RegisterCountsOut",
    "RegisterOut",
    "RegisterRowOut",
    "RegisterSummaryOut",
    "State",
    "Status",
]
