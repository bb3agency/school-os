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

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import Cursor, IdempotencyDep, IfMatch, Limit, Page, etag
from app.certificates import service
from app.certificates.schemas import (
    ApproveIn,
    CertificateFormatOut,
    CertificateOut,
    CertificatePreview,
    CertificateRequest,
    CertificateType,
    CertificateTypeOut,
    DownloadUrlOut,
    DuplicateRequest,
    PublicCertificateOut,
    ReasonIn,
    Status,
    VerificationOut,
    VerificationReasonIn,
)
from app.certificates.templates import STYLE_CSP
from app.core import ratelimit
from app.public.guards import PublicCaller, PublicNotFound, require_public_handle

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
def list_certificate_types(ctx: Reader, db: TenantDB) -> list[CertificateTypeOut]:
    """Certificate types with the values each one asks for; the transfer certificate in the
    school's chosen format (read, issue or approve permission; FR-CERT-015)."""
    return service.types_catalog(db)


@router.get("/certificates/formats", response_model=list[CertificateFormatOut])
def list_certificate_formats(ctx: Reader) -> list[CertificateFormatOut]:
    """The transfer certificate formats a school may choose (``PATCH /tenant`` with
    ``settings.certificate_format.transfer``): lines in print order, inputs, sources and whether
    a school confirmed the layout (``verified``; owner decision D3). Read, issue or approve
    permission (FR-CERT-015)."""
    return service.formats_catalog()


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
    body: ApproveIn,
) -> CertificateOut:
    """Approve and issue a transfer certificate (or a TC duplicate): serial number, register
    entry and, for a TC, the student leaves the rolls (permission ``certificate.approve``, MFA
    within 5 minutes, not the person who prepared it, ``If-Match``). The body carries the
    ``draft_sha256`` read from ``GET /certificates/{id}``. Errors:
    ``self_approval_forbidden`` (403), ``step_up_required`` (428), ``certificate_not_pending``
    / ``certificate_blocked`` / ``change_request_pending`` / ``requester_inactive`` /
    ``certificate_draft_changed`` (409: what it prints changed since it was read)."""
    out = service.approve(db, ctx, certificate_id, body, expected_version=expected)
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
    """The certificate as a print-ready A4 page in English (and Telugu only while Telugu is
    shown, ADR-0036; a DRAFT while it waits for approval; read, issue or approve permission).
    The view is audited."""
    page = service.print_page(db, ctx, certificate_id)
    return _html(page, f"certificate-{str(certificate_id)[:8]}.html")


@router.get("/certificates/{certificate_id}/download-url", response_model=DownloadUrlOut)
def certificate_download_url(
    ctx: Downloader, db: TenantDB, certificate_id: uuid.UUID
) -> DownloadUrlOut:
    """A link to download the certificate PDF, valid for at most 5 minutes (permission
    ``certificate.read``). Error ``pdf_not_ready`` / ``document_not_ready`` (409) while it is
    being made or checked; ``certificate_cancelled`` (409) once it, or the original it copies,
    was cancelled (print it from the print view, marked CANCELLED). Audited."""
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


# --- verification codes (R2; ADR-0045; FR-CERT-019..024) -------------------------------------


def _verification_etag(response: Response, item: VerificationOut) -> VerificationOut:
    response.headers["ETag"] = etag(item.version)
    return item


@router.get("/certificates/{certificate_id}/verification", response_model=VerificationOut)
def get_certificate_verification(
    ctx: Reader, db: TenantDB, certificate_id: uuid.UUID, response: Response
) -> VerificationOut:
    """The certificate's verification code (as printed, in four groups), the QR link, how
    often it was checked online and when last (US-2004 AC1; read, issue or approve permission).
    ``ETag`` is the version a withdrawal sends in ``If-Match``."""
    return _verification_etag(response, service.get_verification(db, ctx, certificate_id))


@router.post(
    "/certificates/{certificate_id}/verification",
    response_model=VerificationOut,
    status_code=201,
)
def add_certificate_verification(
    ctx: Approver,
    db: TenantDB,
    certificate_id: uuid.UUID,
    body: VerificationReasonIn,
    idem: IdempotencyDep,
) -> Response:
    """Add a verification code to a certificate issued before R2 (FR-CERT-023a; permission
    ``certificate.approve``, MFA within 5 minutes; reason 10..500). The original paper has no
    code: the next duplicate carries one. Accepts ``Idempotency-Key``. Errors:
    ``certificate_not_issued``, ``verification_exists`` (409)."""
    return idem.run(
        db,
        body,
        lambda: service.add_verification(db, ctx, certificate_id, body),
        headers=lambda item: {"ETag": etag(item.version)},
    )


@router.post("/certificates/{certificate_id}/verification/withdraw", response_model=VerificationOut)
def withdraw_certificate_verification(
    *,
    ctx: Approver,
    db: TenantDB,
    certificate_id: uuid.UUID,
    expected: IfMatch,
    body: VerificationReasonIn,
    response: Response,
) -> VerificationOut:
    """Withdraw the certificate's verification code (FR-CERT-023b; permission
    ``certificate.approve``, MFA within 5 minutes, ``If-Match`` from ``GET .../verification``;
    reason 10..500): the public page answers "not found" for it from then on. Error
    ``verification_not_active`` (409)."""
    out = service.withdraw_verification(db, ctx, certificate_id, body, expected_version=expected)
    return _verification_etag(response, out)


# --- public verification (R2; pinned in app/public/public_routes.yaml) -----------------------

public_router = APIRouter(prefix="/api/v1", tags=["public"])

VERIFY_BUDGET = "cert_verify_ip"


def _verify_budget(request: Request) -> None:
    """Every lookup from one address counts before anything is resolved (ADR-0045 decision 7:
    20 per 10 minutes; failures also spend ``public_lookup_fail`` in the guard). Charged first,
    so a used-up budget answers 429 for valid and invalid codes alike (no oracle)."""
    ratelimit.charge(request, VERIFY_BUDGET, ratelimit.ip_hash(ratelimit.client_ip_of(request)))


ByCode = Annotated[
    PublicCaller, Depends(require_public_handle("cert_verify", key="path", param="code"))
]

_PUBLIC_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"description": "Unknown, mistyped or withdrawn code (always the same body)."},
    429: {"description": "Too many lookups from this address."},
}


@public_router.get(
    "/public/certificates/{code}",
    response_model=PublicCertificateOut,
    responses=_PUBLIC_RESPONSES,
    dependencies=[Depends(_verify_budget)],
)
async def verify_certificate(
    request: Request, code: str, caller: ByCode, response: Response
) -> PublicCertificateOut:
    """Check a certificate by its verification code (FR-CERT-021, FR-CERT-022; ADR-0045),
    called by the web app's verification page with its service token and no user token. The
    caller sends the normalised code (16 characters, upper case, no dashes). A valid code
    answers the school's printed name and place, the type, serial, date of issue and exactly the
    printed content (a cancelled certificate: no content); anything else is the uniform 404.
    Each check is counted and audited. There is no lookup by serial, name or date of birth."""
    del code  # read by the guard
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    ip_hash = ratelimit.ip_hash(ratelimit.client_ip_of(request))
    out = await run_in_threadpool(
        service.verify_publicly, caller.tenant_id, caller.handle_id, ip_hash=ip_hash
    )
    if out is None:
        raise PublicNotFound()
    return out
