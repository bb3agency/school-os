"""Knowledge public API ("Ask the school"; docs/06). Other modules import only this module.

What other modules and the routes use:

- :func:`get_service` -> :class:`SchoolKnowledgeService`, the :class:`KnowledgeService`:
  ``ask`` (the SSE events of docs/06 §5.1 for one question), ``search_documents`` (search-only
  retrieval, also the fallback mode), query feedback and verified answers (FR-KB-030).
- :func:`reencrypt_queries` (DEK rotation, SEC-012): re-encrypts ``kb.queries`` to the
  school's active key version in the caller's ``tenant_session``; register it with
  ``register_reencryptor("kb_queries", reencrypt_queries)``.
- :func:`purge_old_queries` (retention, docs/05 §13): deletes the school's ``kb.queries`` rows
  older than the query-log retention (180 days); the daily ``knowledge.purge_queries`` job.
- :class:`IngestionPipeline` (worker jobs reacting to ``documents``' events; wired by the
  composition root, :mod:`app.knowledge.composition`).
- The value types below, re-exported so callers never import ``app.knowledge.domain``.

Rules every call keeps (docs/06, CLAUDE.md §6): routes check ``kb.ask`` (``document.read`` for
search, ``kb.verified_answer.manage`` for writes) and the service checks again; retrieval
filters by tenant and permissions in SQL before ranking (invariant 8, FR-KB-002); the model sees
only passages built from what the caller may read (``search_result`` blocks or numbered
passages, ADR-0033); citations are validated
server-side (FR-KB-005) and an unsupported answer says "not found in school records"; tools are
read-only (invariant 9, ADR-0008); every provider call goes through ``knowledge.gateway``
(ADR-0005). Each question writes one ``kb.queries`` row (question and answer encrypted under the
school's key, a keyed HMAC of the question, ids, codes and counts; FR-KB-009) and one audit
event ``kb.query.asked`` (ids and counts only; invariant 7) in the caller's transaction, BEFORE
any event is returned: nothing is shown that was not recorded. Logs never carry question or
answer text (invariant 5).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
import time
import uuid
from collections.abc import Generator, Iterator
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from app.audit import service as audit
from app.authz.kv import KVUnavailable, kv_store
from app.core import languages, retention
from app.core import purge as purging
from app.core.db import tenant_session
from app.core.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PreconditionFailed,
    ServiceUnavailable,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.records import RecordTable
from app.core.redaction import mask_aadhaar, redact
from app.core.textnorm import nfc
from app.documents import service as documents
from app.identity import service as identity
from app.knowledge import (
    answer_cache,
    circular_ai,
    composition,
    conversations,
    keys,
    memory,
    sources,
)
from app.knowledge import repository as repo
from app.knowledge.answer import (
    Answer,
    AskContext,
    CitedSource,
    Progress,
    answer_language,
    detect_language,
    elapsed_ms,
    normalise,
)
from app.knowledge.circular_ai import (
    NO_TEXT as CIRCULAR_NO_TEXT,
)
from app.knowledge.circular_ai import (
    AiUnavailable,
    ReadingOutcome,
    circular_passages,
    draft_notice,
    read_circular,
)
from app.knowledge.circulars.notice import (
    ConfirmedDeadline,
    NoticeDraft,
    NoticeSource,
    has_personal_numbers,
)
from app.knowledge.circulars.reading import (
    CircularContext,
    CircularReading,
    DeadlineSuggestion,
    Passage,
    PassageCitation,
)
from app.knowledge.config.circulars import CircularsConfig
from app.knowledge.config.embeddings import load_embeddings_config
from app.knowledge.config.llm import load_llm_config
from app.knowledge.domain import (
    AclKeys,
    AnswerSegment,
    AskEvent,
    AskMode,
    AskRequest,
    Citation,
    CitationEvent,
    DeltaEvent,
    DoneEvent,
    ErrorEvent,
    FinalEvent,
    FollowupsEvent,
    HistoryTurn,
    Locale,
    MemoryEvent,
    MetaEvent,
    Metering,
    RankedChunk,
    SearchFilters,
    StatusEvent,
    StepUpdate,
    TokenEvent,
)
from app.knowledge.gateway.errors import AiRateLimited
from app.knowledge.gateway.factory import require_provider_agreements
from app.knowledge.ingestion.pipeline import INDEXED_HOOKS, IndexedHook
from app.knowledge.interfaces import IngestionPipeline, KnowledgeService
from app.knowledge.keys import (
    ANSWER_COLUMN,
    QUESTION_COLUMN,
    QUESTION_PURPOSE,
    question_key,
    reencrypt_queries,
    reencrypt_queries_batch,
)
from app.knowledge.models import Conversation, Query, UserMemory, VerifiedAnswer
from app.knowledge.schemas import (
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
    MessageCitationOut,
    MessageOut,
    SearchResultOut,
    VerifiedAnswerIn,
    VerifiedAnswerOut,
    VerifiedAnswerReviewIn,
    VerifiedCitationOut,
)
from app.knowledge.tools.access import acl_keys
from app.knowledge.tools.registry import offered
from app.knowledge.visibility import SourceVisibility
from app.ops import service as ops
from app.students import crypto
from app.students import rotation as key_rotation
from app.tenancy import service as tenancy

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext
    from app.core.config import Settings

log = get_logger(__name__)

ASK: Final = "kb.ask"
SEARCH: Final = "document.read"
MANAGE_VERIFIED: Final = "kb.verified_answer.manage"
READ_SENSITIVE: Final = "student.read_sensitive"
QUERIES_TABLE: Final = "kb.queries"
MAX_QUESTION_CHARS: Final = 1000
INTERNAL_ERROR: Final = "internal_error"
INTERNAL_ERROR_KEY: Final = "kb.errors.internal"


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _result_out(chunk: RankedChunk) -> SearchResultOut:
    return SearchResultOut(
        source=chunk.source,
        document_id=chunk.document_id,
        version_no=chunk.version_no,
        page_from=chunk.page_from,
        page_to=chunk.page_to,
        doc_type=chunk.doc_type,
        title=chunk.title,
        issued_on=chunk.issued_on,
        snippet=chunk.content,
        score=chunk.score,
    )


def _verified_out(row: VerifiedAnswer, verified_by_name: str | None) -> VerifiedAnswerOut:
    return VerifiedAnswerOut(
        id=row.id,
        question=row.question_canonical,
        language=row.language,
        answer_text=row.answer_text,
        citations=[VerifiedCitationOut.model_validate(c) for c in row.citations],
        status=row.status,
        verified_by=row.verified_by,
        verified_by_name=verified_by_name,
        verified_at=row.verified_at,
        review_due=row.review_due,
        version=row.version,
        created_at=row.created_at,
    )


@dataclass(frozen=True, slots=True)
class AskResponse:
    query_id: uuid.UUID
    answer: Answer
    events: tuple[AskEvent, ...]
    followups: tuple[str, ...] = ()
    conversation_id: uuid.UUID | None = None


class SchoolKnowledgeService:
    """:class:`KnowledgeService` over the process runtime (:mod:`app.knowledge.composition`)."""

    def __init__(self, runtime: composition.Runtime | None = None) -> None:
        self._runtime = runtime

    @property
    def runtime(self) -> composition.Runtime:
        return self._runtime or composition.runtime()

    # --- ask ------------------------------------------------------------------------------------

    def admit(self, ctx: UserContext) -> None:
        """Before streaming: permission and the per-user question rate (docs/06 §5 step 1)."""
        if not ctx.has(ASK):
            raise Forbidden()
        limit = self.runtime.llm_config.rate_limit.questions_per_minute_per_user
        minute = dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M")
        key = f"sos:rl:kb:ask:{ctx.tenant_id}:{ctx.user_id}:{minute}"
        try:
            count = kv_store().incr(key, ttl_s=120)
        except KVUnavailable:
            return  # fail open: the school's budget and the gateway's rate limit still apply
        if count > limit:
            raise AiRateLimited("Too many questions. Wait a minute and try again.")

    def ask(self, session: Session, ctx: UserContext, request: AskRequest) -> Iterator[AskEvent]:
        """All events of one question; the query row and audit event are written first."""
        events = self.answer(session, ctx, request)
        yield from events

    def answer(self, session: Session, ctx: UserContext, request: AskRequest) -> list[AskEvent]:
        """The SSE events of one question (recorded and audited before they are returned)."""
        return list(self.respond(session, ctx, request).events)

    def _question(self, ctx: UserContext, request: AskRequest) -> str:
        if not ctx.has(ASK):
            raise Forbidden()
        question = mask_aadhaar(nfc(request.question or "").strip())
        if not question or len(question) > MAX_QUESTION_CHARS:
            raise ValidationFailed([_error("question", "question_length")])
        return question

    # --- conversations (ADR-0034; docs/06 §5) --------------------------------------------------

    def _new_conversation(
        self,
        session: Session,
        ctx: UserContext,
        question: str,
        *,
        conversation_id: uuid.UUID | None = None,
        now: dt.datetime,
    ) -> Conversation:
        """A new conversation of the caller, titled from its first question (encrypted). With
        ``conversation_id`` (a legacy ``session_id``), questions the caller asked earlier in that
        session are put into it and the oldest one names it."""
        cid = conversation_id or new_id()
        legacy = repo.session_questions(session, cid, ctx.user_id) if conversation_id else []
        first = (conversations.question_of(session, legacy[0]) if legacy else None) or question
        title = conversations.derive_title(first, telugu=self.runtime.telugu)
        blob, version = conversations.seal(
            session,
            title,
            table=conversations.CONVERSATIONS_TABLE,
            column=conversations.TITLE_COLUMN,
            row_id=cid,
        )
        row = repo.insert_conversation(
            session,
            {
                "id": cid,
                "user_id": ctx.user_id,
                "title_ciphertext": blob,
                "key_version": version,
                "created_at": legacy[0].created_at if legacy else now,
                "updated_at": now,
            },
        )
        if legacy:
            repo.link_session(session, cid, ctx.user_id)
        return row

    def _conversation_for(
        self,
        session: Session,
        ctx: UserContext,
        request: AskRequest,
        question: str,
        now: dt.datetime,
    ) -> tuple[Conversation, bool]:
        """The conversation a new question goes into, and whether it is new.

        ``conversation_id``: one of the caller's conversations (404 otherwise). ``session_id``
        (legacy): the caller's conversation with that id, else a new one with that id, else (it
        names another user's or a deleted conversation) a new one with a new id: another
        person's history is never joined (FR-KB-012). Neither: a new conversation."""
        if request.conversation_id is not None:
            found = repo.get_conversation(
                session, request.conversation_id, ctx.user_id, for_update=True
            )
            if found is None:
                raise NotFound("Conversation not found")
            return found, False
        if request.session_id is not None:
            found = repo.get_conversation(
                session, request.session_id, ctx.user_id, for_update=True, include_deleted=True
            )
            if found is not None and found.deleted_at is None:
                return found, False
            if found is None and repo.conversation_owner(session, request.session_id) is None:
                return (
                    self._new_conversation(
                        session, ctx, question, conversation_id=request.session_id, now=now
                    ),
                    True,
                )
        return self._new_conversation(session, ctx, question, now=now), True

    def _revision(
        self, session: Session, ctx: UserContext, request: AskRequest
    ) -> tuple[Conversation, Query, list[Query], list[Query]]:
        """A regenerate or edit: the target's conversation, the target, the messages it
        supersedes (the target and every later current one) and the thread before it."""
        target_id = request.regenerate_of or request.edit_of
        target = (
            repo.get_query_of_user(session, target_id, ctx.user_id, for_update=True)
            if target_id is not None
            else None
        )
        if target is None or target.conversation_id is None:
            raise NotFound("Message not found")
        conversation = repo.get_conversation(
            session, target.conversation_id, ctx.user_id, for_update=True
        )
        named = request.conversation_id or request.session_id
        if conversation is None or (named is not None and named != conversation.id):
            raise NotFound("Message not found")
        if target.superseded_by is not None:
            raise Conflict("This message was already replaced.", code="message_superseded")
        thread = repo.thread_messages(session, [conversation.id], ctx.user_id)
        window = conversations.config().revisions.max_revisable_messages
        recent = [r.id for r in thread[-window:]]
        if target.id not in recent:
            raise Conflict(
                "Only one of your latest messages can be changed.", code="message_not_revisable"
            )
        index = next(i for i, r in enumerate(thread) if r.id == target.id)
        return conversation, target, thread[index:], thread[:index]

    def _memory_on(self, session: Session, ctx: UserContext) -> bool:
        """The school's switch AND the user's own switch (no row = on; ADR-0034)."""
        school = tenancy.get_tenant(session).settings
        if not getattr(school, "ai_memory_enabled", True):
            return False
        return repo.memory_enabled(session, ctx.user_id) is not False

    def _ai_on(self, tenant_id: uuid.UUID) -> bool:
        """AI answers are on for the school (kill switch, school setting and flag)."""
        rt = self.runtime
        if not rt.settings.kb_enabled:
            return False
        return rt.policy is None or rt.policy.settings_for(tenant_id).ai_enabled

    def _visibility(self, session: Session, ctx: UserContext) -> SourceVisibility:
        tools = self.runtime.tools
        return SourceVisibility(
            session,
            ctx,
            tools_available=lambda: [t.spec.name for t in offered(tools, ctx, session)],
        )

    def _prepare(self, session: Session, ctx: UserContext, request: AskRequest) -> _Plan:
        """Checks and context for one question, in the caller's transaction (no model call)."""
        if not ctx.has(ASK):
            raise Forbidden()
        if request.regenerate_of is not None and request.edit_of is not None:
            raise ValidationFailed([_error("edit_of", "revision_conflict")])
        now = dt.datetime.now(dt.UTC)
        llm = self.runtime.llm_config
        visibility = self._visibility(session, ctx)
        supersedes: list[Query] = []
        before: list[Query] | None = None
        revision: tuple[str, uuid.UUID] | None = None
        if request.regenerate_of is not None or request.edit_of is not None:
            conversation, target, supersedes, before = self._revision(session, ctx, request)
            if request.regenerate_of is not None:
                stored = conversations.question_of(session, target)
                if stored is None:
                    raise NotFound("Message not found")
                question = stored
                revision = ("regenerate", target.id)
            else:
                question = self._question(ctx, request)
                revision = ("edit", target.id)
            new = False
        else:
            question = self._question(ctx, request)
            conversation, new = self._conversation_for(session, ctx, request, question, now)
        memory_on = self._memory_on(session, ctx)
        # ADR-0036: the question is accepted in any script and searched as written; the answer's
        # language (shown in `meta`, stored in kb.queries.language) is English while Telugu is
        # hidden. The detected style of the question stays audit data only.
        detected = detect_language(question)
        language = answer_language(detected, telugu=self.runtime.telugu)
        turns: tuple[HistoryTurn, ...] = ()
        summary: str | None = None
        if not new:
            if (
                supersedes
                and conversation.summary_through is not None
                and any(r.created_at <= conversation.summary_through for r in supersedes)
            ):
                conversations.forget_summary(session, conversation.id)
                conversation.summary_ciphertext = None
            turns, summary = conversations.build_context(
                session, conversation, ctx.user_id, visibility, llm, before=before
            )
        remember = memory.remember_text(question) if revision is None else None
        items = memory.prompt_items(session, ctx.user_id) if memory_on and not remember else ()
        context = AskContext(turns=turns, summary=summary, memory=items)
        access: bytes | None = None
        hit: answer_cache.Hit | None = None
        cacheable = (
            request.regenerate_of is None
            and remember is None
            and not context.has_history
            and not items
            and self._ai_on(ctx.tenant_id)
        )
        if cacheable:
            access = answer_cache.fingerprint(acl_keys(session, ctx))
        if access is not None:
            hmac, _ = crypto.blind_index(session, question_key(question), purpose=QUESTION_PURPOSE)
            hit = answer_cache.lookup(
                session,
                question_hmac=hmac,
                access=access,
                visibility=visibility,
                cfg=self.runtime.conversations.answer_cache,
                now=now,
                language=language,
            )
        return _Plan(
            query_id=new_id(),
            question=question,
            language=language,
            question_language=detected,
            conversation_id=conversation.id,
            title=conversations.title_of(session, conversation),
            new_conversation=new,
            context=context,
            revision=revision,
            supersedes=tuple(r.id for r in supersedes),
            remember=remember,
            memory_on=memory_on,
            access=access if hit is None else None,
            hit=hit,
            now=now,
        )

    def _open(self, session: Session, ctx: UserContext, plan: _Plan) -> None:
        """The ``kb.queries`` row (``streaming``, question encrypted) and ``kb.query.asked``;
        a regenerate or edit supersedes its messages; the conversation's activity moves."""
        q_blob, version, digest = self._encrypt_question(session, plan.query_id, plan.question)
        revision = plan.revision
        repo.insert_query(
            session,
            {
                "id": plan.query_id,
                "session_id": plan.conversation_id,
                "conversation_id": plan.conversation_id,
                "user_id": ctx.user_id,
                "question_ciphertext": q_blob,
                "question_hmac": digest,
                "key_version": version,
                "language": plan.language,
                "mode": "full",
                "status": "streaming",
                "revises": revision[1] if revision else None,
                "revision": revision[0] if revision else None,
                "access_fingerprint": plan.access,
                "cached_from": plan.hit.query_id if plan.hit else None,
                "summarized": plan.context.summary is not None,
            },
        )
        superseded = repo.supersede(session, plan.supersedes, plan.query_id)
        repo.update_conversation(session, plan.conversation_id, {"updated_at": plan.now})
        audit.record(
            session,
            action="kb.query.asked",
            resource_type="kb_query",
            resource_id=plan.query_id,
            summary={
                "mode": "full",
                "status": "streaming",
                "language": plan.language,
                "question_language": plan.question_language,
                "earlier_questions": len(plan.context.turns),
                "conversation_id": str(plan.conversation_id),
                "new_conversation": plan.new_conversation,
                "revision": revision[0] if revision else None,
                "superseded": superseded,
                "summary_used": plan.context.summary is not None,
                "memory_items": len(plan.context.memory),
                "cached": plan.hit is not None,
                "memory_instruction": plan.remember is not None,
            },
            request_id=ctx.request_id,
        )

    def respond(self, session: Session, ctx: UserContext, request: AskRequest) -> AskResponse:
        """One question answered whole in the CALLER's transaction: the validated
        :class:`Answer`, its events (with ``followups`` and ``memory``), stored and audited
        (``kb.query.asked`` then ``kb.query.completed``; the eval harness and non-streaming
        callers)."""
        plan = self._prepare(session, ctx, request)
        self._open(session, ctx, plan)
        run = _Run(self, ctx, plan, started=time.monotonic())
        events: list[AskEvent] = [run.meta()]
        answer_steps = run.answer_events(session, stream=False)
        while True:
            try:
                events.append(next(answer_steps))
            except StopIteration as done:
                result, latency, replaced = done.value
                break
        events += list(run.closing_events(result, replaced))
        events += list(run.after(session, result))
        events.append(run.done(result, latency))
        return AskResponse(
            query_id=plan.query_id,
            answer=result,
            events=tuple(events),
            followups=run.followups,
            conversation_id=plan.conversation_id,
        )

    def start_stream(self, session: Session, ctx: UserContext, request: AskRequest) -> AskStream:
        """Begin a streamed question (``POST /knowledge/ask``; docs/06 §5.1).

        In the CALLER's transaction: checks, the conversation (a new one when none is named),
        its context (recent turns, summary, memory; no model call), the answer-cache lookup,
        and the ``kb.queries`` row (status ``streaming``, question encrypted) with its audit
        event ``kb.query.asked``. The caller commits BEFORE iterating the returned stream, so
        nothing is shown that was not recorded (invariant 7). The stream then runs in its own
        transactions and completes the same row (``kb.query.completed``), or records it
        ``cancelled`` when closed early (``kb.query.cancelled``)."""
        plan = self._prepare(session, ctx, request)
        self._open(session, ctx, plan)
        return AskStream(self, ctx, plan, started=time.monotonic())

    def _encrypt_question(
        self, session: Session, query_id: uuid.UUID, question: str
    ) -> tuple[bytes, int, bytes]:
        q_blob, version = crypto.encrypt_value(
            session,
            question,
            table=QUERIES_TABLE,
            column=QUESTION_COLUMN,
            row_id=query_id,
        )
        digest, _ = crypto.blind_index(
            session, question_key(question), purpose=QUESTION_PURPOSE, key_version=version
        )
        return q_blob, version, digest

    def _encrypt_answer(self, session: Session, query_id: uuid.UUID, text: str) -> bytes | None:
        if not text:
            return None
        blob, _ = crypto.encrypt_value(
            session, text, table=QUERIES_TABLE, column=ANSWER_COLUMN, row_id=query_id
        )
        return blob

    def _encrypt_citations(
        self, session: Session, query_id: uuid.UUID, result: Answer
    ) -> bytes | None:
        if not result.cited:
            return None
        blob, _ = conversations.seal(
            session,
            conversations.encode_citations(result.cited),
            table=QUERIES_TABLE,
            column=conversations.CITATIONS_COLUMN,
            row_id=query_id,
        )
        return blob

    @staticmethod
    def _outcome_values(result: Answer, latency_ms: int) -> dict[str, object]:
        return {
            "language": result.language,
            "mode": result.mode,
            "route": result.route,
            "status": result.status,
            "error": result.error_code,
            "tools": [
                {"tool": t.name, "results": t.results, "error": t.error} for t in result.tools
            ],
            "retrieved": [{"source": s} for s in result.provided],
            "citations": [{"source": s} for s in sorted({c.source for c in result.cited})],
            "model_ids": list(result.model_ids),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "latency_ms": latency_ms,
        }

    @staticmethod
    def _summary(result: Answer) -> dict[str, object]:
        return {
            "mode": result.mode,
            "status": result.status,
            "route": result.route,
            "language": result.language,
            "error_code": result.error_code,
            "tool_calls": len(result.tools),
            "sources_provided": len(result.provided),
            "citations": len(result.cited),
            "citations_dropped": result.citations_dropped,
            "uncited_factual": result.uncited_factual,
            "sentences_dropped": result.sentences_dropped,
        }

    @staticmethod
    def _log_answered(
        ctx: UserContext, query_id: uuid.UUID, result: Answer, latency_ms: int
    ) -> None:
        log.info(
            "kb.query.answered",
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            resource_type="kb_query",
            resource_id=query_id,
            outcome=result.status,
            action=result.mode,
            count=len(result.cited),
            duration_ms=latency_ms,
        )

    def complete_stream(
        self,
        session: Session,
        ctx: UserContext,
        query_id: uuid.UUID,
        result: Answer,
        *,
        latency_ms: int,
        replaced: bool,
        streamed: bool = True,
        cached_from: uuid.UUID | None = None,
    ) -> None:
        """A question finished: the row gets the validated answer and its numbered citations
        (both encrypted), codes and counts; audit ``kb.query.completed`` in the same
        transaction."""
        repo.update_query(
            session,
            query_id,
            {
                "answer_ciphertext": self._encrypt_answer(session, query_id, result.text),
                "citations_ciphertext": self._encrypt_citations(session, query_id, result),
                **self._outcome_values(result, latency_ms),
            },
        )
        audit.record(
            session,
            action="kb.query.completed",
            resource_type="kb_query",
            resource_id=query_id,
            summary={
                **self._summary(result),
                "streamed": streamed,
                "replaced": replaced,
                "cached": cached_from is not None,
            },
            request_id=ctx.request_id,
        )
        self._log_answered(ctx, query_id, result, latency_ms)

    def end_stream_early(
        self,
        ctx: UserContext,
        query_id: uuid.UUID,
        progress: Progress,
        *,
        latency_ms: int,
        status: str,
        error_code: str | None = None,
    ) -> None:
        """A streamed question ended before its answer was complete: the client went away
        (``cancelled``) or the stream failed (``error``). In a new transaction: the row keeps
        what was shown (encrypted), the tools, sources and tokens used; audit
        ``kb.query.cancelled`` or ``kb.query.failed`` (codes and counts only)."""
        turns = progress.turns
        with tenant_session(ctx.tenant_id, ctx.user_id) as session:
            repo.update_query(
                session,
                query_id,
                {
                    "answer_ciphertext": self._encrypt_answer(session, query_id, progress.text),
                    "status": status,
                    "error": error_code,
                    "tools": [
                        {"tool": t.name, "results": t.results, "error": t.error}
                        for t in progress.runs
                    ],
                    "retrieved": [{"source": s} for s in progress.provided.order],
                    "model_ids": sorted({t.model for t in turns}),
                    "input_tokens": sum(t.usage.input_tokens for t in turns),
                    "output_tokens": sum(t.usage.output_tokens for t in turns),
                    "latency_ms": latency_ms,
                },
            )
            audit.record(
                session,
                action="kb.query.cancelled" if status == "cancelled" else "kb.query.failed",
                resource_type="kb_query",
                resource_id=query_id,
                summary={
                    "status": status,
                    "error_code": error_code,
                    "tool_calls": len(progress.runs),
                    "sources_provided": len(progress.provided.order),
                    "shown_chars": len(progress.text),
                },
                request_id=ctx.request_id,
            )
        log.info(
            "kb.query.ended_early",
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            resource_type="kb_query",
            resource_id=query_id,
            outcome=status,
            error_code=error_code,
            duration_ms=latency_ms,
        )

    # --- conversation history (GET/PATCH/DELETE /knowledge/conversations) ----------------------

    @staticmethod
    def _conversation_out(
        session: Session, row: Conversation, message_count: int
    ) -> ConversationOut:
        return ConversationOut(
            id=row.id,
            title=conversations.title_of(session, row),
            pinned=row.pinned,
            created_at=row.created_at,
            updated_at=row.updated_at,
            message_count=message_count,
            version=row.version,
        )

    def list_conversations(
        self,
        session: Session,
        ctx: UserContext,
        *,
        limit: int,
        after: tuple[bool, dt.datetime, uuid.UUID] | None,
    ) -> tuple[list[ConversationOut], tuple[bool, dt.datetime, uuid.UUID] | None]:
        """The caller's own conversations, pinned first, newest activity first."""
        if not ctx.has(ASK):
            raise Forbidden()
        rows = repo.list_conversations(session, ctx.user_id, limit=limit + 1, after=after)
        more = len(rows) > limit
        rows = rows[:limit]
        out = [self._conversation_out(session, r.conversation, r.message_count) for r in rows]
        last = rows[-1].conversation if more and rows else None
        return out, ((last.pinned, last.updated_at, last.id) if last is not None else None)

    def _message_out(
        self, session: Session, row: Query, visibility: SourceVisibility
    ) -> MessageOut:
        citations: list[MessageCitationOut] = []
        withheld_any = False
        for c in conversations.stored_citations(session, row):
            shown = visibility.visible(c.source)
            withheld_any |= not shown
            citations.append(
                MessageCitationOut(
                    index=c.index,
                    source=c.source,
                    title=c.title if shown else None,
                    snippet=c.snippet if shown else None,
                    withheld=not shown or c.title is None,
                )
            )
        if row.status not in repo.EARLIER_STATUSES:
            # A cancelled or failed answer keeps the UNCHECKED preview and has no citations: it
            # may quote any passage or record the model was given, so every one of them must
            # still be visible (invariant 8).
            withheld_any |= not all(
                visibility.visible(str(item.get("source", "")))
                for item in (row.retrieved or [])
                if isinstance(item, dict)
            )
        answer = None if withheld_any else conversations.answer_of(session, row)
        return MessageOut(
            query_id=row.id,
            question=conversations.question_of(session, row) or "",
            answer=answer or None,
            answer_withheld=withheld_any,
            status=row.status,
            mode=row.mode,
            language=row.language,
            citations=citations,
            feedback=row.feedback,
            followups=[] if withheld_any else list(conversations.stored_followups(session, row)),
            created_at=row.created_at,
            superseded=row.superseded_by is not None,
            cached=row.cached_from is not None,
            summarized=row.summarized,
        )

    def get_conversation(
        self, session: Session, ctx: UserContext, conversation_id: uuid.UUID
    ) -> ConversationDetailOut:
        """One of the caller's conversations with every message (superseded ones flagged).
        Each citation is re-checked NOW: a source the caller can no longer see is returned
        without title and snippet, and that message's answer and follow-ups are withheld too
        (invariant 8). 404 for anyone else's, another school's or a deleted conversation."""
        if not ctx.has(ASK):
            raise Forbidden()
        found = repo.conversation_with_count(session, conversation_id, ctx.user_id)
        if found is None:
            raise NotFound("Conversation not found")
        visibility = self._visibility(session, ctx)
        messages = [
            self._message_out(session, row, visibility)
            for row in repo.conversation_messages(session, conversation_id, ctx.user_id)
        ]
        base = self._conversation_out(session, found.conversation, found.message_count)
        return ConversationDetailOut(**base.model_dump(), messages=messages)

    def update_conversation(
        self,
        session: Session,
        ctx: UserContext,
        conversation_id: uuid.UUID,
        data: ConversationPatchIn,
        *,
        expected_version: int,
    ) -> ConversationOut:
        """Rename or pin (``If-Match``). A title is NFC, 1-120 characters, and never holds an
        Aadhaar-like number (422 ``title_personal_number``); it is stored encrypted. Audited
        ``kb.conversation.updated`` (changed field names, pinned, title length only)."""
        if not ctx.has(ASK):
            raise Forbidden()
        row = repo.get_conversation(session, conversation_id, ctx.user_id, for_update=True)
        if row is None:
            raise NotFound("Conversation not found")
        if row.version != expected_version:
            raise PreconditionFailed()
        values: dict[str, object] = {}
        changed: list[str] = []
        title_chars: int | None = None
        if data.title is not None:
            title = conversations.tidy(data.title)
            limit = conversations.config().titles.max_chars
            if not title or len(title) > limit:
                raise ValidationFailed([_error("title", "title_length")])
            problem = conversations.title_problem(title)
            if problem is not None:
                raise ValidationFailed([_error("title", problem)])
            blob, version = conversations.seal(
                session,
                title,
                table=conversations.CONVERSATIONS_TABLE,
                column=conversations.TITLE_COLUMN,
                row_id=row.id,
            )
            values |= {"title_ciphertext": blob, "key_version": version}
            changed.append("title")
            title_chars = len(title)
        if data.pinned is not None and data.pinned != row.pinned:
            values["pinned"] = data.pinned
            changed.append("pinned")
        if values:
            values |= {"version": row.version + 1, "updated_at": dt.datetime.now(dt.UTC)}
            updated = repo.update_conversation(session, row.id, values)
            if updated is None:  # pragma: no cover - locked above
                raise PreconditionFailed()
            row = updated
            audit.record(
                session,
                action="kb.conversation.updated",
                resource_type="kb_conversation",
                resource_id=row.id,
                summary={"changed": changed, "pinned": row.pinned, "title_chars": title_chars},
                request_id=ctx.request_id,
            )
        found = repo.conversation_with_count(session, row.id, ctx.user_id)
        if found is None:  # pragma: no cover - locked above
            raise NotFound("Conversation not found")
        return self._conversation_out(session, found.conversation, found.message_count)

    def delete_conversation(
        self, session: Session, ctx: UserContext, conversation_id: uuid.UUID
    ) -> None:
        """Remove a conversation from the caller's history (docs/08 §7 decision of
        2026-09-30): it is hidden at once everywhere (list, detail, context, chat search,
        regenerate, feedback), its title and summary are erased now, and its questions stay
        in the encrypted query log until the 180-day purge (FR-KB-009), which then deletes the
        conversation row. Audited ``kb.conversation.deleted`` (message count only)."""
        if not ctx.has(ASK):
            raise Forbidden()
        row = repo.get_conversation(session, conversation_id, ctx.user_id, for_update=True)
        if row is None:
            raise NotFound("Conversation not found")
        count = len(repo.conversation_messages(session, row.id, ctx.user_id))
        repo.update_conversation(
            session,
            row.id,
            {
                "deleted_at": dt.datetime.now(dt.UTC),
                "title_ciphertext": None,
                "summary_ciphertext": None,
                "summary_oldest_at": None,
                "summary_through": None,
                "summary_sources": [],
                "pinned": False,
                "version": row.version + 1,
            },
        )
        audit.record(
            session,
            action="kb.conversation.deleted",
            resource_type="kb_conversation",
            resource_id=row.id,
            summary={"messages": count},
            request_id=ctx.request_id,
        )

    # --- memory (ADR-0034) ------------------------------------------------------------------------

    def _memory_out(self, item: memory.Item) -> MemoryOut:
        r = item.row
        return MemoryOut(
            id=r.id,
            text=item.text,
            source=r.source,
            status=r.status,
            created_at=r.created_at,
            updated_at=r.updated_at,
            expires_at=r.expires_at,
            version=r.version,
        )

    def list_memories(self, session: Session, ctx: UserContext) -> list[MemoryOut]:
        """The caller's memory items in this school (confirmed and pending), newest first."""
        if not ctx.has(ASK):
            raise Forbidden()
        now = dt.datetime.now(dt.UTC)
        return [self._memory_out(i) for i in memory.items(session, ctx.user_id, now)]

    def _screened(self, session: Session, ctx: UserContext, text: str) -> str:
        """The item text after every screen, or the refusal (422 / 503)."""
        note = conversations.tidy(nfc(text))
        verdict = memory.screen(
            self.runtime.gateway, Metering(tenant_id=ctx.tenant_id, feature="ask"), note, set()
        )
        if verdict == "unavailable":
            raise ServiceUnavailable(
                "The memory check is not available now. Try again later.",
                code="memory_check_unavailable",
            )
        if verdict != "ok":
            raise ValidationFailed([_error("text", f"memory_{verdict}")])
        return note

    def _memory_guard(self, session: Session, ctx: UserContext) -> None:
        if not ctx.has(ASK):
            raise Forbidden()
        if not self._memory_on(session, ctx):
            raise Conflict("Memory is off.", code="memory_off")

    def create_memory(self, session: Session, ctx: UserContext, data: MemoryIn) -> MemoryOut:
        """Save an item the user typed (explicit, active at once) after the screen."""
        self._memory_guard(session, ctx)
        now = dt.datetime.now(dt.UTC)
        note = self._screened(session, ctx, data.text)
        existing = memory.items(session, ctx.user_id, now)
        same = memory.duplicate(existing, note)
        if same is not None and same.row.status == "active":
            return self._memory_out(same)
        if len(existing) >= memory.config().max_items:
            raise Conflict("Your memory is full.", code="memory_full")
        row = memory.insert(
            session, user_id=ctx.user_id, text=note, source="explicit", status="active", now=now
        )
        audit.record(
            session,
            action="kb.memory.created",
            resource_type="kb_memory",
            resource_id=row.id,
            summary={"source": "explicit", "via": "settings", "chars": len(note)},
            request_id=ctx.request_id,
        )
        return self._memory_out(memory.Item(row, note))

    def _own_memory(self, session: Session, ctx: UserContext, memory_id: uuid.UUID) -> UserMemory:
        if not ctx.has(ASK):
            raise Forbidden()
        row = repo.get_memory(
            session, memory_id, ctx.user_id, dt.datetime.now(dt.UTC), for_update=True
        )
        if row is None:
            raise NotFound("Memory item not found")
        return row

    def update_memory(
        self,
        session: Session,
        ctx: UserContext,
        memory_id: uuid.UUID,
        data: MemoryPatchIn,
        *,
        expected_version: int,
    ) -> MemoryOut:
        """Edit an item's text (``If-Match``; screened again). Audited ``kb.memory.updated``."""
        row = self._own_memory(session, ctx, memory_id)
        self._memory_guard(session, ctx)
        if row.version != expected_version:
            raise PreconditionFailed()
        note = self._screened(session, ctx, data.text)
        blob, version = conversations.seal(
            session, note, table=memory.TABLE, column=memory.TEXT_COLUMN, row_id=row.id
        )
        updated = repo.update_memory(
            session,
            row.id,
            expected_version=expected_version,
            values={"text_ciphertext": blob, "key_version": version},
        )
        if updated is None:  # pragma: no cover - locked above
            raise PreconditionFailed()
        audit.record(
            session,
            action="kb.memory.updated",
            resource_type="kb_memory",
            resource_id=row.id,
            summary={"chars": len(note), "status": updated.status},
            request_id=ctx.request_id,
        )
        return self._memory_out(memory.Item(updated, note))

    def confirm_memory(self, session: Session, ctx: UserContext, memory_id: uuid.UUID) -> MemoryOut:
        """Keep a suggested item (``pending`` -> ``active``; an active one is returned as it
        is). An expired suggestion is gone (404). Audited ``kb.memory.confirmed``."""
        row = self._own_memory(session, ctx, memory_id)
        self._memory_guard(session, ctx)
        if row.status == "pending":
            updated = repo.update_memory(
                session,
                row.id,
                expected_version=row.version,
                values={"status": "active", "expires_at": None},
            )
            if updated is None:  # pragma: no cover - locked above
                raise PreconditionFailed()
            row = updated
            audit.record(
                session,
                action="kb.memory.confirmed",
                resource_type="kb_memory",
                resource_id=row.id,
                summary={"source": row.source},
                request_id=ctx.request_id,
            )
        return self._memory_out(memory.Item(row, memory.text_of(session, row)))

    def delete_memory(self, session: Session, ctx: UserContext, memory_id: uuid.UUID) -> None:
        """Delete one item (its row is deleted). Audited ``kb.memory.deleted``."""
        row = self._own_memory(session, ctx, memory_id)
        repo.delete_memory(session, row.id)
        audit.record(
            session,
            action="kb.memory.deleted",
            resource_type="kb_memory",
            resource_id=row.id,
            summary={"status": row.status, "source": row.source},
            request_id=ctx.request_id,
        )

    def forget_memories(self, session: Session, ctx: UserContext) -> int:
        """Delete every item of the caller in this school. Audited ``kb.memory.forgotten``."""
        if not ctx.has(ASK):
            raise Forbidden()
        count = repo.delete_user_memories(session, ctx.user_id)
        audit.record(
            session,
            action="kb.memory.forgotten",
            resource_type="kb_memory",
            resource_id=ctx.user_id,
            summary={"count": count},
            request_id=ctx.request_id,
        )
        return count

    def memory_settings(self, session: Session, ctx: UserContext) -> MemorySettingsOut:
        if not ctx.has(ASK):
            raise Forbidden()
        school = bool(getattr(tenancy.get_tenant(session).settings, "ai_memory_enabled", True))
        mine = repo.memory_enabled(session, ctx.user_id) is not False
        return MemorySettingsOut(enabled=mine, school_enabled=school)

    def set_memory_settings(
        self, session: Session, ctx: UserContext, data: MemorySettingsIn
    ) -> MemorySettingsOut:
        """The caller's switch. Off: nothing is stored, suggested or used (items stay listed so
        they can be deleted). Audited ``kb.memory.settings_changed``."""
        if not ctx.has(ASK):
            raise Forbidden()
        repo.set_memory_enabled(session, ctx.user_id, data.enabled)
        audit.record(
            session,
            action="kb.memory.settings_changed",
            resource_type="kb_memory",
            resource_id=ctx.user_id,
            summary={"enabled": data.enabled},
            request_id=ctx.request_id,
        )
        return self.memory_settings(session, ctx)

    # --- search-only ------------------------------------------------------------------------

    def search_documents(
        self,
        session: Session,
        ctx: UserContext,
        query: str,
        *,
        filters: SearchFilters | None = None,
        k: int = 12,
    ) -> list[RankedChunk]:
        if not ctx.has(SEARCH):
            raise Forbidden()
        return self.runtime.search.search(session, ctx, mask_aadhaar(query), filters=filters, k=k)

    def search_results(
        self,
        session: Session,
        ctx: UserContext,
        query: str,
        *,
        filters: SearchFilters | None = None,
        k: int = 12,
    ) -> list[SearchResultOut]:
        return [
            _result_out(c) for c in self.search_documents(session, ctx, query, filters=filters, k=k)
        ]

    # --- feedback ---------------------------------------------------------------------------

    def feedback(
        self, session: Session, ctx: UserContext, query_id: uuid.UUID, data: FeedbackIn
    ) -> FeedbackOut:
        """Helpful / not helpful on one of the caller's OWN questions (404 for anyone else's)."""
        if not ctx.has(ASK):
            raise Forbidden()
        row = repo.get_query_of_user(session, query_id, ctx.user_id, for_update=True)
        if row is None or (
            row.conversation_id is not None
            and repo.get_conversation(session, row.conversation_id, ctx.user_id) is None
        ):
            raise NotFound("Question not found")
        at = dt.datetime.now(dt.UTC)
        repo.set_feedback(session, query_id, feedback=data.feedback, reason=data.reason, at=at)
        audit.record(
            session,
            action="kb.query.feedback",
            resource_type="kb_query",
            resource_id=query_id,
            summary={"feedback": data.feedback, "reason": data.reason},
            request_id=ctx.request_id,
        )
        return FeedbackOut(
            query_id=query_id, feedback=data.feedback, reason=data.reason, recorded_at=at
        )

    # --- verified answers (FR-KB-030) -------------------------------------------------------

    @staticmethod
    def _document_visible(session: Session, ctx: UserContext, document_id: uuid.UUID) -> bool:
        """The caller's ACL/scope reaches it, and a restricted (C3) one only with
        ``student.read_sensitive`` (the rule retrieval applies, docs/06 §6)."""
        try:
            doc = documents.get_document(session, ctx, document_id)
        except NotFound:
            return False
        return doc.sensitivity != "C3" or ctx.has(READ_SENSITIVE)

    def _cites_visible(self, session: Session, ctx: UserContext, row: VerifiedAnswer) -> bool:
        for citation in row.citations:
            try:
                ref = sources.parse(str(citation.get("source", "")))
            except ValueError:
                return False
            if ref.kind != "doc" or not self._document_visible(session, ctx, ref.object_id):
                return False
        return True

    @staticmethod
    def _verified_page(session: Session, rows: list[VerifiedAnswer]) -> list[VerifiedAnswerOut]:
        names = identity.member_display_names(session, {r.verified_by for r in rows})
        return [_verified_out(r, names.get(r.verified_by)) for r in rows]

    def list_verified_answers(
        self,
        session: Session,
        ctx: UserContext,
        *,
        status: str | None,
        limit: int,
        before_id: uuid.UUID | None,
    ) -> tuple[list[VerifiedAnswerOut], uuid.UUID | None]:
        """Verified answers whose every cited document the caller can read (newest first)."""
        if not ctx.has(ASK):
            raise Forbidden()
        rows = repo.list_verified_answers(
            session, status=status, limit=limit + 1, before_id=before_id
        )
        more = len(rows) > limit
        rows = rows[:limit]
        visible = [r for r in rows if self._cites_visible(session, ctx, r)]
        return self._verified_page(session, visible), (rows[-1].id if more and rows else None)

    def _check_citation(
        self, session: Session, ctx: UserContext, index: int, source: str, cited_text: str
    ) -> dict[str, str] | None:
        field = f"citations[{index}].source"
        try:
            ref = sources.parse(source)
        except ValueError:
            return _error(field, "citation_source_invalid")
        if ref.kind != "doc" or ref.version_no is None or ref.page is None:
            return _error(field, "citation_source_unsupported")
        if not self._document_visible(session, ctx, ref.object_id):
            return _error(field, "citation_not_found")  # also a C3 one without read_sensitive
        doc = documents.get_document(session, ctx, ref.object_id)
        current = doc.current_version
        if doc.status != "active" or current is None or current.version_no != ref.version_no:
            return _error(field, "citation_not_current")
        texts = repo.latest_page_texts(session, current.id, ref.page)
        wanted = normalise(cited_text)
        if not wanted or not any(wanted in normalise(t) for t in texts):
            return _error(f"citations[{index}].cited_text", "citation_text_not_found")
        return None

    def _checked_citations(
        self, session: Session, ctx: UserContext, citations: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        errors = [
            e
            for i, c in enumerate(citations)
            if (e := self._check_citation(session, ctx, i, c["source"], c["cited_text"]))
            is not None
        ]
        if errors:
            raise ValidationFailed(errors)
        return [{"source": c["source"], "cited_text": nfc(c["cited_text"])} for c in citations]

    def create_verified_answer(
        self, session: Session, ctx: UserContext, data: VerifiedAnswerIn
    ) -> VerifiedAnswerOut:
        """Store an approved answer (``kb.verified_answer.manage``). Every citation must quote
        the current version of a document the caller can read (docs/06 §8-9)."""
        if not ctx.has(MANAGE_VERIFIED):
            raise Forbidden()
        citations = self._checked_citations(session, ctx, [c.model_dump() for c in data.citations])
        now = dt.datetime.now(dt.UTC)
        row = repo.insert_verified_answer(
            session,
            {
                "id": new_id(),
                "question_canonical": mask_aadhaar(nfc(data.question)),
                "language": data.language,
                "answer_text": mask_aadhaar(nfc(data.answer_text)),
                "citations": citations,
                "verified_by": ctx.membership_id,
                "verified_at": now,
                "review_due": data.review_due,
            },
        )
        audit.record(
            session,
            action="kb.verified_answer.created",
            resource_type="kb_verified_answer",
            resource_id=row.id,
            summary={"language": data.language, "citations": len(data.citations)},
            request_id=ctx.request_id,
        )
        return self._verified_page(session, [row])[0]

    def _managed_answer(
        self, session: Session, ctx: UserContext, answer_id: uuid.UUID, expected_version: int
    ) -> VerifiedAnswer:
        """The answer, locked, for a manager who can read every document it cites (404
        otherwise, like another school's); 412 on a stale ``If-Match``; 409 once retired."""
        if not ctx.has(MANAGE_VERIFIED):
            raise Forbidden()
        row = repo.get_verified_answer(session, answer_id, for_update=True)
        if row is None or not self._cites_visible(session, ctx, row):
            raise NotFound("Verified answer not found")
        if row.version != expected_version:
            raise PreconditionFailed()
        if row.status == "retired":
            raise Conflict("This verified answer is retired.", code="verified_answer_retired")
        return row

    def review_verified_answer(
        self,
        session: Session,
        ctx: UserContext,
        answer_id: uuid.UUID,
        data: VerifiedAnswerReviewIn,
        *,
        expected_version: int,
    ) -> VerifiedAnswerOut:
        """Confirm (or correct) a verified answer, typically one flagged ``needs_review`` after a
        cited document changed (FR-KB-030, docs/06 §4.8). Its citations (the new ones, else the
        stored ones) must again quote the CURRENT version of documents the caller can read;
        it becomes ``active``, verified by the caller now. Audited
        ``kb.verified_answer.reviewed`` with the changed field names only."""
        row = self._managed_answer(session, ctx, answer_id, expected_version)
        citations = self._checked_citations(
            session,
            ctx,
            [c.model_dump() for c in data.citations]
            if data.citations is not None
            else [
                {"source": str(c.get("source", "")), "cited_text": str(c.get("cited_text", ""))}
                for c in row.citations
            ],
        )
        values: dict[str, object] = {
            "status": "active",
            "citations": citations,
            "verified_by": ctx.membership_id,
            "verified_at": dt.datetime.now(dt.UTC),
        }
        changed = []
        if data.answer_text is not None:
            values["answer_text"] = mask_aadhaar(nfc(data.answer_text))
            changed.append("answer_text")
        if data.citations is not None:
            changed.append("citations")
        if "review_due" in data.model_fields_set:
            values["review_due"] = data.review_due
            changed.append("review_due")
        updated = repo.update_verified_answer(
            session, answer_id, expected_version=expected_version, values=values
        )
        if updated is None:  # pragma: no cover - the row is locked above
            raise PreconditionFailed()
        audit.record(
            session,
            action="kb.verified_answer.reviewed",
            resource_type="kb_verified_answer",
            resource_id=answer_id,
            summary={
                "previous_status": row.status,
                "changed": changed,
                "citations": len(citations),
            },
            request_id=ctx.request_id,
        )
        return self._verified_page(session, [updated])[0]

    def retire_verified_answer(
        self, session: Session, ctx: UserContext, answer_id: uuid.UUID, *, expected_version: int
    ) -> VerifiedAnswerOut:
        """Withdraw a verified answer (``retired``): it is never searched or shown as current
        again; kept for the record. Audited ``kb.verified_answer.retired``."""
        row = self._managed_answer(session, ctx, answer_id, expected_version)
        updated = repo.update_verified_answer(
            session, answer_id, expected_version=expected_version, values={"status": "retired"}
        )
        if updated is None:  # pragma: no cover - the row is locked above
            raise PreconditionFailed()
        audit.record(
            session,
            action="kb.verified_answer.retired",
            resource_type="kb_verified_answer",
            resource_id=answer_id,
            summary={"previous_status": row.status},
            request_id=ctx.request_id,
        )
        return self._verified_page(session, [updated])[0]


_MARKER = re.compile(r"\s*\[\d+\]")
_SPACE = re.compile(r"\s+")


def _squash(text: str) -> str:
    return _SPACE.sub("", _MARKER.sub("", text))


def was_replaced(shown: str, result: Answer) -> bool:
    """Whether the validated answer differs from the streamed preview beyond whitespace and the
    ``[n]`` citation markers (the ``final`` event's ``replaced`` flag, docs/06 §5.1)."""
    return bool(shown) and _squash(shown) != _squash(result.text)


@dataclass(frozen=True, slots=True)
class _Plan:
    """Everything decided in the request's transaction before the first event (no model call)."""

    query_id: uuid.UUID
    question: str
    language: Locale
    """The answer's language (ADR-0036: ``en`` while Telugu is hidden)."""
    question_language: Locale
    """The question's detected style (en, te, mixed): audit data, never the output language."""
    conversation_id: uuid.UUID
    title: str
    new_conversation: bool
    context: AskContext
    revision: tuple[str, uuid.UUID] | None
    supersedes: tuple[uuid.UUID, ...]
    remember: str | None
    memory_on: bool
    access: bytes | None
    hit: answer_cache.Hit | None
    now: dt.datetime


class _Run:
    """The work of one question after its row is recorded, shared by the stream (its own
    transactions) and :meth:`SchoolKnowledgeService.respond` (the caller's transaction)."""

    def __init__(
        self, service: SchoolKnowledgeService, ctx: UserContext, plan: _Plan, *, started: float
    ) -> None:
        self.service = service
        self.ctx = ctx
        self.plan = plan
        self.started = started
        self.progress = Progress()
        self.followups: tuple[str, ...] = ()
        self.memory_event: MemoryEvent | None = None

    def meta(self) -> MetaEvent:
        plan = self.plan
        return MetaEvent(
            query_id=plan.query_id,
            language=plan.language,
            mode="full",
            conversation_id=plan.conversation_id,
            title=plan.title,
            cached=plan.hit is not None,
            cached_from=plan.hit.query_id if plan.hit else None,
            summarized=plan.context.summary is not None,
        )

    def _metering(self) -> Metering:
        return Metering(tenant_id=self.ctx.tenant_id, feature="ask", query_id=self.plan.query_id)

    # --- the answer ---------------------------------------------------------------------------

    def answer_events(
        self, session: Session, *, stream: bool
    ) -> Generator[AskEvent, None, tuple[Answer, int, bool]]:
        """Status and preview events while the answer is made; then the row is completed in
        ``session`` (and the rolling summary requested). Returns (answer, latency, replaced)."""
        plan = self.plan
        cached_from: uuid.UUID | None = None
        if plan.remember is not None:
            yield StatusEvent(step="understanding")
            result = self._remember(session)
        elif plan.hit is not None:
            yield StatusEvent(step="writing")
            result = self._cached(plan.hit)
            cached_from = plan.hit.query_id
        else:
            yield StatusEvent(step="understanding")
            result = yield from self._answer(session, stream=stream)
        latency = elapsed_ms(self.started)
        replaced = was_replaced(self.progress.text, result)
        svc = self.service
        svc.complete_stream(
            session,
            self.ctx,
            plan.query_id,
            result,
            latency_ms=latency,
            replaced=replaced,
            streamed=stream,
            cached_from=cached_from,
        )
        self._request_summary(session)
        return result, latency, replaced

    def _answer(self, session: Session, *, stream: bool) -> Generator[AskEvent, None, Answer]:
        svc, ctx, plan = self.service, self.ctx, self.plan
        rt = svc.runtime
        standalone = conversations.rewrite(
            rt.gateway, self._metering(), plan.question, plan.context
        )
        context = dataclasses.replace(plan.context, standalone=standalone)
        school = tenancy.get_tenant(session)
        if not stream:
            return rt.engine.run(
                session,
                ctx,
                plan.question,
                query_id=plan.query_id,
                school_name=school.name,
                context=context,
            )
        engine = rt.engine.stream(
            session,
            ctx,
            plan.question,
            query_id=plan.query_id,
            school_name=school.name,
            progress=self.progress,
            context=context,
            steps=True,
        )
        writing = False
        with closing(engine):
            while True:
                try:
                    item = next(engine)
                except StopIteration as done:
                    result: Answer = done.value
                    return result
                if isinstance(item, StepUpdate):
                    yield StatusEvent(step=item.step, tool=item.tool, count=item.count)
                    continue
                if not writing:
                    writing = True
                    yield StatusEvent(step="writing")
                yield DeltaEvent(text=item.text)

    def _reply(self, text: str, *, status: str, note: str | None = None) -> Answer:
        shown = f"{text} {note}" if note else text
        return Answer(
            language=self.plan.language,
            mode="full",
            status=status,  # type: ignore[arg-type]
            route="memory",  # type: ignore[arg-type]
            segments=(AnswerSegment(shown),),
            cited=(),
            provided=(),
        )

    def _remember(self, session: Session) -> Answer:
        """A "remember that ..." instruction (ADR-0034): screened and saved at once (explicit,
        active), answered with a fixed reply; never a model answer."""
        plan, ctx = self.plan, self.ctx
        cfg = memory.config()
        replies = cfg.replies
        lang = "en" if plan.language == "en" else "te"
        note = conversations.tidy(plan.remember or "")
        if not plan.memory_on:
            return self._reply(getattr(replies.memory_off, lang), status="refused")
        seen = memory.seen_record_terms(session, plan.conversation_id, ctx.user_id)
        verdict = memory.screen(self.service.runtime.gateway, self._metering(), note, seen)
        if verdict == "unavailable":
            return self._reply(getattr(replies.unavailable, lang), status="refused")
        if verdict != "ok":
            log.info(
                "kb.memory.refused",
                tenant_id=ctx.tenant_id,
                resource_type="kb_query",
                resource_id=plan.query_id,
                error_code=verdict,
            )
            return self._reply(getattr(replies.refused, lang), status="refused")
        now = dt.datetime.now(dt.UTC)
        existing = memory.items(session, ctx.user_id, now)
        same = memory.duplicate(existing, note)
        if same is not None and same.row.status == "active":
            item_id = same.row.id
        elif len(existing) >= cfg.max_items:
            return self._reply(getattr(replies.full, lang), status="refused")
        else:
            row = memory.insert(
                session,
                user_id=ctx.user_id,
                text=note,
                source="explicit",
                status="active",
                now=now,
                conversation_id=plan.conversation_id,
                query_id=plan.query_id,
            )
            item_id = row.id
            audit.record(
                session,
                action="kb.memory.created",
                resource_type="kb_memory",
                resource_id=row.id,
                summary={"source": "explicit", "via": "ask", "chars": len(note)},
                request_id=ctx.request_id,
            )
        self.memory_event = MemoryEvent(action="saved", item_id=item_id, text=note)
        return self._reply(getattr(replies.saved, lang), status="answered", note=note)

    def _cached(self, hit: answer_cache.Hit) -> Answer:
        """An exact repeat (docs/06 answer cache): the earlier checked answer, zero tokens."""
        cited = tuple(
            CitedSource(c.index, c.source, c.title or "", c.snippet or "") for c in hit.citations
        )
        self.followups = hit.followups
        return Answer(
            language=self.plan.language,
            mode="full",
            status="answered",
            route="documents",
            segments=(AnswerSegment(hit.text),),
            cited=cited,
            provided=hit.retrieved,
        )

    def _request_summary(self, session: Session) -> None:
        plan, ctx = self.plan, self.ctx
        conversation = repo.get_conversation(session, plan.conversation_id, ctx.user_id)
        if conversation is None:
            return
        conversations.request_summary(
            session,
            conversation,
            ctx.user_id,
            self.service._visibility(session, ctx),
            self.service.runtime.llm_config,
        )

    # --- after the answer ---------------------------------------------------------------------

    def closing_events(self, result: Answer, replaced: bool) -> Iterator[AskEvent]:
        if result.error_code is not None and result.message_key is not None:
            yield ErrorEvent(type=result.error_code, message_key=result.message_key)
        yield FinalEvent(
            text=result.text,
            replaced=replaced,
            status=result.status,
            mode=result.mode,
            summarized=self.plan.context.summary is not None,
        )
        for segment in result.segments:
            if segment.text.strip():
                yield TokenEvent(text=segment.text.strip())
        for c in result.cited:
            yield CitationEvent(index=c.index, source=c.source, title=c.title, snippet=c.snippet)

    def after(self, session: Session, result: Answer) -> Iterator[AskEvent]:
        """Follow-up suggestions (and at most one memory suggestion), stored encrypted in
        ``session`` before they are sent: ``followups`` (always, possibly empty), then
        ``memory`` when an item was saved or suggested."""
        plan, svc = self.plan, self.service
        if (
            plan.hit is None
            and plan.remember is None
            and result.status == "answered"
            and (result.mode == "full")
        ):
            asked = [t.question for t in plan.context.turns] + [plan.question]
            found = conversations.suggest(
                svc.runtime.gateway,
                self._metering(),
                questions=asked,
                answer=result.text,
                language=result.language,
                memory_wanted=plan.memory_on,
                telugu=svc.runtime.telugu,
            )
            self.followups = found.questions
            if found.memory is not None and plan.memory_on:
                self._suggest_memory(session, found.memory, result)
        if self.followups:
            blob, _ = conversations.seal(
                session,
                json.dumps(list(self.followups), ensure_ascii=False),
                table=QUERIES_TABLE,
                column=conversations.FOLLOWUPS_COLUMN,
                row_id=plan.query_id,
            )
            repo.update_query(session, plan.query_id, {"followups_ciphertext": blob})
        yield FollowupsEvent(questions=self.followups)
        if self.memory_event is not None:
            yield self.memory_event

    def _suggest_memory(self, session: Session, text: str, result: Answer) -> None:
        plan, ctx = self.plan, self.ctx
        seen = memory.seen_record_terms(session, plan.conversation_id, ctx.user_id)
        note = conversations.tidy(text)
        verdict = memory.screen(self.service.runtime.gateway, self._metering(), note, seen)
        now = dt.datetime.now(dt.UTC)
        existing = memory.items(session, ctx.user_id, now)
        if (
            verdict != "ok"
            or memory.duplicate(existing, note) is not None
            or len(existing) >= memory.config().max_items
        ):
            log.info(
                "kb.memory.suggestion_dropped",
                tenant_id=ctx.tenant_id,
                resource_type="kb_query",
                resource_id=plan.query_id,
                error_code=verdict if verdict != "ok" else "duplicate_or_full",
            )
            return
        row = memory.insert(
            session,
            user_id=ctx.user_id,
            text=note,
            source="suggested",
            status="pending",
            now=now,
            conversation_id=plan.conversation_id,
            query_id=plan.query_id,
        )
        audit.record(
            session,
            action="kb.memory.suggested",
            resource_type="kb_memory",
            resource_id=row.id,
            summary={"source": "suggested", "chars": len(note)},
            request_id=ctx.request_id,
        )
        self.memory_event = MemoryEvent(action="suggested", item_id=row.id, text=note)

    def done(self, result: Answer, latency: int) -> DoneEvent:
        return DoneEvent(
            latency_ms=latency,
            cited_sources=len(result.cited),
            status=result.status,
            mode=result.mode,
        )


class AskStream:
    """The SSE events of one streamed question (docs/06 §5.1). Iterate it once; call
    :meth:`close` when the client goes away (the question is then recorded ``cancelled``).

    Order: ``meta`` at once (the row and audit event are already committed; ``mode`` is
    ``full`` until the answer says otherwise; with ``conversation_id``, ``title`` and, for an
    exact repeat, ``cached``), ``status``* (``understanding``, tool steps with counts,
    ``writing``), ``delta``* (preview), then after the answer is validated, stored and audited
    (its transaction commits first): ``error``?, ``final``, ``token``* (the validated answer
    again, for clients that predate ``final``), ``citation``*, ``followups`` (stored first),
    ``memory``?, ``done`` (with the final ``status`` and ``mode``)."""

    def __init__(
        self,
        service: SchoolKnowledgeService,
        ctx: UserContext,
        plan: _Plan,
        *,
        started: float,
    ) -> None:
        self._service = service
        self._ctx = ctx
        self._plan = plan
        self.query_id = plan.query_id
        self.conversation_id = plan.conversation_id
        self._run_state = _Run(service, ctx, plan, started=started)
        self.progress = self._run_state.progress
        self._started = started
        self._finished = False
        self._gen = self._run()

    def __iter__(self) -> AskStream:
        return self

    def __next__(self) -> AskEvent:
        return next(self._gen)

    def close(self) -> None:
        """Stop the stream (the provider call is closed); an unfinished question is recorded
        ``cancelled`` with what was shown so far. Idempotent."""
        self._gen.close()
        if self._finished:
            return
        self._finished = True
        self._end_early("cancelled", None)

    def _end_early(self, status: str, error_code: str | None) -> None:
        try:
            self._service.end_stream_early(
                self._ctx,
                self.query_id,
                self.progress,
                latency_ms=elapsed_ms(self._started),
                status=status,
                error_code=error_code,
            )
        except Exception as exc:  # the row stays "streaming"; never break the response for it
            log.error(
                "kb.query.end_unrecorded",
                tenant_id=self._ctx.tenant_id,
                resource_type="kb_query",
                resource_id=self.query_id,
                error_type=type(exc).__name__,
            )

    def _run(self) -> Generator[AskEvent, None, None]:
        ctx, run = self._ctx, self._run_state
        # First, before any work: the client (and a BFF waiting for the response to start)
        # hears at once that the question was accepted and recorded.
        yield run.meta()
        try:
            with tenant_session(ctx.tenant_id, ctx.user_id) as session:
                result, latency, replaced = yield from run.answer_events(session, stream=True)
        except Exception as exc:
            log.warning(
                "kb.query.stream_failed",
                tenant_id=ctx.tenant_id,
                resource_type="kb_query",
                resource_id=self.query_id,
                error_type=type(exc).__name__,
            )
            self._finished = True
            self._end_early("error", INTERNAL_ERROR)
            yield ErrorEvent(type=INTERNAL_ERROR, message_key=INTERNAL_ERROR_KEY)
            yield DoneEvent(
                latency_ms=elapsed_ms(self._started),
                cited_sources=0,
                status="error",
                mode="search_only",
            )
            return
        self._finished = True
        yield from run.closing_events(result, replaced)
        after: list[AskEvent] = []
        try:
            with tenant_session(ctx.tenant_id, ctx.user_id) as session:
                after = list(run.after(session, result))
        except Exception as exc:  # suggestions are optional: the answer is already stored
            log.warning(
                "kb.query.followups_failed",
                tenant_id=ctx.tenant_id,
                resource_type="kb_query",
                resource_id=self.query_id,
                error_type=type(exc).__name__,
            )
            after = [FollowupsEvent(questions=())]
        yield from after
        yield run.done(result, latency)


_service: SchoolKnowledgeService | None = None


def get_service() -> SchoolKnowledgeService:
    global _service  # noqa: PLW0603 - a stateless facade over the process runtime
    if _service is None:
        _service = SchoolKnowledgeService()
    return _service


def circulars_config() -> CircularsConfig:
    """Limits for circular reading and notice drafting (``knowledge/config/circulars.yaml``)."""
    return circular_ai.config()


def check_provider_agreements(settings: Settings) -> None:
    """Start-up check of the API and the worker (Claude safety lock, owner decision 2026-10-01):
    in staging/prod, refuse to start while a models.yaml role uses provider anthropic without
    ``SOS_ANTHROPIC_ZDR_CONFIRMED`` (raises ``ProviderModeError``; docs/10 §11)."""
    require_provider_agreements(settings, load_llm_config())


__all__ = [
    "ASK",
    "CIRCULAR_NO_TEXT",
    "INDEXED_HOOKS",
    "MANAGE_VERIFIED",
    "SEARCH",
    "AclKeys",
    "AiUnavailable",
    "AnswerSegment",
    "AskEvent",
    "AskMode",
    "AskRequest",
    "AskResponse",
    "AskStream",
    "CircularContext",
    "CircularReading",
    "CircularsConfig",
    "Citation",
    "CitationEvent",
    "ConfirmedDeadline",
    "DeadlineSuggestion",
    "DeltaEvent",
    "DoneEvent",
    "ErrorEvent",
    "FinalEvent",
    "FollowupsEvent",
    "IndexedHook",
    "IngestionPipeline",
    "KnowledgeService",
    "Locale",
    "MemoryEvent",
    "MetaEvent",
    "NoticeDraft",
    "NoticeSource",
    "Passage",
    "PassageCitation",
    "RankedChunk",
    "ReadingOutcome",
    "SchoolKnowledgeService",
    "SearchFilters",
    "StatusEvent",
    "TokenEvent",
    "adopt_conversations",
    "check_provider_agreements",
    "circular_passages",
    "circulars_config",
    "draft_notice",
    "export_records",
    "get_service",
    "has_personal_numbers",
    "purge_memories",
    "purge_old_queries",
    "purge_orphan_vectors",
    "purge_tenant_data",
    "read_circular",
    "reencrypt_queries",
    "summarise_conversation",
    "tenant_data_counts",
]


# DEK rotation (SEC-012) covers the query log, conversations and memory items (ADR-0034):
# rotation batches re-encrypt kb.queries, kb.conversations and kb.user_memories too.
key_rotation.register_reencryptor("kb_queries", reencrypt_queries_batch)
key_rotation.register_reencryptor("kb_conversations", keys.reencrypt_conversations_batch)
key_rotation.register_reencryptor("kb_memories", keys.reencrypt_memories_batch)


# --- query log retention (docs/05 §13, docs/08 §7; FR-ADM-002 shows it) -------------------------

SUMMARY_TASK: Final = conversations.SUMMARY_TASK
# The rolling summary job consumes this outbox event (queue ingest; ADR-0034).
ops.register_outbox_route(conversations.SUMMARY_EVENT, SUMMARY_TASK)

QUERY_RETENTION_CATEGORY: Final = "kb_queries"
"""The ``app/admin/retention.yaml`` category of the query log (fixed: not school-configurable)."""
ADOPT_BATCH: Final = 200


def purge_old_queries(session: Session, *, now: dt.datetime | None = None) -> int:
    """Delete the current school's questions and answers older than the query-log retention
    (``query_log.retention_days`` in models.yaml, 180 days; a school setting would win through
    :mod:`app.core.retention`, but the category is fixed). Call inside the school's
    ``tenant_session``; returns the number of rows deleted. Audit events of the questions stay
    (ids and counts only); the metering ledger ``kb.llm_calls`` keeps its own rows.

    Conversations follow their questions (ADR-0034): a rolling summary that covers a deleted
    question is forgotten (rebuilt from the questions kept), and a conversation with no question
    left (deleted from the history or not) is deleted."""
    keep = retention.days(
        session, QUERY_RETENTION_CATEGORY, default=load_llm_config().query_log.retention_days
    )
    cutoff = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=keep)
    deleted = repo.delete_queries_before(session, cutoff)
    repo.clear_summaries_before(session, cutoff)
    repo.delete_empty_conversations(session)
    return deleted


