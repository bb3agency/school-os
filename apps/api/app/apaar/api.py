"""APAAR consent register routes (docs/09 APAAR consent; ADR-0039; US-1901..US-1903,
FR-APC-001..006).

Every route declares its guard: ``require_any(apaar.consent.read, apaar.consent.record)`` for a
student's state and the printed forms, ``require(apaar.consent.read)`` for the lists and
summary, ``require(apaar.consent.record)`` for recording a decision and ``tenant.settings.manage``
(school-wide, step-up) for the form language. The service applies the student scope to every
object (class teachers: their sections; 404 outside scope or school).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import HTMLResponse

from app.apaar import service
from app.apaar.schemas import (
    ApaarSettingsIn,
    ApaarSettingsOut,
    ConsentIn,
    ConsentRowOut,
    ConsentStatus,
    ConsentSummaryOut,
    FormLanguage,
    StudentConsentOut,
)
from app.apaar.templates import STYLE_CSP
from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import (
    Cursor,
    IdempotencyDep,
    IfMatch,
    Limit,
    OptionalIfMatch,
    Page,
    etag,
)

router = APIRouter(prefix="/api/v1", tags=["apaar"])

Reader = Annotated[UserContext, Depends(require_any(service.READ, service.RECORD))]
ListReader = Annotated[UserContext, Depends(require(service.READ))]
Recorder = Annotated[UserContext, Depends(require(service.RECORD))]
SettingsReader = Annotated[
    UserContext, Depends(require_any(service.READ, service.RECORD, service.SETTINGS))
]
SettingsManager = Annotated[
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
LanguageQuery = Annotated[
    FormLanguage | None,
    Query(description="Language of the printed form; the school's setting when left out."),
]


def _html(page: str, filename: str) -> HTMLResponse:
    return HTMLResponse(
        page, headers={**_HTML_HEADERS, "Content-Disposition": f'inline; filename="{filename}"'}
    )


def _headers(item: StudentConsentOut) -> dict[str, str]:
    return {
        "Location": f"/api/v1/students/{item.student_id}/apaar-consent",
        "ETag": etag(item.version),
    }


@router.get("/students/{student_id}/apaar-consent", response_model=StudentConsentOut)
def get_student_consent(
    ctx: Reader, db: TenantDB, student_id: uuid.UUID, response: Response
) -> StudentConsentOut:
    """The student's APAAR consent state (``pending`` until a decision is recorded) and every
    recorded decision, newest first (permission ``apaar.consent.read`` or
    ``apaar.consent.record``; class teachers: their sections). ``ETag`` is the number of
    decisions, for ``If-Match`` on the next one."""
    out = service.get_consent(db, ctx, student_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post(
    "/students/{student_id}/apaar-consent", response_model=StudentConsentOut, status_code=201
)
def record_student_consent(
    *,
    ctx: Recorder,
    db: TenantDB,
    student_id: uuid.UUID,
    body: ConsentIn,
    version: OptionalIfMatch,
    idem: IdempotencyDep,
) -> Response:
    """Record a parent's APAAR consent decision: ``given`` (with the signed form as an evidence
    document), ``refused``, ``withdrawn`` (after a given consent) or ``pending`` (form sent,
    awaited). Appended to the register; nothing is overwritten (permission
    ``apaar.consent.record``). Accepts ``Idempotency-Key`` and ``If-Match`` (the ``ETag`` read).
    Errors: ``consent_not_given`` / ``consent_already_decided`` (409), stale ``If-Match`` (412),
    field errors such as ``signed_form_required`` or ``aadhaar_full_number_rejected`` (422)."""
    return idem.run(
        db,
        body,
        lambda: service.record_consent(db, ctx, student_id, body, expected_version=version),
        headers=_headers,
    )


@router.get(
    "/students/{student_id}/apaar-consent/form",
    response_class=HTMLResponse,
    responses=_PAGE_RESPONSES,
)
def print_student_form(
    ctx: Reader, db: TenantDB, student_id: uuid.UUID, language: LanguageQuery = None
) -> HTMLResponse:
    """The student's APAAR consent form as a print-ready A4 page, with a refusal option, in the
    school's parent-form language unless ``language`` says otherwise (permission
    ``apaar.consent.read`` or ``apaar.consent.record``). Never prints an Aadhaar number. The print
    is audited."""
    page = service.student_form(db, ctx, student_id, language=language)
    return _html(page, f"apaar-consent-{str(student_id)[:8]}.html")


@router.get(
    "/sections/{section_id}/apaar-consent-forms",
    response_class=HTMLResponse,
    responses=_PAGE_RESPONSES,
)
def print_section_forms(
    ctx: Reader,
    db: TenantDB,
    section_id: uuid.UUID,
    status: Annotated[
        ConsentStatus | None,
        Query(description="Print only students in this state, e.g. pending to send again."),
    ] = None,
    language: LanguageQuery = None,
) -> HTMLResponse:
    """APAAR consent forms for a section of the current year, one A4 page per student
    (permission ``apaar.consent.read`` or ``apaar.consent.record``; class teachers: their
    sections). Errors: ``no_forms`` / ``too_many_forms`` (409). The print is audited."""
    page = service.section_forms(db, ctx, section_id, status=status, language=language)
    return _html(page, f"apaar-consent-forms-{str(section_id)[:8]}.html")


@router.get("/apaar/consents", response_model=Page[ConsentRowOut])
def list_consents(
    *,
    ctx: ListReader,
    db: TenantDB,
    section_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    status: ConsentStatus | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[ConsentRowOut]:
    """Students of the current academic year with their APAAR consent state, by class and name
    (permission ``apaar.consent.read``; class teachers: their sections). ``status=pending`` is
    the follow-up list."""
    return service.list_consents(
        db,
        ctx,
        section_id=section_id,
        class_id=class_id,
        status=status,
        limit=limit,
        cursor=cursor,
    )


@router.get("/apaar/consents/summary", response_model=ConsentSummaryOut)
def consent_summary(
    ctx: ListReader,
    db: TenantDB,
    section_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
) -> ConsentSummaryOut:
    """Given / refused / pending / withdrawn counts per section and in total (permission
    ``apaar.consent.read``; class teachers: their sections). Counts only."""
    return service.summary(db, ctx, section_id=section_id, class_id=class_id)


@router.get("/apaar/settings", response_model=ApaarSettingsOut)
def get_settings(ctx: SettingsReader, db: TenantDB, response: Response) -> ApaarSettingsOut:
    """The language of the printed parent form, ``en`` or ``te`` (permission
    ``apaar.consent.read``, ``apaar.consent.record`` or ``tenant.settings.manage``)."""
    out = service.get_settings(db)
    response.headers["ETag"] = etag(out.version)
    return out


@router.put("/apaar/settings", response_model=ApaarSettingsOut)
def put_settings(
    *,
    ctx: SettingsManager,
    db: TenantDB,
    body: ApaarSettingsIn,
    version: IfMatch,
    response: Response,
) -> ApaarSettingsOut:
    """Choose the language of the printed parent form: English or Telugu, per school (owner
    decision D9; the staff screens stay English). Permission ``tenant.settings.manage``, MFA
    within 5 minutes, ``If-Match`` (``W/"0"`` before the first save). Audited."""
    out = service.update_settings(db, ctx, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out
