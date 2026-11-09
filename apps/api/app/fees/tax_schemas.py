"""Pydantic v2 IO models of the tuition fee certificate (FM-06; FR-FEE-046..060; docs/09
"Fees: tuition fee certificates").

Inputs forbid unknown fields. Money is :class:`decimal.Decimal` rupees, strings out. Outputs carry
personal financial data (C2): only fee permissions reach them (FR-FEE-024).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.fees.schemas import Money, PdfStatus

FinancialYear = Annotated[str, StringConstraints(pattern=r"^20[0-9]{2}-[0-9]{2}$")]
CertificateStatus = Literal["issued", "superseded"]
IssueOutcome = Literal["issued", "reissued", "unchanged"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


class TaxCertificateIssue(_In):
    """Issue the certificate of one student for one financial year (``2026-27``, April-March)."""

    student_id: uuid.UUID
    financial_year: FinancialYear


class TaxCertificateBulkIssue(_In):
    """Issue the certificates of one class or one section (current year), exactly one of them.
    Students without tuition paid in the year are skipped."""

    financial_year: FinancialYear
    class_id: uuid.UUID | None = None
    section_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one_target(self) -> TaxCertificateBulkIssue:
        if (self.class_id is None) == (self.section_id is None):
            raise ValueError("give exactly one of class_id and section_id")
        return self


class TaxCertificateReceiptOut(_Out):
    receipt_number: str
    receipt_date: dt.date
    amount: Money
    refunded: Money


class TaxCertificateOut(_Out):
    id: uuid.UUID
    serial_number: str
    financial_year: str
    as_of: dt.date
    in_progress: bool
    student_id: uuid.UUID
    student_name: str
    admission_no: str
    class_section: str
    paid: Money
    refunded: Money
    amount: Money
    amount_in_words: str
    receipts: list[TaxCertificateReceiptOut]
    head_names: list[str]
    status: CertificateStatus
    superseded_by: uuid.UUID | None
    issued_by: uuid.UUID
    issued_by_name: str | None
    issued_at: dt.datetime
    pdf_status: PdfStatus
    content_sha256: str
    legal_verified: bool
    outcome: IssueOutcome | None = Field(
        default=None,
        description=(
            "Only on issue: ``issued`` (new), ``reissued`` (the amounts changed: the earlier "
            "certificate is superseded and keeps its serial) or ``unchanged`` (the issued "
            "certificate already shows these amounts and is returned as it is)."
        ),
    )


class TaxCertificateListItem(_Out):
    id: uuid.UUID
    serial_number: str
    financial_year: str
    as_of: dt.date
    student_id: uuid.UUID
    student_name: str
    class_section: str
    amount: Money
    receipt_count: int
    status: CertificateStatus
    issued_at: dt.datetime
    pdf_status: PdfStatus


class TaxCertificateBulkOut(_Out):
    financial_year: str
    students: int
    issued: int
    reissued: int
    unchanged: int
    skipped_no_tuition: int
    skipped_too_many_receipts: int = 0
    certificate_ids: list[uuid.UUID]


class TaxCertificateDownloadOut(_Out):
    url: str
    expires_at: dt.datetime
    filename: str


__all__ = [
    "CertificateStatus",
    "FinancialYear",
    "IssueOutcome",
    "TaxCertificateBulkIssue",
    "TaxCertificateBulkOut",
    "TaxCertificateDownloadOut",
    "TaxCertificateIssue",
    "TaxCertificateListItem",
    "TaxCertificateOut",
    "TaxCertificateReceiptOut",
]