def purge_orphan_vectors(session: Session, *, now: dt.datetime | None = None) -> int:
    """Delete the current school's cached document vectors that no chunk uses any more and that
    are older than ``orphan_vector_grace_hours`` (embeddings.yaml; docs/08 §7 erasure chain).
    Deleting a document or version already removes its vectors in the same transaction; this
    daily sweep catches vectors cached before that existed and ones left by a job that stopped
    between embedding and writing. Call inside the school's ``tenant_session``."""
    grace = load_embeddings_config().orphan_vector_grace_hours
    cutoff = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(hours=grace)
    return repo.purge_orphan_embeddings(session, older_than=cutoff)


def purge_memories(session: Session, *, now: dt.datetime | None = None) -> int:
    """Memory retention (ADR-0034), in the school's ``tenant_session``: suggestions nobody
    confirmed within ``memory.pending_ttl_hours`` are deleted, and so is every item and setting
    of a person who is no longer an active member of the school. Returns items deleted."""
    at = now or dt.datetime.now(dt.UTC)
    deleted = repo.delete_expired_memories(session, at)
    owners = repo.memory_user_ids(session)
    if owners:
        members = identity.members_for_users(session, owners)
        active = {m.membership_id for m in identity.active_members(session)}
        gone = sorted(u for u in owners if u not in members or members[u][0] not in active)
        deleted += repo.delete_memory_data_of(session, gone)
    return deleted


