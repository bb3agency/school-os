"""Consent routes for optional purposes (docs/09 R3 "Consents for optional purposes"; ADR-0049;
US-2701..US-2703, FR-DPDP-020..026).

Every route declares its guard: ``require_any(consent.read, consent.record, ...)`` for reads and
prints, ``require(consent.read)`` for the one consent view, ``require(consent.record)`` for
recording a decision and ``tenant.settings.manage`` (school-wide, step-up) for purposes. The
service applies the student scope to every object (class teachers: their sections; 404 outside
scope or school).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import HTMLResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import IdempotencyDep, IfMatch, OptionalIfMatch, etag
from app.privacy import consents_service as service
from app.privacy.consents_schemas import (
    ConsentViewOut,
    FormLanguage,
    PurposeConsentIn,
    PurposeConsentSummaryOut,
    PurposeCreate,
    PurposeListOut,
    PurposeOut,
    PurposeUpdate,
    StudentConsentsOut,
)
from app.privacy.consents_templates import STYLE_CSP

router = APIRouter(prefix="/api/v1", tags=["consents"])

Reader = Annotated[UserContext, Depends(require_any(service.READ, service.RECORD))]
ViewReader = Annotated[UserContext, Depends(require(service.READ))]
Recorder = Annotated[UserContext, Depends(require(service.RECORD))]
PurposeReader = Annotated[
    UserContext, Depends(require_any(service.READ, service.RECORD, service.SETTINGS))
]
PurposeManager = Annotated[
    UserContext, Depends(require(service.SETTINGS, scope="school", step_up=True))
]

_HTML_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": STYLE_CSP,
    "X-Content-Type-Options": "nosniff",
}
_PAGE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/html": {}}, "description": "Print-ready A4 pages (one per student)"}
}


def _student_headers(item: StudentConsentsOut) -> dict[str, str]:
    return {
        "Location": f"/api/v1/students/{item.student_id}/consents",
        "ETag": etag(item.version),
    }


def _purpose_headers(item: PurposeOut) -> dict[str, str]:
    return {"Location": f"/api/v1/consent-purposes/{item.id}", "ETag": etag(item.version)}


@router.get("/consent-purposes", response_model=PurposeListOut)
def list_purposes(ctx: PurposeReader, db: TenantDB) -> PurposeListOut:
    """The school's optional purposes (switched-off ones last) and the suggestions it may enable
    (permission ``consent.read``, ``consent.record`` or ``tenant.settings.manage``)."""
    return service.list_purposes(db)


@router.post("/consent-purposes", response_model=PurposeOut, status_code=201)
def create_purpose(
    ctx: PurposeManager, db: TenantDB, body: PurposeCreate, idem: IdempotencyDep
) -> Response:
    """Enable a suggested purpose or add the school's own (permission
    ``tenant.settings.manage``, MFA within 5 minutes). Errors: ``purpose_exists``,
    ``too_many_purposes`` (409), ``unknown_suggestion`` (422). Accepts ``Idempotency-Key``."""
    return idem.run(
        db, body, lambda: service.create_purpose(db, ctx, body), headers=_purpose_headers
    )


@router.patch("/consent-purposes/{purpose_id}", response_model=PurposeOut)
def update_purpose(
    *,
    ctx: PurposeManager,
    db: TenantDB,
    purpose_id: uuid.UUID,
    body: PurposeUpdate,
    version: IfMatch,
    response: Response,
) -> PurposeOut:
    """Edit a purpose's text or switch it on or off (``tenant.settings.manage``, step-up,
    ``If-Match``). Changing the text of a purpose families answered makes the next
    ``form_version``; earlier answers stay with their version."""
    out = service.update_purpose(db, ctx, purpose_id, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get("/students/{student_id}/consents", response_model=StudentConsentsOut)
def get_student_consents(
    ctx: Reader, db: TenantDB, student_id: uuid.UUID, response: Response
) -> StudentConsentsOut:
    """Every purpose's current answer and history for one student (permission ``consent.read``
    or ``consent.record``; class teachers: their sections). ``ETag`` is the number of decisions,
    for ``If-Match`` on the next one."""
    out = service.get_student_consents(db, ctx, student_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/students/{student_id}/consents", response_model=StudentConsentsOut, status_code=201)
def record_consent(
    *,
    ctx: Recorder,
    db: TenantDB,
    student_id: uuid.UUID,
    body: PurposeConsentIn,
    version: OptionalIfMatch,
    idem: IdempotencyDep,
) -> Response:
    """Record a family's decision on one purpose: ``given`` (with the signed form), ``refused``,
    ``withdrawn`` (after a given consent) or ``pending`` (form sent home). Appended; nothing is
    overwritten (permission ``consent.record``). Accepts ``Idempotency-Key`` and ``If-Match``.
    Errors: ``consent_not_given`` / ``consent_already_decided`` / ``purpose_inactive`` (409),
    stale ``If-Match`` (412), ``signed_form_required`` and other field errors (422)."""
    return idem.run(
        db,
        body,
        lambda: service.record_consent(db, ctx, student_id, body, expected_version=version),
        headers=_student_headers,
    )


@router.get("/students/{student_id}/consent-view", response_model=ConsentViewOut)
def consent_view(ctx: ViewReader, db: TenantDB, student_id: uuid.UUID) -> ConsentViewOut:
    """The one read-only consent view of a student: optional purposes, the APAAR decision and the
    guardians' WhatsApp opt-ins, each from its own register (permission ``consent.read``; class
    teachers: their sections). APAAR and WhatsApp parts are null for readers of neither."""
    return service.consent_view(db, ctx, student_id)


@router.get("/consents/summary", response_model=PurposeConsentSummaryOut)
def consent_summary(
    ctx: Reader,
    db: TenantDB,
    section_id: uuid.UUID | None = None,
    purpose_id: uuid.UUID | None = None,
) -> PurposeConsentSummaryOut:
    """Counts per active purpose and section of the current year (permission ``consent.read``
    or ``consent.record``; class teachers: their sections). Counts only."""
    return service.summary(db, ctx, section_id=section_id, purpose_id=purpose_id)


@router.get("/consents/forms", response_class=HTMLResponse, responses=_PAGE_RESPONSES)
def print_forms(
    *,
    ctx: Reader,
    db: TenantDB,
    student_id: uuid.UUID | None = None,
    section_id: uuid.UUID | None = None,
    only_unanswered: Annotated[
        bool, Query(description="Sections only: students with a purpose not answered yet.")
    ] = False,
    language: Annotated[
        FormLanguage | None,
        Query(
            description=(
                "Language of the printed form; the school's parent language if left out. "
                "English while Telugu is switched off (D47)."
            )
        ),
    ] = None,
) -> HTMLResponse:
    """Consent forms for one student or one section as print-ready A4 pages, one per student,
    with "I agree" and "I do not agree" for every active purpose (permission ``consent.read`` or
    ``consent.record``). Never prints an Aadhaar number. Errors: ``no_forms``,
    ``too_many_forms``, ``no_purposes`` (409). The print is audited."""
    page = service.print_forms(
        db,
        ctx,
        student_id=student_id,
        section_id=section_id,
        only_unanswered=only_unanswered,
        language=language,
    )
    target = student_id or section_id
    return HTMLResponse(
        page,
        headers={
            **_HTML_HEADERS,
            "Content-Disposition": f'inline; filename="consent-forms-{str(target)[:8]}.html"',
        },
    )
