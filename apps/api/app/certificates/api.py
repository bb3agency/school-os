"""Certificate and register routes (docs/09 Certificates and registers; US-1101..US-1108,
FR-CERT-*, FR-REG-*).

Every route declares its guard: ``require()`` for issuing (``certificate.issue``), deciding and
cancelling (``certificate.approve``, step-up MFA within 5 minutes) and the register print views
(``register.read``, step-up, school-wide only); :func:`require_any` for the reads that the
reader, the maker and the checker share. The service applies the student scope to every object
(404 outside scope or school) and enforces maker != checker and the certificate's state.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import HTMLResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import Cursor, IdempotencyDep, IfMatch, Limit, Page, etag
from app.certificates import service
from app.certificates.schemas import (
    ApproveIn,
    CertificateOut,
    CertificatePreview,
    CertificateRequest,
    CertificateType,
    CertificateTypeOut,
    DownloadUrlOut,
    DuplicateRequest,
    ReasonIn,
    Status,
)
from app.certificates.templates import STYLE_CSP

router = APIRouter(prefix="/api/v1", tags=["certificates"])

Reader = Annotated[UserContext, Depends(require_any(service.READ, service.ISSUE, service.APPROVE))]
Downloader = Annotated[UserContext, Depends(require(service.READ))]
Issuer = Annotated[UserContext, Depends(require(service.ISSUE))]
Approver = Annotated[UserContext, Depends(require(service.APPROVE, step_up=True))]
RegisterReader = Annotated[
    UserContext, Depends(require(service.REGISTER, scope="school", step_up=True))
]

_HTML_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": STYLE_CSP,
    "X-Content-Type-Options": "nosniff",
}


def _html(page: str, filename: str) -> HTMLResponse:
    return HTMLResponse(
        page, headers={**_HTML_HEADERS, "Content-Disposition": f'inline; filename="{filename}"'}
    )


def _headers(item: CertificateOut) -> dict[str, str]:
    return {"Location": f"/api/v1/certificates/{item.id}", "ETag": etag(item.version)}


def _with_etag(response: Response, item: CertificateOut) -> CertificateOut:
    response.headers["ETag"] = etag(item.version)
    return item


_PAGE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/html": {}}, "description": "Print-ready A4 page"}
}
_REGISTER_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_PAGE_RESPONSES,
    204: {"description": "With check=true: the register can be printed now (nothing audited)"},
}
RegisterCheck = Annotated[
    bool,
    Query(
        description="true: only check that the register can be printed now (step-up, year, "
        "type, size) and answer 204 without the page; the print view itself is audited once."
    ),
]


def _register(
    check: bool, page: Callable[[], str], verify: Callable[[], None], filename: str
) -> Response:
    if check:
        verify()
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return _html(page(), filename)


@router.get("/certificates/types", response_model=list[CertificateTypeOut])
def list_certificate_types(ctx: Reader) -> list[CertificateTypeOut]:
    """Certificate types with the values each one asks for (read, issue or approve
    permission)."""
    return service.types_catalog()


@router.get("/students/{student_id}/certificates/preview", response_model=CertificatePreview)
def preview_certificate(
    ctx: Issuer,
    db: TenantDB,
    student_id: uuid.UUID,
    certificate_type: Annotated[CertificateType, Query()],
) -> CertificatePreview:
    """What the certificate would print now, where each value comes from, and anything that
    stops it being issued (open blocker findings, empty required fields; permission
    ``certificate.issue``). Nothing is saved."""
    return service.preview(db, ctx, student_id, certificate_type)


@router.post("/students/{student_id}/certificates", response_model=CertificateOut, status_code=201)
def request_certificate(
    ctx: Issuer,
    db: TenantDB,
    student_id: uuid.UUID,
    body: CertificateRequest,
    idem: IdempotencyDep,
) -> Response:
    """Issue a bonafide, study or conduct certificate, or prepare a transfer certificate for the
    principal's approval (permission ``certificate.issue``). Accepts ``Idempotency-Key``.
    Errors: ``certificate_blocked``, ``transfer_certificate_exists``,
    ``no_current_academic_year`` (409); input errors (422)."""
    return idem.run(
        db,
        body,
        lambda: service.request_certificate(db, ctx, student_id, body),
        headers=_headers,
    )


@router.get("/certificates", response_model=Page[CertificateOut])
def list_certificates(
    *,
    ctx: Reader,
    db: TenantDB,
    student_id: uuid.UUID | None = None,
    certificate_type: CertificateType | None = None,
    status: Annotated[Status | None, Query()] = None,
    academic_year_id: uuid.UUID | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[CertificateOut]:
    """Certificates and requests of students you can see, newest first (read, issue or approve
    permission)."""
    return service.list_certificates(
        db,
        ctx,
        student_id=student_id,
        certificate_type=certificate_type,
        status=status,
        academic_year_id=academic_year_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/certificates/{certificate_id}", response_model=CertificateOut)
def get_certificate(
    ctx: Reader, db: TenantDB, certificate_id: uuid.UUID, response: Response
) -> CertificateOut:
    """One certificate with its printed values once issued; returns ``ETag`` for the decision
    calls (read, issue or approve permission)."""
    return _with_etag(response, service.get_certificate(db, ctx, certificate_id))


@router.post("/certificates/{certificate_id}/approve", response_model=CertificateOut)
def approve_certificate(
    *,
    ctx: Approver,
    db: TenantDB,
    certificate_id: uuid.UUID,
    expected: IfMatch,
    response: Response,
    body: ApproveIn | None = None,
) -> CertificateOut:
    """Approve and issue a transfer certificate (or a TC duplicate): serial number, register
    entry and, for a TC, the student leaves the rolls (permission ``certificate.approve``, MFA
    within 5 minutes, not the person who prepared it, ``If-Match``). Errors:
    ``self_approval_forbidden`` (403), ``step_up_required`` (428), ``certificate_not_pending``
    / ``certificate_blocked`` (409)."""
    out = service.approve(db, ctx, certificate_id, body or ApproveIn(), expected_version=expected)
    return _with_etag(response, out)


@router.post("/certificates/{certificate_id}/reject", response_model=CertificateOut)
def reject_certificate(
    *,
    ctx: Approver,
    db: TenantDB,
    certificate_id: uuid.UUID,
    expected: IfMatch,
    body: ReasonIn,
    response: Response,
) -> CertificateOut:
    """Reject a request with a reason the requester will see (permission
    ``certificate.approve``, MFA within 5 minutes, ``If-Match``)."""
    out = service.reject(db, ctx, certificate_id, body, expected_version=expected)
    return _with_etag(response, out)


@router.post("/certificates/{certificate_id}/withdraw", response_model=CertificateOut)
def withdraw_certificate(
    *,
    ctx: Issuer,
    db: TenantDB,
    certificate_id: uuid.UUID,
    expected: IfMatch,
    response: Response,
) -> CertificateOut:
    """Withdraw your own request while it waits for approval (permission
    ``certificate.issue``, ``If-Match``). Error ``not_requester`` (403)."""
    out = service.withdraw(db, ctx, certificate_id, expected_version=expected)
    return _with_etag(response, out)


@router.post("/certificates/{certificate_id}/cancel", response_model=CertificateOut)
def cancel_certificate(
    *,
    ctx: Approver,
    db: TenantDB,
    certificate_id: uuid.UUID,
    expected: IfMatch,
    body: ReasonIn,
    response: Response,
) -> CertificateOut:
    """Cancel an issued certificate with a reason: it keeps its number and the registers show
    it as cancelled; a TC does not re-admit the student (permission ``certificate.approve``,
    MFA within 5 minutes, ``If-Match``). Error ``certificate_not_issued`` (409)."""
    out = service.cancel(db, ctx, certificate_id, body, expected_version=expected)
    return _with_etag(response, out)


@router.post(
    "/certificates/{certificate_id}/duplicates", response_model=CertificateOut, status_code=201
)
def request_duplicate(
    ctx: Issuer,
    db: TenantDB,
    certificate_id: uuid.UUID,
    body: DuplicateRequest,
    idem: IdempotencyDep,
) -> Response:
    """Issue a duplicate of an issued certificate, marked DUPLICATE with the original serial
    number (a TC duplicate waits for approval; permission ``certificate.issue``). Accepts
    ``Idempotency-Key``. Errors: ``certificate_not_issued``, ``duplicate_pending`` (409)."""
    return idem.run(
        db,
        body,
        lambda: service.request_duplicate(db, ctx, certificate_id, body),
        headers=_headers,
    )


@router.post("/certificates/{certificate_id}/render", response_model=CertificateOut)
def retry_certificate_pdf(
    ctx: Issuer, db: TenantDB, certificate_id: uuid.UUID, response: Response
) -> CertificateOut:
    """Make the PDF again after it failed (permission ``certificate.issue``)."""
    return _with_etag(response, service.retry_render(db, ctx, certificate_id))


@router.get(
    "/certificates/{certificate_id}/print",
    response_class=HTMLResponse,
    responses=_PAGE_RESPONSES,
)
def print_certificate(ctx: Reader, db: TenantDB, certificate_id: uuid.UUID) -> HTMLResponse:
    """The certificate as a print-ready A4 page in English and Telugu (a DRAFT while it waits
    for approval; read, issue or approve permission). The view is audited."""
    page = service.print_page(db, ctx, certificate_id)
    return _html(page, f"certificate-{str(certificate_id)[:8]}.html")


@router.get("/certificates/{certificate_id}/download-url", response_model=DownloadUrlOut)
def certificate_download_url(
    ctx: Downloader, db: TenantDB, certificate_id: uuid.UUID
) -> DownloadUrlOut:
    """A link to download the certificate PDF, valid for at most 5 minutes (permission
    ``certificate.read``). Error ``pdf_not_ready`` / ``document_not_ready`` (409) while it is
    being made or checked. Audited."""
    return service.download_url(db, ctx, certificate_id)


@router.get(
    "/registers/transfer-certificates",
    response_class=HTMLResponse,
    responses=_REGISTER_RESPONSES,
)
def transfer_certificate_register(
    ctx: RegisterReader,
    db: TenantDB,
    academic_year_id: uuid.UUID | None = None,
    check: RegisterCheck = False,
) -> Response:
    """The TC register (counterfoil) of an academic year (default: the current one) as an A4
    landscape print page (permission ``register.read``, school-wide, MFA within 5 minutes).
    Audited (``register.viewed``); with ``check=true`` 204 only, not audited."""
    kw: dict[str, Any] = {"kind": "transfer", "academic_year_id": academic_year_id}
    return _register(
        check,
        lambda: service.register_page(db, ctx, **kw),
        lambda: service.check_register(db, ctx, **kw),
        "tc-register.html",
    )


@router.get("/registers/certificates", response_class=HTMLResponse, responses=_REGISTER_RESPONSES)
def certificate_register(
    ctx: RegisterReader,
    db: TenantDB,
    academic_year_id: uuid.UUID | None = None,
    certificate_type: CertificateType | None = None,
    check: RegisterCheck = False,
) -> Response:
    """The certificate issue register (bonafide, study and conduct certificates, or one of them)
    of an academic year as an A4 landscape print page (permission ``register.read``,
    school-wide, MFA within 5 minutes). Audited (``register.viewed``); with ``check=true`` 204
    only, not audited."""
    kw: dict[str, Any] = {
        "kind": "certificates",
        "academic_year_id": academic_year_id,
        "certificate_type": certificate_type,
    }
    return _register(
        check,
        lambda: service.register_page(db, ctx, **kw),
        lambda: service.check_register(db, ctx, **kw),
        "certificate-register.html",
    )


@router.get(
    "/registers/admission-withdrawal",
    response_class=HTMLResponse,
    responses=_REGISTER_RESPONSES,
)
def admission_withdrawal_register(
    ctx: RegisterReader,
    db: TenantDB,
    academic_year_id: uuid.UUID | None = None,
    check: RegisterCheck = False,
) -> Response:
    """The admission and withdrawal register of the students enrolled in an academic year, in
    admission-number order, as an A4 landscape print page (permission ``register.read``,
    school-wide, MFA within 5 minutes). Audited (``register.viewed``); with ``check=true`` 204
    only, not audited."""
    return _register(
        check,
        lambda: service.admission_register_page(db, ctx, academic_year_id=academic_year_id),
        lambda: service.check_register(
            db, ctx, kind="admission", academic_year_id=academic_year_id
        ),
        "admission-withdrawal-register.html",
    )
