"""Student record routes (docs/09 Students; US-301..303, FR-STU-001..012, SEC-012/013/015).

Every route declares its permission with ``require()``; the service applies the caller's
class/section scope to every object (404 outside scope or school). Mutating routes first scan
the raw JSON body for full Aadhaar numbers (422 ``aadhaar_full_number_rejected`` with
``errors.aadhaar_last4_only`` on each offending field), before any other validation, so a
pasted Aadhaar number is always explained the same way (US-303).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, IdempotencyDep, IfMatch, Limit, Page, etag, if_match_version
from app.core.errors import ValidationFailed
from app.students import service as students
from app.students.definitions import AADHAAR_DETAIL, aadhaar_error, find_full_aadhaar
from app.students.schemas import (
    AttributeOut,
    EnrollmentEnd,
    EnrollmentIn,
    EnrollmentOut,
    EnrollmentPatch,
    GuardianCreate,
    GuardianOut,
    GuardianPatch,
    PromotionCommitIn,
    PromotionIn,
    PromotionPreviewOut,
    PromotionRunOut,
    RevealIn,
    RevealOut,
    SearchFilters,
    StudentCreate,
    StudentOut,
    StudentPatch,
    StudentSearchIn,
    StudentStatus,
    StudentSummary,
    ValueIn,
    ValueOut,
    ValueRecorded,
    VerifyIn,
)

router = APIRouter(prefix="/api/v1", tags=["students"])

Reader = Annotated[UserContext, Depends(require(students.READ))]
Creator = Annotated[UserContext, Depends(require("student.create", scope="school"))]
Updater = Annotated[UserContext, Depends(require(students.UPDATE))]
Revealer = Annotated[UserContext, Depends(require(students.SENSITIVE))]
Promoter = Annotated[UserContext, Depends(require(students.PROMOTE, scope="school"))]


async def reject_full_aadhaar_body(request: Request) -> None:
    """SEC-013 / FR-STU-012: refuse any JSON body containing a full Aadhaar number."""
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return  # malformed JSON: FastAPI's own validation answers 422
    paths = find_full_aadhaar(payload)
    if paths:
        raise ValidationFailed([aadhaar_error(p) for p in paths], detail=AADHAAR_DETAIL)


AadhaarGuard = Annotated[None, Depends(reject_full_aadhaar_body)]


def optional_if_match(request: Request) -> int | None:
    """``If-Match`` when sent (lost-update guard on appends); required routes use ``IfMatch``."""
    if request.headers.get("if-match") is None:
        return None
    return if_match_version(request)


OptionalIfMatch = Annotated[int | None, Depends(optional_if_match)]


def _student_headers(item: StudentOut) -> dict[str, str]:
    return {"Location": f"/api/v1/students/{item.id}", "ETag": etag(item.version)}


# --- attribute catalog ---------------------------------------------------------------------------


@router.get("/attributes", response_model=list[AttributeOut])
def list_attributes(ctx: Reader, db: TenantDB) -> list[AttributeOut]:
    """Student attributes with classification (C2/C3), identity flag, allowed sources and
    English/Telugu labels (permission ``student.read_basic``)."""
    return students.attribute_catalog(db)


# --- students ------------------------------------------------------------------------------------


_PII_IN_URL = (
    "Deprecated: names and admission numbers in the URL end up in proxy and load-balancer "
    "access logs. Send them in the body of POST /api/v1/students/search instead (SEC-008). "
    "Stops working after the Sunset date, Thu, 31 Dec 2026 23:59:59 GMT."
)
# RFC 9745 ``Deprecation`` date (2026-09-27) for GET /students with personal-data parameters.
_SEARCH_DEPRECATED_AT = "@1790467200"
# RFC 8594 ``Sunset``: after this the deprecated parameters may be removed (owner, 2026-09-27).
_SEARCH_SUNSET = "Thu, 31 Dec 2026 23:59:59 GMT"
_SEARCH_SUCCESSOR = '</api/v1/students/search>; rel="successor-version"'


@router.get("/students", response_model=Page[StudentSummary])
def search_students(
    *,
    ctx: Reader,
    db: TenantDB,
    response: Response,
    query: Annotated[
        str | None, Query(max_length=200, deprecated=True, description=_PII_IN_URL)
    ] = None,
    section_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    status: StudentStatus | None = None,
    admission_no: Annotated[
        str | None, Query(max_length=32, deprecated=True, description=_PII_IN_URL)
    ] = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[StudentSummary]:
    """List students by class, section and status (permission ``student.read_basic``; class
    and subject teachers see only students in their sections/classes this year). To search by
    name, parent name or admission number use ``POST /students/search``: the ``query`` and
    ``admission_no`` parameters still work but are deprecated because URLs are logged by
    proxies and load balancers. A response to a request that used them carries a
    ``Deprecation`` header (RFC 9745), a ``Sunset: Thu, 31 Dec 2026 23:59:59 GMT`` header
    (RFC 8594; the parameters may stop working after that date) and
    ``Link: </api/v1/students/search>; rel="successor-version"``."""
    if query or admission_no:
        response.headers["Deprecation"] = _SEARCH_DEPRECATED_AT
        response.headers["Sunset"] = _SEARCH_SUNSET
        response.headers["Link"] = _SEARCH_SUCCESSOR
    filters = SearchFilters(
        query=query,
        section_id=section_id,
        class_id=class_id,
        status=status,
        admission_no=admission_no,
    )
    return students.search(db, ctx, filters, limit=limit, cursor=cursor)


@router.post("/students/search", response_model=Page[StudentSummary])
def search_students_by_body(
    ctx: Reader,
    _aadhaar: AadhaarGuard,
    db: TenantDB,
    body: StudentSearchIn,
) -> Page[StudentSummary]:
    """Find students by partial name in English or Telugu, admission number, class/section
    (``9b``, ``IX-B``) or parent name, with the filters in the JSON body so personal data never
    appears in a URL (SEC-008; permission ``student.read_basic``; class and subject teachers see
    only students in their sections/classes this year). Same results, page size and cursor as
    ``GET /students``; send ``next_cursor`` back as ``cursor`` with the same filters. Read-only:
    nothing is written, so no ``Idempotency-Key``. A full Aadhaar number anywhere in the body is
    refused (422 ``aadhaar_full_number_rejected``)."""
    return students.search(db, ctx, body.filters(), limit=body.limit, cursor=body.cursor)


@router.post("/students", response_model=StudentOut, status_code=201)
def create_student(
    ctx: Creator,
    _aadhaar: AadhaarGuard,
    db: TenantDB,
    body: StudentCreate,
    idem: IdempotencyDep,
) -> Response:
    """Add a student with first values, each with its source; optionally enrol in a section
    (permission ``student.create``). Accepts ``Idempotency-Key``."""
    return idem.run(
        db, body, lambda: students.create_student(db, ctx, body), headers=_student_headers
    )


@router.get("/students/{student_id}", response_model=StudentOut)
def get_student(ctx: Reader, db: TenantDB, student_id: uuid.UUID, response: Response) -> StudentOut:
    """Canonical profile and current per-source values (permission ``student.read_basic``).
    Sensitive fields are hidden without ``student.read_sensitive`` and masked with it."""
    out = students.get_profile(db, ctx, student_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.patch("/students/{student_id}", response_model=StudentOut)
def update_student(
    *,
    ctx: Updater,
    _aadhaar: AadhaarGuard,
    db: TenantDB,
    student_id: uuid.UUID,
    body: StudentPatch,
    version: IfMatch,
    response: Response,
) -> StudentOut:
    """Change the record status, e.g. ``left`` (permission ``student.update_nonidentity``;
    ``If-Match`` required)."""
    out = students.update_student_status(db, ctx, student_id, body.status, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get("/students/{student_id}/values", response_model=list[ValueOut])
def list_values(
    ctx: Reader,
    db: TenantDB,
    student_id: uuid.UUID,
    attribute: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{1,63}$")] = None,
) -> list[ValueOut]:
    """Full value history, newest first, for one attribute or all (permission
    ``student.read_basic``)."""
    return students.value_history(db, ctx, student_id, attribute)


@router.post("/students/{student_id}/values", response_model=ValueRecorded, status_code=201)
def record_value(
    *,
    ctx: Updater,
    _aadhaar: AadhaarGuard,
    db: TenantDB,
    student_id: uuid.UUID,
    body: ValueIn,
    version: OptionalIfMatch,
    idem: IdempotencyDep,
) -> Response:
    """Record a value from a source; the previous value of that source stays in history
    (permission ``student.update_nonidentity``). Identity fields from the admission register
    need a change request (403 ``identity_change_required``). Optional ``If-Match``."""
    return idem.run(
        db,
        body,
        lambda: students.record_value_in(db, ctx, student_id, body, expected_version=version),
        headers=lambda r: {"ETag": etag(r.student_version)},
    )


@router.post(
    "/students/{student_id}/values/{value_id}/verify", response_model=ValueOut, status_code=200
)
def verify_value(
    *,
    ctx: Updater,
    _aadhaar: AadhaarGuard,
    db: TenantDB,
    student_id: uuid.UUID,
    value_id: uuid.UUID,
    body: VerifyIn | None = None,
) -> ValueOut:
    """Mark the current value of a non-identity attribute verified or rejected (permission
    ``student.update_nonidentity``). Identity values are verified by change requests."""
    decision = body.status if body is not None else "verified"
    return students.verify_value(db, ctx, student_id, value_id, decision)


@router.post("/students/{student_id}/sensitive-reveal", response_model=RevealOut)
def reveal_sensitive(
    ctx: Revealer,
    _aadhaar: AadhaarGuard,
    db: TenantDB,
    student_id: uuid.UUID,
    body: RevealIn,
    response: Response,
) -> RevealOut:
    """Show one sensitive (C3) value; every reveal is audited (permission
    ``student.read_sensitive``, class teachers only for their sections)."""
    response.headers["Cache-Control"] = "no-store"
    return students.reveal_sensitive(db, ctx, student_id, body)


# --- guardians and enrolments ----------------------------------------------------------------


@router.get("/students/{student_id}/guardians", response_model=list[GuardianOut])
def list_guardians(ctx: Reader, db: TenantDB, student_id: uuid.UUID) -> list[GuardianOut]:
    """Parents and guardians (permission ``student.read_basic``); phone and address masked."""
    return students.list_guardians(db, ctx, student_id)


@router.post("/students/{student_id}/guardians", response_model=GuardianOut, status_code=201)
def add_guardian(
    *,
    ctx: Updater,
    _aadhaar: AadhaarGuard,
    db: TenantDB,
    student_id: uuid.UUID,
    body: GuardianCreate,
    idem: IdempotencyDep,
) -> Response:
    """Add a guardian, or link an existing one by ``guardian_id`` (permission
    ``student.update_nonidentity``). Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: students.add_guardian(db, ctx, student_id, body),
        headers=lambda g: {"ETag": etag(g.version)},
    )