def adopt_conversations(session: Session, *, limit: int = ADOPT_BATCH) -> int:
    """Give questions asked before conversations existed (0038) a conversation: one per
    ``(session_id, user)``, id = the session id, titled (encrypted) from its first question.
    Idempotent; a session id already used by another person's conversation is left alone (those
    questions stay in the query log only, never in anyone's history). Returns conversations made."""
    made = 0
    for session_id, user_id in repo.orphan_sessions(session, limit):
        if repo.conversation_owner(session, session_id) is not None:
            continue  # taken by another person's group in this batch
        rows = repo.session_questions(session, session_id, user_id)
        first = conversations.question_of(session, rows[0]) if rows else None
        if first is None:
            continue
        title = conversations.derive_title(first, telugu=languages.telugu_enabled())
        blob, version = conversations.seal(
            session,
            title,
            table=conversations.CONVERSATIONS_TABLE,
            column=conversations.TITLE_COLUMN,
            row_id=session_id,
        )
        repo.insert_conversation(
            session,
            {
                "id": session_id,
                "user_id": user_id,
                "title_ciphertext": blob,
                "key_version": version,
                "created_at": rows[0].created_at,
                "updated_at": rows[-1].created_at,
            },
        )
        repo.link_session(session, session_id, user_id)
        made += 1
    return made


