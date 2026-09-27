"""Knowledge routes: "Ask the school" (docs/09 Knowledge, docs/06 §5; FR-KB-001..012, FR-KB-030).

- ``POST /knowledge/ask`` (``kb.ask``): Server-Sent Events ``meta``, ``token``, ``citation``,
  ``error``, ``done`` (docs/06 §5.1). The whole answer is produced, validated, stored
  (encrypted) and audited inside the request's transaction, which commits BEFORE the first
  event is sent: nothing is shown that was not recorded (invariant 7). Budget exhaustion or an
  outage is not an HTTP error: ``meta`` says ``mode: search_only`` and an ``error`` event
  carries the reason code and i18n key. Too many questions from one user answers 429.
- ``POST /knowledge/search`` (``document.read``): search-only retrieval, the text in the body
  (SEC-008). Filtered by the document ACL and your scopes in SQL.
- ``POST /knowledge/queries/{query_id}/feedback`` (``kb.ask``, own questions only; 404 else).
- ``GET /knowledge/verified-answers`` (``kb.ask``) and ``POST`` (``kb.verified_answer.manage``,
  ``Idempotency-Key``).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import (
    Cursor,
    IdempotencyDep,
    Limit,
    Page,
    decode_cursor,
    encode_cursor,
)
from app.core.errors import ValidationFailed
from app.knowledge import service
from app.knowledge.schemas import (
    AskIn,
    FeedbackIn,
    FeedbackOut,
    SearchIn,
    SearchOut,
    VerifiedAnswerIn,
    VerifiedAnswerOut,
    VerifiedStatus,
)

router = APIRouter(prefix="/api/v1", tags=["knowledge"])

Asker = Annotated[UserContext, Depends(require(service.ASK))]
Searcher = Annotated[UserContext, Depends(require(service.SEARCH))]
Verifier = Annotated[UserContext, Depends(require(service.MANAGE_VERIFIED))]

SSE_MEDIA_TYPE = "text/event-stream"
_SSE_DOC: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Server-Sent Events: meta, token, citation, error, done (docs/06 §5.1).",
        "content": {
            SSE_MEDIA_TYPE: {
                "schema": {"type": "string"},
                "example": 'event: meta\ndata: {"query_id":"…","language":"en","mode":"full"}\n\n'
                'event: token\ndata: {"text":"Exams begin on 22/09/2026. [1]"}\n\n'
                'event: citation\ndata: {"index":1,"source":"sos://doc/…/v2#p1",'
                '"title":"Circular · …","snippet":"…"}\n\n'
                'event: done\ndata: {"latency_ms":4120,"cited_sources":1}\n\n',
            }
        },
    }
}


def _sse(event: service.AskEvent) -> str:
    data = {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in asdict(event).items()}
    return f"event: {event.event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post(
    "/knowledge/ask",
    response_class=StreamingResponse,
    responses=_SSE_DOC,
)
def ask(ctx: Asker, db: TenantDB, body: AskIn) -> StreamingResponse:
    """Ask a question of the school's records and documents (permission ``kb.ask``).

    Answers cite their sources (``sos://`` URIs) or say the answer was not found in the school
    records you can access. Only records and documents you may see are used. When the school's
    AI budget is used up or AI answers are unavailable, you get ranked, cited passages instead
    (``mode: search_only``). 429 ``ai_rate_limited`` when you ask too many questions a minute.
    """
    svc = service.get_service()
    svc.admit(ctx)
    events = svc.answer(
        db, ctx, service.AskRequest(question=body.question, session_id=body.session_id)
    )
    payload = [_sse(e) for e in events]

    def stream() -> Iterator[str]:
        yield from payload

    return StreamingResponse(
        stream(),
        media_type=SSE_MEDIA_TYPE,
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/knowledge/search", response_model=SearchOut)
def search(ctx: Searcher, db: TenantDB, body: SearchIn) -> SearchOut:
    """Ranked passages from documents you can read (permission ``document.read``): the
    search-only view of Ask, without generated prose. The query goes in the body (SEC-008)."""
    filters = service.SearchFilters(
        doc_types=frozenset(body.doc_types) if body.doc_types is not None else None,
        from_date=body.from_date,
    )
    results = service.get_service().search_results(
        db, ctx, body.query, filters=filters, k=body.limit
    )
    return SearchOut(data=results)


@router.post("/knowledge/queries/{query_id}/feedback", response_model=FeedbackOut)
def feedback(ctx: Asker, db: TenantDB, query_id: uuid.UUID, body: FeedbackIn) -> FeedbackOut:
    """Mark one of YOUR answers helpful or not (permission ``kb.ask``; a reason code, never
    free text). Other people's questions answer 404."""
    return service.get_service().feedback(db, ctx, query_id, body)


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


@router.get("/knowledge/verified-answers", response_model=Page[VerifiedAnswerOut])
def list_verified_answers(
    *,
    ctx: Asker,
    db: TenantDB,
    limit: Limit = 50,
    cursor: Cursor = None,
    status: VerifiedStatus | None = None,
) -> Page[VerifiedAnswerOut]:
    """Verified answers (permission ``kb.ask``), newest first. Only answers whose cited
    documents you can all read are listed."""
    items, last = service.get_service().list_verified_answers(
        db, ctx, status=status, limit=limit, before_id=_before(cursor)
    )
    return Page[VerifiedAnswerOut](
        data=items, next_cursor=encode_cursor({"k": str(last)}) if last else None
    )


@router.post("/knowledge/verified-answers", response_model=VerifiedAnswerOut, status_code=201)
def create_verified_answer(
    ctx: Verifier, db: TenantDB, body: VerifiedAnswerIn, idem: IdempotencyDep
) -> Any:
    """Record an approved answer to a recurring question (permission
    ``kb.verified_answer.manage``; FR-KB-030). Each citation must quote the current version of
    a document you can read (422 ``citation_not_found``, ``citation_not_current``,
    ``citation_text_not_found``, ``citation_source_unsupported``). It is flagged for review when
    a cited document changes or is deleted. Accepts ``Idempotency-Key``."""
    return idem.run(db, body, lambda: service.get_service().create_verified_answer(db, ctx, body))
