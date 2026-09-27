"""Export routes (docs/09 Exports; US-501 AC4, US-901, FR-EXP-001..004, SEC-017; ADR-0021).

Every route declares its guard. Creating any export needs step-up MFA (ADR-0021 decision 1):
``POST /exports`` takes ``export.board`` or ``export.portal`` and ``POST /exports/student-list``
``student.export``, both with ``step_up``. The read routes accept any export permission or
``export.read_all`` (download: or ``export.download_any``) through :func:`require_any`; the
service then checks the permission of the particular profile or export (``export.board`` for
board profiles, ``export.portal`` for portal profiles, ``student.export`` for student lists)
and whose export it is: your own, anyone's details with ``export.read_all``, anyone's files
with ``export.download_any`` (step-up); 404 otherwise, also for other schools' ids. Files are
built in workers (202); the export at ``Location`` shows its status, and ``/download-url``
gives a presigned link valid at most 5 minutes.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import Cursor, IdempotencyDep, Limit, Page
from app.exports import service
from app.exports.schemas import (
    ExportDownloadOut,
    ExportOut,
    ExportProfileOut,
    FileFormat,
    PrecheckCreate,
    RequestedBy,
    StudentListCreate,
)

router = APIRouter(prefix="/api/v1", tags=["exports"])


ProfileReader = Annotated[UserContext, Depends(require_any(service.BOARD, service.PORTAL))]
PrecheckMaker = Annotated[
    UserContext, Depends(require_any(service.BOARD, service.PORTAL, step_up=True))
]
ListMaker = Annotated[UserContext, Depends(require(service.STUDENT_EXPORT, step_up=True))]
Reader = Annotated[
    UserContext,
    Depends(require_any(service.BOARD, service.PORTAL, service.STUDENT_EXPORT, service.READ_ALL)),
]
Downloader = Annotated[
    UserContext,
    Depends(
        require_any(service.BOARD, service.PORTAL, service.STUDENT_EXPORT, service.DOWNLOAD_ANY)
    ),
]


def _headers(out: ExportOut) -> dict[str, str]:
    return {"Location": f"/api/v1/exports/{out.id}"}


@router.get("/export-profiles", response_model=list[ExportProfileOut])
def list_export_profiles(ctx: ProfileReader) -> list[ExportProfileOut]:
    """Board and portal pre-check profiles (e.g. ``cisce-registration-2026``, ``udise-plus``)
    with their field order for the "ready to enter" sheet (permission ``export.board`` or
    ``export.portal``). ``allowed`` says whether you can run each one."""
    return service.list_profiles(ctx)


@router.post("/exports", response_model=ExportOut, status_code=202)
def create_precheck_export(
    ctx: PrecheckMaker, db: TenantDB, body: PrecheckCreate, idem: IdempotencyDep
) -> Response:
    """Make a pre-check report for a board or portal profile (``export.board`` for board
    profiles, ``export.portal`` for portal profiles; also ``student.read_basic`` and
    ``dq.findings.read``) with a recent sign-in with MFA (428 ``step_up_required``). Choose
    sections or classes (empty = every student you can see), the formats (``xlsx``, ``pdf``)
    and the language (``en``, ``te``). The students are checked again and the files are made
    in the background (202); you are notified when they are ready. Restricted (C3) values such
    as the UDISE+ ``category`` are hidden unless ``include_sensitive`` is true (needs
    ``student.read_sensitive``, else 403 ``sensitive_not_allowed``); the audit log then lists
    the restricted columns included. Errors: 422 ``unknown_profile``, ``no_students``,
    ``too_many_students``. Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: service.request_precheck(db, ctx, body),
        status_code=202,
        headers=_headers,
    )


@router.post("/exports/student-list", response_model=ExportOut, status_code=202)
def create_student_list_export(
    ctx: ListMaker, db: TenantDB, body: StudentListCreate, idem: IdempotencyDep
) -> Response:
    """Export a student list with the columns you choose as CSV or XLSX (permission
    ``student.export``, recent sign-in with MFA). Columns are attribute keys (``GET
    /attributes``) or ``class``, ``section``, ``roll_no``; restricted (C3) columns need
    ``student.read_sensitive`` (403 ``sensitive_not_allowed``) and the Aadhaar-as-printed fields
    are never exported (422 ``column_not_exportable``). Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: service.request_student_list(db, ctx, body),
        status_code=202,
        headers=_headers,
    )


@router.get("/exports", response_model=Page[ExportOut])
def list_exports(
    *,
    ctx: Reader,
    db: TenantDB,
    limit: Limit = 50,
    cursor: Cursor = None,
    requested_by: RequestedBy = "me",
) -> Page[ExportOut]:
    """Exports, newest first (any export permission or ``export.read_all``).
    ``requested_by=me`` (default): your own. ``requested_by=all``: every export of the school
    (``export.read_all``, else 403). Each item says who requested it (``requested_by``), whether
    it is yours (``own``) and whether you may download it (``can_download``)."""
    return service.list_exports(db, ctx, limit=limit, cursor=cursor, requested_by=requested_by)


@router.get("/exports/{export_id}", response_model=ExportOut)
def get_export(ctx: Reader, db: TenantDB, export_id: uuid.UUID) -> ExportOut:
    """One export: status (``queued``, ``running``, ``ready``, ``failed``, ``expired``), files,
    when they are deleted and who requested it. Your own, or anyone's with
    ``export.read_all``; 404 otherwise."""
    return service.get_export(db, ctx, export_id)


@router.get("/exports/{export_id}/download-url", response_model=ExportDownloadOut)
def get_export_download_url(
    ctx: Downloader,
    db: TenantDB,
    export_id: uuid.UUID,
    file_format: Annotated[FileFormat | None, Query(alias="format")] = None,
) -> ExportDownloadOut:
    """A download link for one file of a ready export, valid at most 5 minutes (the first
    format unless ``format`` is given). Your own export: student lists and exports with
    restricted values need a recent sign-in with MFA (428). Someone else's export needs
    ``export.download_any`` and always a recent sign-in with MFA (428); 403 ``not_own_export``
    if you can see it (``export.read_all``) but not download it, 404 otherwise. Errors: 409
    ``export_not_ready``, ``export_failed``, ``export_expired``. Every download is recorded in
    the audit log."""
    return service.download_url(db, ctx, export_id, file_format)
