"""Knowledge-gap routes (FA-06; FR-KB-081..FR-KB-095; docs/09 Knowledge gaps).

Every route needs ``kb.verified_answer.manage`` (the people who keep the library: principal and
office admin). A gap shows one REDACTED sample question and counts, never who asked.

- ``GET /knowledge/gaps`` (``status`` open|closed|dismissed): audited ``kb.gaps.viewed``.
- ``GET /knowledge/gaps/summary``: counts only.
- ``POST /knowledge/gaps/{id}/verified-answer`` (``If-Match``): write a verified answer for the
  gap and close it (same rules as ``POST /knowledge/verified-answers``).
- ``POST /knowledge/gaps/{id}/close`` (``If-Match``): close with an existing verified answer or
  the document that was missing, or dismiss.

No ``Idempotency-Key``: responses carry decrypted (redacted) question text, which the
idempotency store must not keep; a repeat needs the current ``If-Match`` and answers 409.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, IfMatch, Limit, Page, decode_cursor, encode_cursor, etag
from app.core.errors import ValidationFailed
from app.knowledge import gaps, service
from app.knowledge.gap_schemas import KnowledgeGapCloseIn, KnowledgeGapOut, KnowledgeGapSummaryOut
from app.knowledge.schemas import VerifiedAnswerIn

router = APIRouter(prefix="/api/v1", tags=["knowledge"])

Keeper = Annotated[UserContext, Depends(require(gaps.MANAGE))]


def _offset(cursor: str | None) -> int:
    after = decode_cursor(cursor)
    if after is None:
        return 0
    value = after.get("o")
    if not isinstance(value, int) or value < 0:
        raise ValidationFailed(
            [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid_cursor"}]
        )
    return value


@router.get("/knowledge/gaps", response_model=Page[KnowledgeGapOut])
def list_knowledge_gaps(
    *,
    ctx: Keeper,
    db: TenantDB,
    status: Annotated[Literal["open", "closed", "dismissed"], Query()] = "open",
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[KnowledgeGapOut]:
    """Questions Ask could not answer (permission ``kb.verified_answer.manage``; FR-KB-085):
    open gaps most asked first, closed and dismissed ones newest first. Each shows one sample
    question with numbers, phone numbers and emails masked, and how often it came back "not
    found", unchecked or "not helpful"; never who asked. Audited ``kb.gaps.viewed``."""
    offset = _offset(cursor)
    items, more = gaps.list_gaps(db, ctx, status=status, limit=limit, offset=offset)
    return Page[KnowledgeGapOut](
        data=items, next_cursor=encode_cursor({"o": offset + limit}) if more else None
    )


@router.get("/knowledge/gaps/summary", response_model=KnowledgeGapSummaryOut)
def knowledge_gap_summary(ctx: Keeper, db: TenantDB) -> KnowledgeGapSummaryOut:
    """Counts only (permission ``kb.verified_answer.manage``; FR-KB-090): open gaps by signal
    and gaps closed or dismissed per month (this month and the two before, IST)."""
    return gaps.summary(db, ctx)


@router.post("/knowledge/gaps/{gap_id}/verified-answer", response_model=KnowledgeGapOut)
def answer_knowledge_gap(
    *,
    ctx: Keeper,
    db: TenantDB,
    gap_id: uuid.UUID,
    body: VerifiedAnswerIn,
    version: IfMatch,
    response: Response,
) -> KnowledgeGapOut:
    """Write a verified answer for this gap and close it (permission
    ``kb.verified_answer.manage``; ``If-Match``; FR-KB-087). The answer follows every rule of
    ``POST /knowledge/verified-answers`` (422 ``citation_*``, ``answer_personal_data``) and is
    then searched by Ask like any other, only for people who can read every document it cites.
    404 unknown gap; 409 ``knowledge_gap_closed``; 412 when it changed since you read it."""
    out = gaps.answer_gap(
        db,
        ctx,
        gap_id,
        lambda: service.get_service().create_verified_answer(db, ctx, body),
        expected_version=version,
    )
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/knowledge/gaps/{gap_id}/close", response_model=KnowledgeGapOut)
def close_knowledge_gap(
    *,
    ctx: Keeper,
    db: TenantDB,
    gap_id: uuid.UUID,
    body: KnowledgeGapCloseIn,
    version: IfMatch,
    response: Response,
) -> KnowledgeGapOut:
    """Close this gap with an existing active verified answer (every document it cites must
    be one you can read; 422 ``gap_answer_not_usable``) or the document that was missing
    (one you can read; 422 ``gap_document_not_found``), or dismiss it (permission
    ``kb.verified_answer.manage``; ``If-Match``; FR-KB-088, FR-KB-089). 404 / 409 / 412 as
    above."""
    out = gaps.close_gap(db, ctx, gap_id, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


__all__ = ["router"]
