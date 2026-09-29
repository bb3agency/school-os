"""Import routes (docs/09 Imports; US-401, FR-IMP-001..007).

Every route declares its permission with ``require()``: ``import.run`` to start, map, check and
read imports and templates; ``import.commit`` to add a checked file to the student records or
revert it. Parsing, checking and adding run in workers (202 + the job id in the body; the batch
resource at ``Location`` shows status and counts). Another school's or an unknown import id
answers 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, IdempotencyDep, IfMatch, Limit, Page, etag
from app.identity.principal import Principal, get_principal, require_recent_auth
from app.imports import service
from app.imports.schemas import (
    BatchStatus,
    CommitIn,
    ImportCreate,
    ImportOut,
    ImportRowOut,
    ImportSheetOut,
    ImportSummary,
    MappingIn,
    RowEditIn,
    RowFilter,
    SheetEditOut,
    SheetFormat,
    TemplateCreate,
    TemplateOut,
)

router = APIRouter(prefix="/api/v1", tags=["imports"])

Runner = Annotated[UserContext, Depends(require(service.RUN))]
Committer = Annotated[UserContext, Depends(require(service.COMMIT))]
SheetLimit = Annotated[int, Query(ge=1, le=200, description="Rows per page (max 200).")]
RowNo = Annotated[int, Path(ge=1, le=1_000_000, description="Row number as the file shows it")]


def recent_sign_in(principal: Annotated[Principal, Depends(get_principal)]) -> None:
    """Step-up for downloads of personal data (FR-EXP-004, docs/07 §5.2: MFA within 5
    minutes, 428 ``step_up_required``); ``import.run`` itself is not a step-up permission."""
    require_recent_auth(principal)


_SHEET_FILE_DOC: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The staged sheet as a file (CSV: UTF-8 with BOM; XLSX: text cells)",
        "content": {
            "text/csv": {"schema": {"type": "string"}},
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                "schema": {"type": "string", "format": "binary"}
            },
        },
    }
}


def _headers(batch: ImportOut) -> dict[str, str]:
    return {"Location": f"/api/v1/imports/{batch.id}", "ETag": etag(batch.version)}


@router.post("/imports", response_model=ImportOut, status_code=202)
def create_import(ctx: Runner, db: TenantDB, body: ImportCreate, idem: IdempotencyDep) -> Response:
    """Import an uploaded spreadsheet (permission ``import.run``).

    Upload the file first with ``POST /documents/uploads`` (purpose ``import_file``: XLSX or
    CSV, at most 10 MB) and register it; once it passed the virus check, send its
    ``document_id`` and the ``source`` the data comes from (e.g. ``admission_register``,
    ``udise_plus``). Answers 202: the file is read (formulas are never run) and columns are
    matched to fields in the background. Accepts ``Idempotency-Key``.
    """
    return idem.run(
        db, body, lambda: service.create_import(db, ctx, body), status_code=202, headers=_headers
    )


@router.get("/imports", response_model=Page[ImportSummary])
def list_imports(
    *,
    ctx: Runner,
    db: TenantDB,
    limit: Limit = 50,
    cursor: Cursor = None,
    status: BatchStatus | None = None,
) -> Page[ImportSummary]:
    """The school's imports, newest first (permission ``import.run``)."""
    return service.list_imports(db, ctx, limit=limit, cursor=cursor, status=status)


