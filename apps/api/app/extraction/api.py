"""Register-photo extraction routes (docs/09 Imports and extraction; US-402, FR-IMP-020..024).

Photos are uploaded first as ``register_scan`` documents (``POST /documents/uploads`` +
``POST /documents``). A batch reads them in the background; each row found waits in the
verification queue until a person confirms (with edits) or rejects it. Nothing becomes a student
record without that confirmation.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, IdempotencyDep, Limit, Page, decode_cursor
from app.core.errors import ValidationFailed
from app.extraction import service
from app.extraction.schemas import (
    BatchCreate,
    BatchDetail,
    BatchOut,
    ItemConfirm,
    ItemDetail,
    ItemOut,
    ItemReject,
    ItemStatus,
)

router = APIRouter(prefix="/api/v1", tags=["extraction"])

Runner = Annotated[UserContext, Depends(require(service.RUN, scope="school"))]
Committer = Annotated[UserContext, Depends(require(service.COMMIT, scope="school"))]


def _cursor_id(cursor: str | None) -> uuid.UUID | None:
    after = decode_cursor(cursor)
    if after is None:
        return None
    try:
        return uuid.UUID(str(after.get("k")))
    except ValueError as exc:
        raise ValidationFailed(
            [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid_cursor"}]
        ) from exc


@router.get("/extraction-batches", response_model=Page[BatchOut])
def list_batches(
    ctx: Runner, db: TenantDB, limit: Limit = 50, cursor: Cursor = None
) -> Page[BatchOut]:
    """Register-photo batches, newest first, with progress counters (permission
    ``import.run``)."""
    return service.list_batches(db, ctx, limit=limit, before_id=_cursor_id(cursor))


@router.post("/extraction-batches", response_model=BatchOut, status_code=202)
def create_batch(ctx: Runner, db: TenantDB, body: BatchCreate, idem: IdempotencyDep) -> Response:
    """Read register-page photos into the verification queue (permission ``import.run``).

    Send the ids of ``register_scan`` documents that passed the malware scan: JPG or PNG, one
    page each (PDF answers 422 ``pdf_not_supported`` for now: upload a photo of each page).
    Answers 202; follow progress with ``GET /extraction-batches/{id}``. Accepts
    ``Idempotency-Key``.
    """
    return idem.run(
        db,
        body,
        lambda: service.create_batch(db, ctx, body),
        status_code=202,
        headers=lambda b: {"Location": f"/api/v1/extraction-batches/{b.id}"},
    )


@router.get("/extraction-batches/{batch_id}", response_model=BatchDetail)
def get_batch(ctx: Runner, db: TenantDB, batch_id: uuid.UUID) -> BatchDetail:
    """One batch with progress per page (US-402 AC4). A page with ``image_withheld`` showed a
    full Aadhaar number: its image is not shown (permission ``import.run``)."""
    return service.get_batch(db, ctx, batch_id)


@router.get("/extraction-items", response_model=Page[ItemOut])
def list_items(
    *,
    ctx: Runner,
    db: TenantDB,
    batch_id: uuid.UUID | None = None,
    status: ItemStatus | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[ItemOut]:
    """The verification queue in page order (permission ``import.run``). Each field carries its
    confidence and region; ``low_confidence_fields`` lists the ones to check carefully."""
    return service.list_items(
        db, ctx, batch_id=batch_id, status=status, limit=limit, after_id=_cursor_id(cursor)
    )


@router.get("/extraction-items/{item_id}", response_model=ItemDetail)
def get_item(ctx: Runner, db: TenantDB, item_id: uuid.UUID, response: Response) -> ItemDetail:
    """One row with a 5-minute link to its page image and students with the same admission
    number (permission ``import.run``; the image also needs ``document.read`` on the page)."""
    response.headers["Cache-Control"] = "no-store"
    return service.get_item(db, ctx, item_id)


@router.post("/extraction-items/{item_id}/confirm", response_model=ItemOut)
def confirm_item(
    ctx: Committer, db: TenantDB, item_id: uuid.UUID, body: ItemConfirm, idem: IdempotencyDep
) -> Response:
    """Save the row as you read it on the page (permission ``import.commit``).

    Creates a student (or adds to ``student_id``) with source ``admission_register`` and the
    page as evidence; creating a student also needs ``student.create`` (403
    ``student_create_required``). A row already checked answers 409 ``item_already_reviewed``;
    changing an
    existing register identity value answers 403 ``identity_change_required`` (use a change
    request). Accepts ``Idempotency-Key``.
    """
    return idem.run(db, body, lambda: service.confirm_item(db, ctx, item_id, body), status_code=200)


@router.post("/extraction-items/{item_id}/reject", response_model=ItemOut)
def reject_item(ctx: Committer, db: TenantDB, item_id: uuid.UUID, body: ItemReject) -> ItemOut:
    """Discard a row that is not a student entry; nothing is recorded (permission
    ``import.commit``)."""
    return service.reject_item(db, ctx, item_id, body)
