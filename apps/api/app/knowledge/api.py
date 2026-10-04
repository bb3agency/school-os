"""Knowledge routes: "Ask the school" (docs/09 Knowledge, docs/06 §5; FR-KB-001..012, FR-KB-030).

- ``POST /knowledge/ask`` (``kb.ask``): Server-Sent Events (docs/06 §5.1): ``meta`` (with
  ``conversation_id``, ``title``, ``cached``), ``status`` (progress codes), ``delta`` (streamed
  preview), ``error``, ``final`` (the validated answer, replacing the preview), ``token``,
  ``citation``, ``followups``, ``memory``, ``done``. Body: ``conversation_id`` (or the older
  ``session_id``), ``regenerate_of`` / ``edit_of`` (ADR-0034). The question's ``kb.queries``
  row (encrypted) and its audit event are written in the request's transaction, which commits
  BEFORE the first event is sent (invariant 7); the stream then completes the row
  (``kb.query.completed``) or, when the client goes away, records it ``cancelled``. Budget
  exhaustion or an outage is not an HTTP error: an ``error`` event carries the reason code and
  i18n key and the answer is search-only. Too many questions from one user answers 429.
- ``POST /knowledge/search`` (``document.read``): search-only retrieval, the text in the body
  (SEC-008). Filtered by the document ACL and your scopes in SQL.
- ``POST /knowledge/queries/{query_id}/feedback`` (``kb.ask``, own questions only; 404 else).
- ``GET /knowledge/verified-answers`` (``kb.ask``) and ``POST`` (``kb.verified_answer.manage``,
  ``Idempotency-Key``); ``POST .../{id}/review`` and ``.../{id}/retire``
  (``kb.verified_answer.manage``, ``If-Match``).
- Conversations (``kb.ask``, the caller's own only; 404 otherwise): ``GET
  /knowledge/conversations``, ``GET|PATCH|DELETE /knowledge/conversations/{id}`` (PATCH with
  ``If-Match``).
- Memory (``kb.ask``, the caller's own only; ADR-0034): ``GET|POST|DELETE
  /knowledge/memories``, ``PATCH|DELETE /knowledge/memories/{id}`` (PATCH with ``If-Match``),
  ``POST /knowledge/memories/{id}/confirm``, ``GET|PUT /knowledge/memory-settings``.

No write route here takes ``Idempotency-Key``: the idempotency store keeps response bodies for 24
hours, and these responses carry decrypted titles and memory text (docs/09 Knowledge). A
repeated PATCH needs the current ``If-Match``; DELETE and confirm are naturally idempotent.
"""

from __future__ import annotations

import datetime as dt
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
    ConversationDetailOut,
    ConversationOut,
    ConversationPatchIn,
    FeedbackIn,
    FeedbackOut,
    MemoryIn,
    MemoryOut,
    MemoryPatchIn,
    MemorySettingsIn,
    MemorySettingsOut,
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
        "description": "Server-Sent Events: meta, status, delta, error, final, token, "
        "citation, followups, memory, done (docs/06 §5.1).",
        "content": {
            SSE_MEDIA_TYPE: {
                "schema": {"type": "string"},
                "example": 'event: meta\ndata: {"query_id":"…","language":"en","mode":"full",'
                '"conversation_id":"…","title":"When do exams begin?","cached":false,'
                '"cached_from":null}\n\n'
                'event: status\ndata: {"step":"understanding","tool":null,"count":null}\n\n'
                'event: status\ndata: {"step":"searching_documents","tool":"search_documents",'
                '"count":null}\n\n'
                'event: status\ndata: {"step":"searching_documents","tool":"search_documents",'
                '"count":4}\n\n'
                'event: status\ndata: {"step":"writing","tool":null,"count":null}\n\n'
                'event: delta\ndata: {"text":"Exams begin on "}\n\n'
                'event: delta\ndata: {"text":"22/09/2026."}\n\n'
                'event: final\ndata: {"text":"Exams begin on 22/09/2026. [1]","replaced":false,'
                '"status":"answered","mode":"full"}\n\n'
                'event: token\ndata: {"text":"Exams begin on 22/09/2026. [1]"}\n\n'
                'event: citation\ndata: {"index":1,"source":"sos://doc/…/v2#p1",'
                '"title":"Circular · …","snippet":"…"}\n\n'
                'event: followups\ndata: {"questions":["Which classes write the first exam?"]}'
                "\n\n"
                'event: done\ndata: {"latency_ms":4120,"cited_sources":1,"status":"answered",'
                '"mode":"full"}\n\n',
            }
        },
    }
}