@router.patch("/students/{student_id}/guardians/{guardian_id}", response_model=GuardianOut)
def update_guardian(
    *,
    ctx: Updater,
    _aadhaar: AadhaarGuard,
    db: TenantDB,
    student_id: uuid.UUID,
    guardian_id: uuid.UUID,
    body: GuardianPatch,
    version: IfMatch,
    response: Response,
) -> GuardianOut:
    """Change a guardian's name, phone, address, relationship or primary flag (permission
    ``student.update_nonidentity``; ``If-Match`` with the guardian's ETag)."""
    out = students.update_guardian(db, ctx, student_id, guardian_id, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/students/{student_id}/enrollments", response_model=EnrollmentOut, status_code=201)
def enrol_student(
    *,
    ctx: Updater,
    _aadhaar: AadhaarGuard,
    db: TenantDB,
    student_id: uuid.UUID,
    body: EnrollmentIn,
    idem: IdempotencyDep,
) -> Response:
    """Enrol in a section; an active enrolment in the same year becomes ``transferred``
    (permission ``student.update_nonidentity``). Accepts ``Idempotency-Key``. 409
    ``structure_archived`` when the section, its class or its academic year is archived."""
    return idem.run(
        db,
        body,
        lambda: students.enrol(db, ctx, student_id, body),
        headers=lambda e: {"ETag": etag(e.version)},
    )


@router.get("/students/{student_id}/enrollments", response_model=list[EnrollmentOut])
def list_enrollments(ctx: Reader, db: TenantDB, student_id: uuid.UUID) -> list[EnrollmentOut]:
    """Every enrolment of the student (any year, active or closed), newest first; each carries
    its ``version`` for ``If-Match`` (permission ``student.read_basic``)."""
    return students.list_enrollments(db, ctx, student_id)


@router.patch("/students/{student_id}/enrollments/{enrollment_id}", response_model=EnrollmentOut)
def update_enrollment(
    *,
    ctx: Updater,
    _aadhaar: AadhaarGuard,
    db: TenantDB,
    student_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    body: EnrollmentPatch,
    version: IfMatch,
    response: Response,
) -> EnrollmentOut:
    """Correct the roll number, or move an active enrolment to another section of the same
    class and year (permission ``student.update_nonidentity``; ``If-Match`` with the
    enrolment's version, 412 ``precondition_failed`` when stale). Moving to another class is a
    new enrolment (``POST …/enrollments``). 409 ``structure_archived`` when the target section
    (or its class or year) is archived."""
    out = students.update_enrollment(
        db, ctx, student_id, enrollment_id, body, expected_version=version
    )
    response.headers["ETag"] = etag(out.version)
    return out


@router.post(
    "/students/{student_id}/enrollments/{enrollment_id}/end",
    response_model=EnrollmentOut,
    status_code=200,
)
def end_enrollment(
    *,
    ctx: Updater,
    _aadhaar: AadhaarGuard,
    db: TenantDB,
    student_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    body: EnrollmentEnd | None = None,
    version: IfMatch,
    response: Response,
) -> EnrollmentOut:
    """Close an active enrolment as ``completed`` (default) or ``transferred`` on ``ended_on``
    (default today); the record status is unchanged (permission
    ``student.update_nonidentity``; ``If-Match`` with the enrolment's version). 409
    ``enrollment_not_active`` when it is already closed."""
    out = students.end_enrollment(
        db, ctx, student_id, enrollment_id, body or EnrollmentEnd(), expected_version=version
    )
    response.headers["ETag"] = etag(out.version)
    return out


@router.delete("/students/{student_id}/guardians/{guardian_id}", status_code=204)
def remove_guardian(
    *,
    ctx: Updater,
    db: TenantDB,
    student_id: uuid.UUID,
    guardian_id: uuid.UUID,
    version: IfMatch,
) -> Response:
    """Unlink a guardian from the student (permission ``student.update_nonidentity``;
    ``If-Match`` with the guardian's ETag). A guardian no other student is linked to is deleted
    with their phone and address."""
    students.remove_guardian(db, ctx, student_id, guardian_id, expected_version=version)
    return Response(status_code=204)


# --- promotions (FR-TEN-011, US-202 AC2) -------------------------------------------------------


def _promotion_headers(run: PromotionRunOut) -> dict[str, str]:
    return {
        "Location": f"/api/v1/academic-years/{run.from_academic_year_id}/promotions",
        "ETag": etag(run.version),
    }


@router.post(
    "/academic-years/{year_id}/promotions:preview",
    response_model=PromotionPreviewOut,
    tags=["promotions"],
)
def preview_promotion(
    ctx: Promoter, db: TenantDB, year_id: uuid.UUID, body: PromotionIn
) -> PromotionPreviewOut:
    """Plan the year-end promotion of this academic year into ``to_academic_year_id`` without
    changing anything (permission ``tenant.structure.manage``). Class N goes to N+1 by class
    order; ``held_back_student_ids`` stay in their class; the last class graduates; students who
    left are skipped. Sections keep their name unless ``section_map`` says otherwise.
    ``problems`` lists students who cannot be placed; ``plan_fingerprint`` can be sent with the
    commit to make sure nothing changed in between."""
    return students.preview_promotion(db, ctx, year_id, body)


@router.post(
    "/academic-years/{year_id}/promotions:commit",
    response_model=PromotionRunOut,
    status_code=201,
    tags=["promotions"],
)
def commit_promotion(
    ctx: Promoter,
    db: TenantDB,
    year_id: uuid.UUID,
    body: PromotionCommitIn,
    idem: IdempotencyDep,
) -> Response:
    """Apply the promotion in one transaction (permission ``tenant.structure.manage``; accepts
    ``Idempotency-Key``): old enrolments are closed, new ones opened, graduates marked
    ``graduated``. 409 ``promotion_already_committed``, ``promotion_plan_changed`` or
    ``nothing_to_promote``; 409 ``structure_archived`` when a target section, class or the
    target year is archived; 422 ``no_target_section``. Can be undone within 24 hours."""
    return idem.run(
        db,
        body,
        lambda: students.commit_promotion(db, ctx, year_id, body),
        headers=_promotion_headers,
    )


@router.post(
    "/academic-years/{year_id}/promotions:undo",
    response_model=PromotionRunOut,
    tags=["promotions"],
)
def undo_promotion(ctx: Promoter, db: TenantDB, year_id: uuid.UUID) -> PromotionRunOut:
    """Undo this year's committed promotion within 24 hours (permission
    ``tenant.structure.manage``). 409 ``no_promotion``, ``promotion_undo_expired``, or
    ``promotion_has_dependents`` when an enrolment it touched changed afterwards, or
    ``structure_archived`` when the source year (or a section or class of it) was archived."""
    return students.undo_promotion(db, ctx, year_id)


@router.get(
    "/academic-years/{year_id}/promotions",
    response_model=list[PromotionRunOut],
    tags=["promotions"],
)
def list_promotions(ctx: Promoter, db: TenantDB, year_id: uuid.UUID) -> list[PromotionRunOut]:
    """Promotions out of this academic year, newest first, with ``can_undo`` and
    ``undo_until`` (permission ``tenant.structure.manage``)."""
    return students.list_promotions(db, ctx, year_id)
