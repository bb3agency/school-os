"""Tuition fee certificate routes (docs/09 "Fees: tuition fee certificates"; FM-06;
FR-FEE-046..060).

Every route declares its guard. Fee permissions are school-wide only (FR-FEE-024): issuing needs
``fee.collect`` (office staff, office admin, accountant, principal); reads take ``fee.collect``
or ``finance.read`` and are re-checked school-wide by the service. While fees are off for the
school every route answers 404 ``module_hidden``. Filters are ids and codes only.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import HTMLResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import Cursor, Limit, Page
from app.fees import service
from app.fees.tax_schemas import (
    CertificateStatus,
    FinancialYear,
    TaxCertificateBulkIssue,
    TaxCertificateBulkOut,
    TaxCertificateDownloadOut,
    TaxCertificateIssue,
    TaxCertificateListItem,
    TaxCertificateOut,
)
from app.fees.tax_templates import STYLE_CSP

router = APIRouter(prefix="/api/v1", tags=["fees"])

Viewer = Annotated[UserContext, Depends(require_any(service.COLLECT, service.READ))]
Collector = Annotated[UserContext, Depends(require(service.COLLECT, scope="school"))]

_PAGE: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/html": {}}, "description": "Print-ready A4 page (no scripts)"}
}


@router.post("/fees/tax-certificates", response_model=TaxCertificateOut, status_code=201)
def issue_tax_certificate(
    ctx: Collector, db: TenantDB, body: TaxCertificateIssue, response: Response
) -> TaxCertificateOut:
    """Issue the tuition fee certificate of one student for one financial year (``fee.collect``;
    FR-FEE-049). Repeating it returns the issued certificate (``outcome: unchanged``) while the
    receipts and amounts are the same; otherwise the earlier one is superseded (``reissued``).
    Errors: 404 (student), 409 ``no_tuition_paid``, ``too_many_receipts``, 422
    ``financial_year``."""
    out = service.issue_tax_certificate(db, ctx, body)
    response.headers["Location"] = f"/api/v1/fees/tax-certificates/{out.id}"
    return out


@router.post("/fees/tax-certificates/bulk", response_model=TaxCertificateBulkOut)
def issue_tax_certificates_bulk(
    ctx: Collector, db: TenantDB, body: TaxCertificateBulkIssue
) -> TaxCertificateBulkOut:
    """Issue the certificates of one class or section of the current year (``fee.collect``;
    FR-FEE-050). Students without tuition paid in the year are skipped. 409
    ``too_many_students``."""
    return service.issue_tax_certificates_bulk(db, ctx, body)


@router.get("/fees/tax-certificates", response_model=Page[TaxCertificateListItem])
def list_tax_certificates(
    *,
    ctx: Viewer,
    db: TenantDB,
    financial_year: Annotated[FinancialYear | None, Query()] = None,
    student_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    section_id: uuid.UUID | None = None,
    status: Annotated[CertificateStatus | None, Query()] = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[TaxCertificateListItem]:
    """Certificates, newest first (``fee.collect`` or ``finance.read``; FR-FEE-055)."""
    return service.list_tax_certificates(
        db,
        ctx,
        financial_year=financial_year,
        student_id=student_id,
        class_id=class_id,
        section_id=section_id,
        status=status,
        limit=limit,
        cursor=cursor,
    )


@router.get("/fees/tax-certificates/{tax_certificate_id}", response_model=TaxCertificateOut)
def get_tax_certificate(
    ctx: Viewer, db: TenantDB, tax_certificate_id: uuid.UUID
) -> TaxCertificateOut:
    return service.get_tax_certificate(db, ctx, tax_certificate_id)


@router.get(
    "/fees/tax-certificates/{tax_certificate_id}/print",
    response_class=HTMLResponse,
    responses=_PAGE,
)
def print_tax_certificate(ctx: Viewer, db: TenantDB, tax_certificate_id: uuid.UUID) -> HTMLResponse:
    """The certificate as a print-ready A4 page; a superseded one is marked. Audited."""
    page = service.tax_certificate_print_page(db, ctx, tax_certificate_id)
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": STYLE_CSP,
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": (
                f'inline; filename="tuition-certificate-{str(tax_certificate_id)[:8]}.html"'
            ),
        },
    )


@router.get(
    "/fees/tax-certificates/{tax_certificate_id}/download-url",
    response_model=TaxCertificateDownloadOut,
)
def tax_certificate_download_url(
    ctx: Viewer, db: TenantDB, tax_certificate_id: uuid.UUID
) -> TaxCertificateDownloadOut:
    """A short-lived link to the stored PDF. 409 ``pdf_not_ready``."""
    return service.tax_certificate_download_url(db, ctx, tax_certificate_id)


@router.post("/fees/tax-certificates/{tax_certificate_id}/render", response_model=TaxCertificateOut)
def render_tax_certificate(
    ctx: Collector, db: TenantDB, tax_certificate_id: uuid.UUID
) -> TaxCertificateOut:
    """Make the PDF again after it failed (``fee.collect``)."""
    return service.retry_tax_certificate_render(db, ctx, tax_certificate_id)


__all__ = ["router"]
