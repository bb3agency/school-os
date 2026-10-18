"""Request and response bodies of the public routes (docs/09 §4 "Public"; ADR-0046).

Responses are explicit allowlists of fields (FR-PUB-009): nothing about a student, guardian,
phone, address or another school.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.public.config import ParentKind

ParentLanguage = Literal["en", "te"]
Rupees = Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
OptinCategory = Literal["notices", "attendance", "fees"]
LanguageChoice = Literal["en", "te", "school"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LinkExchangeIn(_In):
    """The token from ``/p/{token}`` (the BFF's exchange-and-redirect handler sends it here)."""

    token: str = Field(min_length=1, max_length=128)


class LinkExchangeOut(_Out):
    """What the BFF needs to set the ``__Host-sos_parent`` cookie (max-age = until
    ``expires_at``). Exchanging does not count as a view."""

    kind: ParentKind
    expires_at: dt.datetime


class ParentNoticeOut(_Out):
    """ADR-0046 decision 2 "Notice": the approved notice's title, body and date, and whether its
    PDF can be opened now (``GET /public/links/current/document``)."""

    title: str
    body: str
    date: dt.date | None
    has_pdf: bool


class ParentReceiptLineOut(_Out):
    label: str
    head_name: str
    amount: Rupees


class ParentReceiptOut(_Out):
    """ADR-0046 decision 2 "Receipt" (owner decision D22): the receipt card as printed."""

    receipt_number: str
    receipt_date: dt.date
    student_name: str
    class_section: str
    admission_no: str
    lines: list[ParentReceiptLineOut]
    amount: Rupees
    amount_in_words: str
    mode: str
    cancelled_on: dt.date | None
    has_pdf: bool


class ParentDueInstalmentOut(_Out):
    instalment_no: int
    label: str
    due_on: dt.date | None
    due: Rupees
    late_fee: Rupees
    overdue: bool


class ParentUpiOut(_Out):
    """The school's static UPI QR (SVG, no amount) and payee, when set (R2-2E)."""

    payee_name: str
    upi_id: str
    qr_svg: str


class ParentDuesOut(_Out):
    """ADR-0046 decision 2 "Dues": the child's first name and class only, what is due now per
    instalment (late fee shown separately), "Pay online" when the school takes online payments,
    the UPI QR when set and the office phone."""

    child_first_name: str
    class_name: str | None
    as_of: dt.date
    instalments: list[ParentDueInstalmentOut]
    total_due: Rupees
    late_fee_total: Rupees
    pay_online: bool
    upi: ParentUpiOut | None
    office_phone: str | None


class ParentOptinCategoryOut(_Out):
    category: OptinCategory
    status: Literal["opted_in", "opted_out", "not_asked"]
    stopped_by_parent: bool


class ParentOptinOut(_Out):
    """ADR-0046 decision 2 "Opt-in": the categories with the choice made and the guardian's
    message language (``school`` = the school's choice)."""

    categories: list[ParentOptinCategoryOut]
    language_choice: LanguageChoice
    school_language: ParentLanguage


class LinkEnvelopeOut(_Out):
    """The parent page for the cookie's link (``/p/view``): which page it is, until when it
    works, its language (the guardian's choice, else the school's parent language, else English:
    ADR-0044), the school's name, and exactly one content block for its kind (R2-3H)."""

    kind: ParentKind
    expires_at: dt.datetime
    language: ParentLanguage
    school_name: str
    notice: ParentNoticeOut | None = None
    receipt: ParentReceiptOut | None = None
    dues: ParentDuesOut | None = None
    optin: ParentOptinOut | None = None


class ParentDocumentLinkOut(_Out):
    """A presigned GET of the notice or receipt PDF, valid at most 5 minutes; the BFF redirects
    the parent to it (303) and never shows the address."""

    url: str
    expires_at: dt.datetime


class ParentPayIn(_In):
    """The instalments the parent ticked on the dues page."""

    instalments: list[int] = Field(min_length=1, max_length=24)


class ParentPayOut(_Out):
    """Where to send the parent: the school's own Razorpay payment page (FR-PUB-010)."""

    redirect_url: str
    amount: Rupees
    reused: bool


class ParentPaymentStatusOut(_Out):
    """The return page's status of one payment link (``created`` = not paid yet)."""

    status: Literal["created", "paid", "expired", "cancelled", "failed"]
    amount: Rupees
    receipt_issued: bool


class ParentOptinIn(_In):
    """The parent's message language from the opt-in page (ADR-0044 decision 4)."""

    language: LanguageChoice


__all__ = [
    "LinkEnvelopeOut",
    "LinkExchangeIn",
    "LinkExchangeOut",
    "ParentDocumentLinkOut",
    "ParentDueInstalmentOut",
    "ParentDuesOut",
    "ParentLanguage",
    "ParentNoticeOut",
    "ParentOptinCategoryOut",
    "ParentOptinIn",
    "ParentOptinOut",
    "ParentPayIn",
    "ParentPayOut",
    "ParentPaymentStatusOut",
    "ParentReceiptLineOut",
    "ParentReceiptOut",
    "ParentUpiOut",
]
