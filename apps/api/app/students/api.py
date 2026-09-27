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
    EnrollmentIn,
    EnrollmentOut,
    GuardianCreate,
    GuardianOut,
    GuardianPatch,
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
    "access logs. Send them in the body of POST /api/v1/students/search instead (SEC-008)."
)
# RFC 9745 ``Deprecation`` date (2026-09-27) for GET /students with personal-data parameters.
_SEARCH_DEPRECATED_AT = "@1790467200"


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
    ``admission_no`` parameters still work but are deprecated (answered with a ``Deprecation``
    header) because URLs are logged by proxies and load balancers."""
    if query or admission_no:
        response.headers["Deprecation"] = _SEARCH_DEPRECATED_AT
        response.headers["Link"] = '</api/v1/students/search>; rel="successor-version"'
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
    (permission ``student.update_nonidentity``). Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: students.enrol(db, ctx, student_id, body),
        headers=lambda e: {"ETag": etag(e.version)},
    )