def _value(value: object) -> object:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, tuple):
        return [_value(v) for v in value]
    return value


def _sse(event: service.AskEvent) -> str:
    data = {k: _value(v) for k, v in asdict(event).items()}
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
    the preview. ``status`` events report progress as codes. Your earlier messages in the same
    conversation (``conversation_id``, or the older ``session_id``) are context for a follow-up
    (never another person's); the ``meta`` event names the conversation (a new one when you
    name none) and its title. ``regenerate_of`` / ``edit_of`` replace one of your latest
    messages (409 ``message_superseded``, ``message_not_revisable``; 404 for anyone else's).
    ``followups`` suggests up to 3 next questions; ``memory`` reports an item saved ("remember
    that ...") or suggested (confirm it in memory settings). When the school's AI budget is
    used up or AI answers are unavailable, you get ranked, cited passages instead (``mode:
    search_only``). 429 ``ai_rate_limited`` when you ask too many questions a minute.
    A question may be written in English, Telugu or both; the answer, follow-ups and title are
    in English (``meta.language`` is ``en``) unless Telugu is switched on for the deployment.
    """
    svc = service.get_service()
    svc.admit(ctx)
    stream = svc.start_stream(
        db,
        ctx,
        service.AskRequest(
            question=body.question or "",
            session_id=body.session_id,
            conversation_id=body.conversation_id,
            regenerate_of=body.regenerate_of,
            edit_of=body.edit_of,
        ),
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


# --- conversations (ADR-0034; docs/09 Knowledge) -------------------------------------------------


def _conversation_cursor(cursor: str | None) -> tuple[bool, dt.datetime, uuid.UUID] | None:
    after = decode_cursor(cursor)
    if after is None:
        return None
    try:
        return (
            bool(after["p"]),
            dt.datetime.fromisoformat(str(after["u"])),
            uuid.UUID(str(after["k"])),
        )
    except (KeyError, ValueError) as exc:
        raise ValidationFailed(
            [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid_cursor"}]
        ) from exc


@router.get("/knowledge/conversations", response_model=Page[ConversationOut])
def list_conversations(
    *, ctx: Asker, db: TenantDB, limit: Limit = 50, cursor: Cursor = None
) -> Page[ConversationOut]:
    """Your Ask conversations in this school (permission ``kb.ask``; only your own), pinned
    first, then newest activity first. Titles are decrypted for you only."""
    items, last = service.get_service().list_conversations(
        db, ctx, limit=limit, after=_conversation_cursor(cursor)
    )
    next_cursor = (
        encode_cursor({"p": last[0], "u": last[1].isoformat(), "k": str(last[2])}) if last else None
    )
    return Page[ConversationOut](data=items, next_cursor=next_cursor)


@router.get("/knowledge/conversations/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(
    ctx: Asker, db: TenantDB, conversation_id: uuid.UUID, response: Response
) -> ConversationDetailOut:
    """One of your conversations with every message, oldest first (``superseded`` marks one
    replaced by a regenerate or an edit). Sources you can no longer see are withheld
    (``withheld``: no title or snippet; that answer and its follow-ups are withheld too). 404
    for anyone else's, another school's or a deleted conversation."""
    out = service.get_service().get_conversation(db, ctx, conversation_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.patch("/knowledge/conversations/{conversation_id}", response_model=ConversationOut)
def update_conversation(
    *,
    ctx: Asker,
    db: TenantDB,
    conversation_id: uuid.UUID,
    body: ConversationPatchIn,
    version: IfMatch,
    response: Response,
) -> ConversationOut:
    """Rename and/or pin one of your conversations (``If-Match``). A title is 1-120 characters
    and may not hold an Aadhaar-like number (422 ``title_personal_number``). 412 when it changed
    since you read it; 404 for anyone else's."""
    out = service.get_service().update_conversation(
        db, ctx, conversation_id, body, expected_version=version
    )
    response.headers["ETag"] = etag(out.version)
    return out


@router.delete("/knowledge/conversations/{conversation_id}", status_code=204)
def delete_conversation(ctx: Asker, db: TenantDB, conversation_id: uuid.UUID) -> Response:
    """Remove one of your conversations from your history (at once, everywhere). Its questions
    stay in the school's encrypted query log until it is purged 180 days after they were
    asked (docs/08 §7). 404 for anyone else's or one already removed."""
    service.get_service().delete_conversation(db, ctx, conversation_id)
    return Response(status_code=204)


# --- memory (ADR-0034) -------------------------------------------------------------------------


@router.get("/knowledge/memories", response_model=Page[MemoryOut])
def list_memories(ctx: Asker, db: TenantDB) -> Page[MemoryOut]:
    """Your memory items in this school (permission ``kb.ask``): confirmed and pending
    suggestions, newest first. Only your own; they are used only while memory is on."""
    return Page[MemoryOut](data=service.get_service().list_memories(db, ctx), next_cursor=None)


@router.post("/knowledge/memories", response_model=MemoryOut, status_code=201)
def create_memory(ctx: Asker, db: TenantDB, body: MemoryIn) -> MemoryOut:
    """Add a memory item: your own preference or work context (e.g. "Keep answers short").
    Never details about students, parents or other staff: refused with 422
    (``memory_personal_number``, ``memory_date``, ``memory_long_number``, ``memory_others``,
    ``memory_unsure``, ``memory_too_long``); 503 ``memory_check_unavailable`` when the check
    cannot run; 409 ``memory_off`` or ``memory_full``; 429 ``ai_rate_limited`` (the item check
    counts against your per-minute question limit)."""
    return service.get_service().create_memory(db, ctx, body)


@router.delete("/knowledge/memories", status_code=204)
def forget_memories(ctx: Asker, db: TenantDB) -> Response:
    """Forget everything: delete all your memory items in this school."""
    service.get_service().forget_memories(db, ctx)
    return Response(status_code=204)


@router.patch("/knowledge/memories/{memory_id}", response_model=MemoryOut)
def update_memory(
    *,
    ctx: Asker,
    db: TenantDB,
    memory_id: uuid.UUID,
    body: MemoryPatchIn,
    version: IfMatch,
    response: Response,
) -> MemoryOut:
    """Edit one of your memory items (``If-Match``; checked again like a new item, 429
    ``ai_rate_limited`` included)."""
    out = service.get_service().update_memory(db, ctx, memory_id, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.delete("/knowledge/memories/{memory_id}", status_code=204)
def delete_memory(ctx: Asker, db: TenantDB, memory_id: uuid.UUID) -> Response:
    """Delete one of your memory items."""
    service.get_service().delete_memory(db, ctx, memory_id)
    return Response(status_code=204)


@router.post("/knowledge/memories/{memory_id}/confirm", response_model=MemoryOut)
def confirm_memory(ctx: Asker, db: TenantDB, memory_id: uuid.UUID) -> MemoryOut:
    """Keep a suggested item (it is used from now on). A suggestion not confirmed within 24
    hours is deleted (404 afterwards)."""
    return service.get_service().confirm_memory(db, ctx, memory_id)


@router.get("/knowledge/memory-settings", response_model=MemorySettingsOut)
def get_memory_settings(ctx: Asker, db: TenantDB) -> MemorySettingsOut:
    """Whether memory is on for you (``enabled``) and for the school (``school_enabled``)."""
    return service.get_service().memory_settings(db, ctx)


@router.put("/knowledge/memory-settings", response_model=MemorySettingsOut)
def put_memory_settings(ctx: Asker, db: TenantDB, body: MemorySettingsIn) -> MemorySettingsOut:
    """Turn memory on or off for you. Off: nothing is saved, suggested or used (your items stay
    listed so you can delete them)."""
    return service.get_service().set_memory_settings(db, ctx, body)