def summarise_conversation(tenant_id: uuid.UUID, payload: dict[str, object]) -> str:
    """Worker job ``knowledge.summarise_conversation`` (outbox event
    ``kb.conversation.summary_requested``; ids only in the payload): the rolling summary,
    never on a question's path."""
    return conversations.summarise(
        tenant_id,
        payload,
        composition.runtime().gateway,
        session_factory=tenant_session,
    )


# --- full data export (FR-ADM-001; ADR-0034) --------------------------------------------------


def export_records(session: Session) -> list[RecordTable]:
    """Worker only: the school's Ask memory for its full data export (the caller checked
    ``tenant.export_all`` and audits the export). Memory items are decrypted (the archive masks
    Aadhaar-like numbers in every text value; items never hold them). The query log and
    conversations are not in the archive (docs/05 §12, 14 · M1 status: knowledge questions)."""
    items: list[tuple[object, ...]] = [
        (
            r.id,
            r.user_id,
            r.source,
            r.status,
            redact(memory.text_of(session, r)),
            r.created_at,
            r.updated_at,
            r.expires_at,
        )
        for r in repo.export_memories(session)
    ]
    switches: list[tuple[object, ...]] = [
        (r.user_id, r.enabled, r.updated_at) for r in repo.export_memory_settings(session)
    ]
    return [
        RecordTable(
            name="ask_memories",
            columns=(
                "id",
                "user_id",
                "source",
                "status",
                "text",
                "created_at",
                "updated_at",
                "expires_at",
            ),
            rows=list(items),
        ),
        RecordTable(
            name="ask_memory_settings", columns=("user_id", "enabled", "updated_at"), rows=switches
        ),
    ]


# --- offboarding purge (FR-PLT-005, ADR-0029) ------------------------------------------------
# Ask-the-school data: memory, chunks, verified answers, the query log and conversations, the
# model-call log, the embedding cache.
# Registered with app.tenancy at import; the offboarding job counts them as sos_app and deletes
# them as sos_purger (children before parents) inside the school's tenant_session.
_PURGE = purging.PurgeTables(
    deleted=(
        "kb.user_memories",
        "kb.user_memory_settings",
        "kb.document_chunks",
        "kb.verified_answers",
        "kb.queries",
        "kb.conversations",
        "kb.llm_calls",
        "kb.embedding_cache",
    ),
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only: the database allows it
    only as ``sos_purger`` for a school in ``offboarding``)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="knowledge", count=tenant_data_counts, purge=purge_tenant_data)
)
