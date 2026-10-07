"""Document routes (docs/09 Documents; FR-DOC-001..006, FR-DOC-008, SEC-016).

Uploads go straight from the browser to S3 with a presigned POST; the API then registers the
object after checking it (size, magic bytes, hash) and a worker scans it for malware. Reads
are filtered by the document ACL and the caller's scopes (404 outside them). Downloads are
presigned GETs valid for at most 5 minutes that always download as attachments.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import (
    Cursor,
    IdempotencyDep,
    IfMatch,
    Limit,
    Page,
    decode_cursor,
    encode_cursor,
    etag,
)
from app.core.errors import ValidationFailed
from app.documents import service
from app.documents.schemas import (
    AclUpdate,
    DocType,
    DocumentCreate,
    DocumentDetail,
    DocumentOut,
    DocumentSheetOut,
    DocumentStatus,
    DocumentUpdate,
    DownloadUrlOut,
    Purpose,
    SheetExportIn,
    SheetSaveIn,
    UploadCreate,
    UploadOut,
    VersionCreate,
)

router = APIRouter(prefix="/api/v1", tags=["documents"])

Uploader = Annotated[UserContext, Depends(require(service.UPLOAD))]
Reader = Annotated[UserContext, Depends(require(service.READ))]
Manager = Annotated[UserContext, Depends(require(service.MANAGE, scope="school"))]
SheetLimit = Annotated[int, Query(ge=1, le=200, description="Rows per page (max 200).")]

_SHEET_FILE_DOC: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The sheet as a file (CSV: UTF-8 with BOM; XLSX: text cells)",
        "content": {
            "text/csv": {"schema": {"type": "string"}},
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                "schema": {"type": "string", "format": "binary"}
            },
        },
    }
}


def _created(doc: DocumentOut) -> dict[str, str]:
    return {"Location": f"/api/v1/documents/{doc.id}", "ETag": etag(doc.version)}


def _before(cursor: str | None) -> uuid.UUID | None:
    after = decode_cursor(cursor)
    if after is None:
        return None
    try:
        return uuid.UUID(str(after.get("k")))
    except ValueError as exc:
        raise ValidationFailed(
            [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid_cursor"}]
        ) from exc


@router.post("/documents/uploads", response_model=UploadOut, status_code=201)
def create_upload(
    ctx: Uploader, db: TenantDB, body: UploadCreate, idem: IdempotencyDep
) -> Response:
    """Get a presigned POST for one file (permission ``document.upload``).

    Accepted: PDF, JPG, PNG, DOCX, XLSX up to 25 MB (evidence and register scans: PDF, JPG,
    PNG; spreadsheet imports: XLSX or CSV up to 10 MB). The form must be posted within 10
    minutes with the returned fields; the key, Content-Type and size are fixed by the policy.
    Send ``document_id`` to upload a new version of a document you uploaded, or of any document
    you can see if you hold ``document.manage_acl`` (403 ``document_owner_only`` otherwise; 409
    ``document_archived`` for an archived document). Accepts ``Idempotency-Key``.
    """
    return idem.run(db, body, lambda: service.create_upload(db, ctx, body))


@router.post("/documents", response_model=DocumentOut, status_code=202)
def register_document(
    ctx: Uploader, db: TenantDB, body: DocumentCreate, idem: IdempotencyDep
) -> Response:
    """Register an uploaded file with its metadata and ACL (permission ``document.upload``).

    The file is checked by content (not extension): a mismatch answers 415 and the object is
    deleted; too large answers 413; the same file already visible to you answers 409
    ``duplicate_document``. Class teachers must limit the ACL to their own sections/classes.
    Answers 202: version 1 is ``queued`` for the malware scan. Accepts ``Idempotency-Key``.
    """
    return idem.run(
        db,
        body,
        lambda: service.register_document(db, ctx, body),
        status_code=202,
        headers=_created,
    )


@router.get("/documents", response_model=Page[DocumentOut])
def list_documents(
    *,
    ctx: Reader,
    db: TenantDB,
    limit: Limit = 50,
    cursor: Cursor = None,
    purpose: Purpose | None = None,
    doc_type: DocType | None = None,
    academic_year_id: uuid.UUID | None = None,
    status: DocumentStatus | None = None,
) -> Page[DocumentOut]:
    """Documents you may see, newest first (permission ``document.read``; filtered by the
    document ACL and your class/section scopes)."""
    items, last = service.list_documents(
        db,
        ctx,
        limit=limit,
        before_id=_before(cursor),
        purpose=purpose,
        doc_type=doc_type,
        academic_year_id=academic_year_id,
        status=status,
    )
    return Page[DocumentOut](
        data=items, next_cursor=encode_cursor({"k": str(last)}) if last else None
    )


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(
    ctx: Reader, db: TenantDB, document_id: uuid.UUID, response: Response
) -> DocumentDetail:
    """One document with its versions and processing status (permission ``document.read``;
    404 outside your ACL/scopes)."""
    doc = service.get_document(db, ctx, document_id)
    response.headers["ETag"] = etag(doc.version)
    return doc


@router.patch("/documents/{document_id}", response_model=DocumentOut)
def update_document(
    *,
    ctx: Uploader,
    db: TenantDB,
    document_id: uuid.UUID,
    body: DocumentUpdate,
    version: IfMatch,
    response: Response,
) -> DocumentOut:
    """Change the title, type, language, issuer or date (permission ``document.upload``; the
    document must be visible to you and uploaded by you, unless you hold
    ``document.manage_acl``, as for a new version: 403 ``document_owner_only`` otherwise;
    ``If-Match``). An archived document
    answers 409 ``document_archived``; a type that does not suit the purpose 422. Audited with
    the changed field names only."""
    doc = service.update_document(db, ctx, document_id, body, expected_version=version)
    response.headers["ETag"] = etag(doc.version)
    return doc


@router.post("/documents/{document_id}/archive", response_model=DocumentOut)
def archive_document(
    ctx: Manager, db: TenantDB, document_id: uuid.UUID, version: IfMatch, response: Response
) -> DocumentOut:
    """Archive a document: kept with its versions, listed only with ``status=archived``
    (permission ``document.manage_acl``; ``If-Match``)."""
    doc = service.set_document_status(db, ctx, document_id, archived=True, expected_version=version)
    response.headers["ETag"] = etag(doc.version)
    return doc


@router.post("/documents/{document_id}/unarchive", response_model=DocumentOut)
def unarchive_document(
    ctx: Manager, db: TenantDB, document_id: uuid.UUID, version: IfMatch, response: Response
) -> DocumentOut:
    """Make an archived document active again (permission ``document.manage_acl``;
    ``If-Match``)."""
    doc = service.set_document_status(
        db, ctx, document_id, archived=False, expected_version=version
    )
    response.headers["ETag"] = etag(doc.version)
    return doc


@router.post("/documents/{document_id}/versions", response_model=DocumentOut, status_code=202)
def add_version(
    ctx: Uploader,
    db: TenantDB,
    document_id: uuid.UUID,
    body: VersionCreate,
    idem: IdempotencyDep,
) -> Response:
    """Register an uploaded file as the next version; history is kept (permission
    ``document.upload``; only for a document you uploaded, or any visible one with
    ``document.manage_acl``: 403 ``document_owner_only`` otherwise). Get the upload with
    ``POST /documents/uploads`` and ``document_id``. Accepts ``Idempotency-Key``. An archived
    document answers 409 ``document_archived``."""
    return idem.run(
        db,
        body,
        lambda: service.add_version(db, ctx, document_id, body),
        status_code=202,
        headers=_created,
    )


@router.get("/documents/{document_id}/download-url", response_model=DownloadUrlOut)
def get_download_url(
    ctx: Reader,
    db: TenantDB,
    document_id: uuid.UUID,
    response: Response,
    version: Annotated[int | None, Query(ge=1, le=999_999)] = None,
) -> DownloadUrlOut:
    """A download link valid for 5 minutes, always saved as a file (permission
    ``document.read``). Only files that passed the malware scan are served (409 otherwise)."""
    response.headers["Cache-Control"] = "no-store"
    return service.get_download_url(db, ctx, document_id, version)


@router.put("/documents/{document_id}/acl", response_model=DocumentOut)
def set_acl(
    *,
    ctx: Manager,
    db: TenantDB,
    document_id: uuid.UUID,
    body: AclUpdate,
    version: IfMatch,
    response: Response,
) -> DocumentOut:
    """Replace who can see the document: roles, sections, classes or members (permission
    ``document.manage_acl``; ``If-Match``). An empty list limits it to school-wide readers."""
    doc = service.set_acl(db, ctx, document_id, body.acl, expected_version=version)
    response.headers["ETag"] = etag(doc.version)
    return doc


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(ctx: Manager, db: TenantDB, document_id: uuid.UUID) -> Response:
    """Delete the document, all versions and stored files (permission ``document.manage_acl``).
    Evidence still linked to a student record answers 409 ``document_in_use``."""
    service.delete_document(db, ctx, document_id)
    return Response(status_code=204)


@router.get("/documents/{document_id}/sheet", response_model=DocumentSheetOut)
def get_sheet(
    *,
    ctx: Reader,
    db: TenantDB,
    document_id: uuid.UUID,
    response: Response,
    limit: SheetLimit = 100,
    cursor: Cursor = None,
) -> DocumentSheetOut:
    """Open an XLSX or CSV document as a table (permission ``document.read``; FR-DOC-009):
    the first worksheet of the newest checked version, row 1 as column names, 100 rows per
    page. ``sheet_count`` says when the workbook has more sheets (not shown). Aadhaar-like
    numbers are masked; formulas are shown as text, never run. 415 ``not_a_sheet`` for other
    files, 413 above 10 MB (download instead), 403 for restricted (C3) files as downloads,
    409 ``import_file_sheet`` for files uploaded for an import (open them from the import).
    ``editable`` says whether you may save edits as a new version; ``ETag`` is needed to save."""
    response.headers["Cache-Control"] = "no-store"
    out = service.get_sheet(db, ctx, document_id, limit=limit, cursor=cursor)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/documents/{document_id}/sheet/versions", response_model=DocumentOut, status_code=202)
def save_sheet_version(
    *,
    ctx: Uploader,
    db: TenantDB,
    document_id: uuid.UUID,
    body: SheetSaveIn,
    version: IfMatch,
    idem: IdempotencyDep,
) -> Response:
    """Save edited cells as the next version (permission ``document.upload``; only for a document
    you uploaded, or any visible one with ``document.manage_acl``: 403 ``document_owner_only``
    otherwise; ``If-Match``; FR-DOC-010). The current file is kept in the history; the new version
    (values only, an XLSX) is checked for viruses and indexed like an upload (202). 409 for import
    files and CSVs, archived documents, workbooks with several sheets or with formulas (edit those
    in a spreadsheet program), when a newer version exists, or when nothing changed; 422 for a full
    Aadhaar number (enter only the last 4 digits) or line breaks. Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: service.save_sheet_version(db, ctx, document_id, body, expected_version=version),
        status_code=202,
        headers=_created,
    )


@router.post(
    "/documents/{document_id}/sheet/export", response_class=Response, responses=_SHEET_FILE_DOC
)
def export_sheet(
    ctx: Reader, db: TenantDB, document_id: uuid.UUID, body: SheetExportIn
) -> Response:
    """Download the sheet, with any unsaved edits, as CSV or XLSX (permission
    ``document.read``, as downloads; FR-DOC-011). Personal (C2) and restricted (C3) documents
    also need a recent sign-in with MFA (428 ``step_up_required``; FR-EXP-004). Aadhaar-like
    numbers masked, formulas neutralised, CSV in UTF-8 with BOM so Telugu opens in Excel, XLSX
    with the internal-checking watermark. Every download is audited."""
    file = service.export_sheet(db, ctx, document_id, body)
    return Response(
        content=file.content,
        media_type=file.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
