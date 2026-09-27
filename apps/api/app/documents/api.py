"""Document routes (docs/09 Documents; FR-DOC-001..006, FR-DOC-008, SEC-016).

Uploads go straight from the browser to S3 with a presigned POST; the API then registers the
object after checking it (size, magic bytes, hash) and a worker scans it for malware. Reads
are filtered by the document ACL and the caller's scopes (404 outside them). Downloads are
presigned GETs valid for at most 5 minutes that always download as attachments.
"""

from __future__ import annotations

import uuid
from typing import Annotated

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
    DocumentStatus,
    DocumentUpdate,
    DownloadUrlOut,
    Purpose,
    UploadCreate,
    UploadOut,
    VersionCreate,
)

router = APIRouter(prefix="/api/v1", tags=["documents"])

Uploader = Annotated[UserContext, Depends(require(service.UPLOAD))]
Reader = Annotated[UserContext, Depends(require(service.READ))]
Manager = Annotated[UserContext, Depends(require(service.MANAGE, scope="school"))]


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
    Send ``document_id`` to upload a new version (409 ``document_archived`` for an archived
    document). Accepts ``Idempotency-Key``.
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
    document must be visible to you, as for a new version; ``If-Match``). An archived document
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
    ``document.upload``). Get the upload with ``POST /documents/uploads`` and ``document_id``.
    Accepts ``Idempotency-Key``. An archived document answers 409 ``document_archived``."""
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