@router.get("/imports/{import_id}", response_model=ImportOut)
def get_import(ctx: Runner, db: TenantDB, import_id: uuid.UUID, response: Response) -> ImportOut:
    """One import: status, columns with suggested and chosen fields, row counts, revert
    deadline (permission ``import.run``). ``ETag`` is needed to change the mapping."""
    out = service.get_import(db, ctx, import_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get("/imports/{import_id}/rows", response_model=Page[ImportRowOut])
def list_rows(
    *,
    ctx: Runner,
    db: TenantDB,
    import_id: uuid.UUID,
    status: RowFilter | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[ImportRowOut]:
    """Checked rows in file order with row-level errors and warnings (permission
    ``import.run``); ``status=error`` lists the rows to fix. Restricted (C3) values are never
    shown, only which of them a row has."""
    return service.list_rows(db, ctx, import_id, status=status, limit=limit, cursor=cursor)


@router.get("/imports/{import_id}/sheet", response_model=ImportSheetOut)
def get_sheet(
    *,
    ctx: Runner,
    db: TenantDB,
    import_id: uuid.UUID,
    response: Response,
    limit: SheetLimit = 100,
    cursor: Cursor = None,
) -> ImportSheetOut:
    """The uploaded file as a sheet (permission ``import.run``; FR-IMP-008): every column with
    the field it fills, the rows in file order with staged edits applied and marked, and each
    row's check result. Restricted (C3) columns show no values and Aadhaar-like numbers are
    masked. ``editable`` says whether cells can still be changed (not after the import was
    added). ``ETag`` is the import's version, needed to edit. 409 ``import_not_ready`` while the
    file is being read, ``file_missing`` after the raw file was deleted (90 days after import)."""
    out = service.get_sheet(db, ctx, import_id, limit=limit, cursor=cursor)
    response.headers["ETag"] = etag(out.version)
    return out


@router.patch("/imports/{import_id}/sheet/rows/{row_no}", response_model=SheetEditOut)
def edit_sheet_row(
    *,
    ctx: Runner,
    db: TenantDB,
    import_id: uuid.UUID,
    row_no: RowNo,
    body: RowEditIn,
    version: IfMatch,
    response: Response,
) -> SheetEditOut:
    """Change cells of one row before the import is added (permission ``import.run``;
    ``If-Match``; FR-IMP-008). The uploaded file is kept as it was; the edit is recorded with
    who and when, and a checked file re-checks the row at once. 412 when the import changed
    meanwhile (reload), 409 ``import_not_editable`` once it was added or reverted (correct
    records on the student profile or with a change request), 422 for a full Aadhaar number
    (``aadhaar_full_number_rejected``: enter only the last 4 digits), line breaks, text over
    1,000 characters or a restricted column."""
    out = service.edit_row(db, ctx, import_id, row_no, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get("/imports/{import_id}/sheet/export", response_class=Response, responses=_SHEET_FILE_DOC)
def export_sheet(
    *,
    ctx: Runner,
    _step_up: Annotated[None, Depends(recent_sign_in)],
    db: TenantDB,
    import_id: uuid.UUID,
    format: Annotated[SheetFormat, Query(description="csv or xlsx")] = "csv",
) -> Response:
    """Download the staged sheet with its edits as CSV or XLSX (permission ``import.run`` and
    a recent sign-in with MFA, 428 ``step_up_required``; FR-IMP-009, FR-EXP-004). One header
    row and the data rows; Aadhaar-like numbers masked, formulas neutralised; restricted (C3)
    columns are empty unless you may see sensitive fields. Every download is audited."""
    file = service.export_sheet(db, ctx, import_id, format)
    return Response(
        content=file.content,
        media_type=file.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put("/imports/{import_id}/mapping", response_model=ImportOut)
def set_mapping(
    *,
    ctx: Runner,
    db: TenantDB,
    import_id: uuid.UUID,
    body: MappingIn,
    version: IfMatch,
    response: Response,
) -> ImportOut:
    """Choose which field each column fills (permission ``import.run``; ``If-Match``). Check
    the file again afterwards with ``POST /imports/{id}/validate``."""
    out = service.set_mapping(db, ctx, import_id, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/imports/{import_id}/validate", response_model=ImportOut, status_code=202)
def validate_import(
    ctx: Runner, db: TenantDB, import_id: uuid.UUID, idem: IdempotencyDep
) -> Response:
    """Check every row with the current mapping (permission ``import.run``; 202). Nothing is
    saved to student records. Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        None,
        lambda: service.request_validation(db, ctx, import_id),
        status_code=202,
        headers=_headers,
    )


@router.post("/imports/{import_id}/commit", response_model=ImportOut, status_code=202)
def commit_import(
    ctx: Committer,
    db: TenantDB,
    import_id: uuid.UUID,
    idem: IdempotencyDep,
    body: CommitIn | None = None,
) -> Response:
    """Add a checked file to the student records, all rows or none (permission
    ``import.commit``; 202). Values are recorded from the import's source; identity values
    from the admission register stay provisional until verified. With rows in error send
    ``{"skip_error_rows": true}`` to add only the valid rows. Accepts ``Idempotency-Key``."""
    data = body or CommitIn()
    return idem.run(
        db,
        data,
        lambda: service.request_commit(db, ctx, import_id, data),
        status_code=202,
        headers=_headers,
    )


@router.post("/imports/{import_id}/revert", response_model=ImportOut)
def revert_import(ctx: Committer, db: TenantDB, import_id: uuid.UUID) -> ImportOut:
    """Undo an added import within 24 hours (permission ``import.commit``). Refused with 409
    ``import_has_dependents`` when records from it were changed or are used since, and 409
    ``revert_window_closed`` after 24 hours."""
    return service.revert(db, ctx, import_id)


@router.get("/import-templates", response_model=list[TemplateOut])
def list_templates(ctx: Runner, db: TenantDB) -> list[TemplateOut]:
    """Saved column mappings; a file with the same headers reuses one automatically
    (permission ``import.run``)."""
    return service.list_templates(db, ctx)


@router.post("/import-templates", response_model=TemplateOut, status_code=201)
def create_template(
    ctx: Runner, db: TenantDB, body: TemplateCreate, idem: IdempotencyDep
) -> Response:
    """Save an import's column mapping as a template (permission ``import.run``). Accepts
    ``Idempotency-Key``."""
    return idem.run(db, body, lambda: service.create_template(db, ctx, body), status_code=201)
