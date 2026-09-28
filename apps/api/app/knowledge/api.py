"""Knowledge routes: "Ask the school" (docs/09 Knowledge, docs/06 §5; FR-KB-001..012, FR-KB-030).

- ``POST /knowledge/ask`` (``kb.ask``): Server-Sent Events (docs/06 §5.1): ``meta``,
  ``delta`` (streamed preview), ``error``, ``final`` (the validated answer, replacing the
  preview), ``token``, ``citation``, ``done``. The question's ``kb.queries`` row (encrypted)
  and its audit event are written in the request's transaction, which commits BEFORE the first
  event is sent (invariant 7); the stream then completes the row (``kb.query.completed``) or,
  when the client goes away, records it ``cancelled``. Budget exhaustion or an outage is not an
  HTTP error: an ``error`` event carries the reason code and i18n key and the answer is
  search-only. Too many questions from one user answers 429.
- ``POST /knowledge/search`` (``document.read``): search-only retrieval, the text in the body
  (SEC-008). Filtered by the document ACL and your scopes in SQL.
- ``POST /knowledge/queries/{query_id}/feedback`` (``kb.ask``, own questions only; 404 else).
- ``GET /knowledge/verified-answers`` (``kb.ask``) and ``POST`` (``kb.verified_answer.manage``,
  ``Idempotency-Key``); ``POST .../{id}/review`` and ``.../{id}/retire``
  (``kb.verified_answer.manage``, ``If-Match``).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from dataclasses import asdict
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

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
from app.knowledge import service
from app.knowledge.schemas import (
    AskIn,
    FeedbackIn,
    FeedbackOut,
    SearchIn,
    SearchOut,
    VerifiedAnswerIn,
    VerifiedAnswerOut,
    VerifiedAnswerReviewIn,
    VerifiedStatus,
)

router = APIRouter(prefix="/api/v1", tags=["knowledge"])

Asker = Annotated[UserContext, Depends(require(service.ASK))]
Searcher = Annotated[UserContext, Depends(require(service.SEARCH))]
Verifier = Annotated[UserContext, Depends(require(service.MANAGE_VERIFIED))]

SSE_MEDIA_TYPE = "text/event-stream"
_SSE_DOC: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Server-Sent Events: meta, delta, error, final, token, citation, done "
        "(docs/06 §5.1).",
        "content": {
            SSE_MEDIA_TYPE: {
                "schema": {"type": "string"},
                "example": 'event: meta\ndata: {"query_id":"…","language":"en","mode":"full"}\n\n'
                'event: delta\ndata: {"text":"Exams begin on "}\n\n'
                'event: delta\ndata: {"text":"22/09/2026."}\n\n'
                'event: final\ndata: {"text":"Exams begin on 22/09/2026. [1]","replaced":false,'
                '"status":"answered","mode":"full"}\n\n'
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


async def _sse_stream(stream: service.AskStream) -> AsyncGenerator[str, None]:
    """The events as SSE frames. Each step of the (synchronous) stream runs in the thread
    pool; when the client disconnects, Starlette cancels this generator and ``finally`` closes
    the stream, which stops the provider call and records the question ``cancelled``."""
    try:
        while True:
            event = await run_in_threadpool(next, stream, None)
            if event is None:
                return
            yield _sse(event)
    finally:
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(stream.close)


@router.post(
    "/knowledge/ask",
    response_class=StreamingResponse,
    responses=_SSE_DOC,
)
def ask(ctx: Asker, db: TenantDB, body: AskIn) -> StreamingResponse:
    """Ask a question of the school's records and documents (permission ``kb.ask``).

    Answers cite their sources (``sos://`` URIs) or say the answer was not found in the school
    records you can access. Only records and documents you may see are used. The answer streams
    as ``delta`` events (a preview); the ``final`` event carries the checked answer and replaces
    the preview. Questions asked earlier in the same ``session_id`` by you are context for a
    follow-up (never another person's). When the school's AI budget is used up or AI answers
    are unavailable, you get ranked, cited passages instead (``mode: search_only``). 429
    ``ai_rate_limited`` when you ask too many questions a minute.
    """
    svc = service.get_service()
    svc.admit(ctx)
    stream = svc.start_stream(
        db, ctx, service.AskRequest(question=body.question, session_id=body.session_id)
    )
    return StreamingResponse(
        _sse_stream(stream),
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


@router.post("/knowledge/verified-answers/{answer_id}/review", response_model=VerifiedAnswerOut)
def review_verified_answer(
    *,
    ctx: Verifier,
    db: TenantDB,
    answer_id: uuid.UUID,
    body: VerifiedAnswerReviewIn,
    version: IfMatch,
    response: Response,
) -> VerifiedAnswerOut:
    """Confirm a verified answer again, as it is or corrected (permission
    ``kb.verified_answer.manage``; ``If-Match``; FR-KB-030). Use it for answers flagged
    ``needs_review`` after a cited document changed. Its citations (new ones if you send them)
    must quote the current version of documents you can read (422 as on create); it becomes
    ``active`` and you become its verifier. 404 when you cannot read a document it cites; 409
    ``verified_answer_retired``; 412 when it changed since you read it."""
    out = service.get_service().review_verified_answer(
        db, ctx, answer_id, body, expected_version=version
    )
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/knowledge/verified-answers/{answer_id}/retire", response_model=VerifiedAnswerOut)
def retire_verified_answer(
    ctx: Verifier, db: TenantDB, answer_id: uuid.UUID, version: IfMatch, response: Response
) -> VerifiedAnswerOut:
    """Withdraw a verified answer (permission ``kb.verified_answer.manage``; ``If-Match``): it
    is no longer used by Ask; kept for the record. 409 ``verified_answer_retired`` when it
    already is; 404 / 412 as for review."""
    out = service.get_service().retire_verified_answer(db, ctx, answer_id, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out
