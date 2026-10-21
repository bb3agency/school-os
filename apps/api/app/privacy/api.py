"""Privacy routes: notices, data-principal requests and grievances, access reports, erasure and
record classes (docs/09 "R3: privacy"; ADR-0048; US-2601..US-2609; FR-DPDP-001..018).

Every route declares its guard: ``privacy.read`` for reads (the service applies the reader's
student reach: requests about students outside it are 404), ``privacy.request.manage`` for
logging and working on requests and recording notices, ``privacy.request.decide`` (step-up) for
releasing reports and erasing. Closing and downloading take either permission; the service checks
which one the request's kind needs (maker-checker, step-up). No personal data in URLs: request
searches are ``POST``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Response
from fastapi.responses import HTMLResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import IdempotencyDep, IfMatch, etag
from app.privacy import service
from app.privacy.config import LetterKey, TemplateKey
from app.privacy.render import STYLE_CSP
from app.privacy.schemas import (
    PrivacyCloseIn,
    PrivacyConsentWithdrawalIn,
    PrivacyDownloadOut,
    PrivacyErasureIn,
    PrivacyErasurePlanOut,
    PrivacyErasureResultOut,
    PrivacyLinkIn,
    PrivacyModuleFileOut,
    PrivacyNoticeIn,
    PrivacyNoticeOut,
    PrivacyRecordClassesOut,
    PrivacyRequestCreate,
    PrivacyRequestOut,
    PrivacyRequestPageOut,
    PrivacyRequestPatch,
    PrivacyRequestSearchIn,
    PrivacyTemplateOut,
)

router = APIRouter(prefix="/api/v1", tags=["privacy"])

KeyParam = Annotated[TemplateKey, Path(max_length=40, description="Notice template key.")]
LetterParam = Annotated[LetterKey, Path(max_length=40, description="Letter key.")]
Reader = Annotated[UserContext, Depends(require(service.READ))]
Manager = Annotated[UserContext, Depends(require(service.MANAGE))]
Decider = Annotated[UserContext, Depends(require(service.DECIDE, scope="school", step_up=True))]
Worker = Annotated[UserContext, Depends(require_any(service.MANAGE, service.DECIDE))]

_HTML_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": STYLE_CSP,
    "X-Content-Type-Options": "nosniff",
}
_PAGE: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/html": {}}, "description": "Print-ready A4 page"}
}


def _html(page: str, filename: str) -> HTMLResponse:
    return HTMLResponse(
        page, headers={**_HTML_HEADERS, "Content-Disposition": f'inline; filename="{filename}"'}
    )


def _request_headers(item: PrivacyRequestOut) -> dict[str, str]:
    return {"Location": f"/api/v1/privacy/requests/{item.id}", "ETag": etag(item.version)}


# --- templates and notices --------------------------------------------------------------------


@router.get("/privacy/templates", response_model=list[PrivacyTemplateOut])
def list_templates(ctx: Reader, db: TenantDB) -> list[PrivacyTemplateOut]:
    """The parents' and staff notice templates: version, placeholders the school has not filled,
    and whether a newer version exists than the last one issued (FR-DPDP-002, FR-DPDP-003;
    permission ``privacy.read``)."""
    return service.list_templates(db)


@router.get("/privacy/templates/{key}/preview", response_class=HTMLResponse, responses=_PAGE)
def preview_template(ctx: Reader, db: TenantDB, key: KeyParam) -> HTMLResponse:
    """The notice filled from the school's settings as an A4 page; unfilled placeholders print
    "[not set]" (FR-DPDP-002; permission ``privacy.read``). Not legal advice."""
    return _html(service.preview_template(db, key), f"privacy-{key}.html")


@router.post("/privacy/templates/{key}/pdf", response_model=PrivacyModuleFileOut, status_code=202)
def notice_pdf(ctx: Reader, db: TenantDB, key: KeyParam) -> PrivacyModuleFileOut:
    """Make the filled notice into an A4 PDF in the background (FR-DPDP-002; permission
    ``privacy.read``). Poll ``GET /privacy/files/{export_id}``."""
    return service.request_notice_pdf(db, ctx, key)


@router.get("/privacy/files/{export_id}", response_model=PrivacyModuleFileOut)
def notice_pdf_state(ctx: Reader, db: TenantDB, export_id: uuid.UUID) -> PrivacyModuleFileOut:
    """The state of a notice PDF (permission ``privacy.read``)."""
    return service.notice_pdf_state(db, export_id)


@router.get("/privacy/files/{export_id}/download-url", response_model=PrivacyDownloadOut)
def notice_pdf_download(ctx: Reader, db: TenantDB, export_id: uuid.UUID) -> PrivacyDownloadOut:
    """A link (5 minutes) to a ready notice PDF (permission ``privacy.read``). Audited."""
    return service.notice_pdf_download(db, export_id)


@router.get("/privacy/notices", response_model=list[PrivacyNoticeOut])
def list_notices(ctx: Reader, db: TenantDB) -> list[PrivacyNoticeOut]:
    """Every notice issue the school recorded, newest first (FR-DPDP-003; permission
    ``privacy.read``)."""
    return service.list_notices(db)


@router.post("/privacy/notices", response_model=PrivacyNoticeOut, status_code=201)
def record_notice(
    ctx: Manager, db: TenantDB, body: PrivacyNoticeIn, idem: IdempotencyDep
) -> Response:
    """Record that the school issued a notice: template and version, audience, language, date,
    how it was issued and optionally the final document (FR-DPDP-003; permission
    ``privacy.request.manage``; ``Idempotency-Key``). Append-only. Errors (422):
    ``audience_mismatch``, ``unknown_version``, ``date_in_future``, ``not_found``."""
    return idem.run(db, body, lambda: service.record_notice(db, ctx, body))


@router.get("/privacy/record-classes", response_model=PrivacyRecordClassesOut)
def record_classes(ctx: Reader) -> PrivacyRecordClassesOut:
    """Every kind of record SchoolOS keeps for the school with its retention, basis and erasure
    rule, read-only (FR-DPDP-017; permission ``privacy.read``)."""
    return service.record_classes()


# --- requests ---------------------------------------------------------------------------------


@router.post("/privacy/requests/search", response_model=PrivacyRequestPageOut)
def search_requests(
    ctx: Reader, db: TenantDB, body: PrivacyRequestSearchIn
) -> PrivacyRequestPageOut:
    """Requests and complaints you may see, open ones first by due date (FR-DPDP-005,
    FR-DPDP-006; permission ``privacy.read``; requests about students outside your reach are
    left out). ``POST`` so that no personal data goes into the URL."""
    return service.search_requests(db, ctx, body)


@router.post("/privacy/requests", response_model=PrivacyRequestOut, status_code=201)
def create_request(
    ctx: Manager, db: TenantDB, body: PrivacyRequestCreate, idem: IdempotencyDep
) -> Response:
    """Log a request or complaint about one student, guardian or staff member: who asked, how
    they were checked, what they asked (encrypted, never logged). Due in the school's target
    (at most 7 days; FR-DPDP-005..007; permission ``privacy.request.manage``;
    ``Idempotency-Key``). Errors (422): ``subject_required``, ``nomination_adults_only``,
    ``date_in_future``, ``aadhaar_full_number_rejected``."""
    return idem.run(
        db, body, lambda: service.create_request(db, ctx, body), headers=_request_headers
    )


@router.get("/privacy/requests/{request_id}", response_model=PrivacyRequestOut)
def get_request(
    ctx: Reader, db: TenantDB, request_id: uuid.UUID, response: Response
) -> PrivacyRequestOut:
    """One request with its texts, timeline, links and report state (permission
    ``privacy.read``; 404 outside your reach). ``ETag`` for ``If-Match``."""
    out = service.get_request(db, ctx, request_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.patch("/privacy/requests/{request_id}", response_model=PrivacyRequestOut)
def update_request(
    *,
    ctx: Manager,
    db: TenantDB,
    request_id: uuid.UUID,
    body: PrivacyRequestPatch,
    version: IfMatch,
    response: Response,
) -> PrivacyRequestOut:
    """Change the status, notes, verification or response, or withdraw a nomination
    (permission ``privacy.request.manage``; ``If-Match``). Errors: ``request_closed`` (409),
    stale ``If-Match`` (412)."""
    out = service.update_request(db, ctx, request_id, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get(
    "/privacy/requests/{request_id}/letters/{letter}",
    response_class=HTMLResponse,
    responses=_PAGE,
)
def letter(ctx: Reader, db: TenantDB, request_id: uuid.UUID, letter: LetterParam) -> HTMLResponse:
    """A response letter (acknowledgement, access, correction, erasure, nomination, consent
    withdrawn, grievance) as an A4 page to print and hand over (permission ``privacy.read``).
    Audited."""
    page = service.letter_html(db, ctx, request_id, letter)
    return _html(page, f"privacy-letter-{letter}.html")


@router.post(
    "/privacy/requests/{request_id}/report", response_model=PrivacyRequestOut, status_code=202
)
def build_report(ctx: Manager, db: TenantDB, request_id: uuid.UUID) -> PrivacyRequestOut:
    """Build the access report in the background from every module (FR-DPDP-009; permission
    ``privacy.request.manage``). It must then be released by someone else."""
    return service.build_report(db, ctx, request_id)


@router.post("/privacy/requests/{request_id}/report/release", response_model=PrivacyRequestOut)
def release_report(ctx: Decider, db: TenantDB, request_id: uuid.UUID) -> PrivacyRequestOut:
    """Release the ready access report (FR-DPDP-010; permission ``privacy.request.decide``,
    step-up; not the person who logged the request: 403 ``self_approval_forbidden``; 409
    ``not_verified``, ``report_not_ready``). It can be downloaded for 24 hours."""
    return service.release_report(db, ctx, request_id)


@router.get("/privacy/requests/{request_id}/report/download-url", response_model=PrivacyDownloadOut)
def report_download(ctx: Worker, db: TenantDB, request_id: uuid.UUID) -> PrivacyDownloadOut:
    """A link (5 minutes) to the released report, within 24 hours of release (permission
    ``privacy.request.manage`` or ``privacy.request.decide``, step-up). Audited
    ``privacy.report.downloaded``; SchoolOS sends the report nowhere."""
    return service.report_download(db, ctx, request_id)


@router.post("/privacy/requests/{request_id}/links", response_model=PrivacyRequestOut)
def add_link(
    ctx: Manager, db: TenantDB, request_id: uuid.UUID, body: PrivacyLinkIn
) -> PrivacyRequestOut:
    """Link a change request about the same student to a correction request (FR-DPDP-011;
    permission ``privacy.request.manage``)."""
    return service.add_link(db, ctx, request_id, body)


@router.post("/privacy/requests/{request_id}/consent-withdrawal", response_model=PrivacyRequestOut)
def withdraw_consent(
    ctx: Manager, db: TenantDB, request_id: uuid.UUID, body: PrivacyConsentWithdrawalIn
) -> PrivacyRequestOut:
    """Record the withdrawal a consent withdrawal request asks for, on one optional purpose of
    the request's student, and link both ways (R3-1B; FR-DPDP-026; permission
    ``privacy.request.manage``, and ``consent.record`` in the service: 403 otherwise). Errors:
    409 ``not_a_consent_withdrawal``, ``not_verified``, ``consent_not_given``,
    ``request_closed``."""
    return service.withdraw_consent(db, ctx, request_id, body)


@router.get("/privacy/requests/{request_id}/erasure-plan", response_model=PrivacyErasurePlanOut)
def erasure_plan(ctx: Reader, db: TenantDB, request_id: uuid.UUID) -> PrivacyErasurePlanOut:
    """Per kind of record: can be erased, kept as a school record, kept by law, or kept until
    its retention ends, with the reason (FR-DPDP-012; permission ``privacy.read``)."""
    return service.erasure_plan(db, ctx, request_id)


@router.post("/privacy/requests/{request_id}/erasure", response_model=PrivacyErasureResultOut)
def execute_erasure(
    ctx: Decider, db: TenantDB, request_id: uuid.UUID, body: PrivacyErasureIn
) -> PrivacyErasureResultOut:
    """Erase the chosen erasable kinds of record now (FR-DPDP-013, FR-DPDP-014; permission
    ``privacy.request.decide``, step-up, not the person who logged it). Errors: 403
    ``self_approval_forbidden``; 409 ``not_verified``, ``class_not_erasable``."""
    return service.execute_erasure(db, ctx, request_id, body)


@router.post("/privacy/requests/{request_id}/close", response_model=PrivacyRequestOut)
def close_request(
    ctx: Worker, db: TenantDB, request_id: uuid.UUID, body: PrivacyCloseIn
) -> PrivacyRequestOut:
    """Close a request. Access, erasure and complaints need ``privacy.request.decide`` with
    step-up by someone other than the person who logged it; others ``privacy.request.manage``. A
    complaint needs a written answer (422 ``response_required``); a correction waits for its
    change requests (409 ``changes_pending``)."""
    return service.close_request(db, ctx, request_id, body)
