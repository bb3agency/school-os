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
only ``search_result`` blocks built from what the caller may read; citations are validated
server-side (FR-KB-005) and an unsupported answer says "not found in school records"; tools are
read-only (invariant 9, ADR-0008); every provider call goes through ``knowledge.gateway``
(ADR-0005). Each question writes one ``kb.queries`` row (question and answer encrypted under the
school's key, a keyed HMAC of the question, ids, codes and counts; FR-KB-009) and one audit
event ``kb.query.asked`` (ids and counts only; invariant 7) in the caller's transaction, BEFORE
any event is returned: nothing is shown that was not recorded. Logs never carry question or
answer text (invariant 5).
"""

from __future__ import annotations

import datetime as dt
import re
import time
import uuid
from collections.abc import Generator, Iterator
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from app.audit import service as audit
from app.authz.kv import KVUnavailable, kv_store
from app.core import purge as purging
from app.core import retention
from app.core.db import tenant_session
from app.core.errors import Conflict, Forbidden, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.redaction import mask_aadhaar
from app.core.textnorm import nfc
from app.documents import service as documents
from app.identity import service as identity
from app.knowledge import circular_ai, composition, sources
from app.knowledge import repository as repo
from app.knowledge.answer import Answer, Progress, detect_language, elapsed_ms, normalise
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
    Locale,
    MetaEvent,
    RankedChunk,
    SearchFilters,
    TokenEvent,
)
from app.knowledge.gateway.errors import AiRateLimited
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
from app.knowledge.models import VerifiedAnswer
from app.knowledge.schemas import (
    FeedbackIn,
    FeedbackOut,
    SearchResultOut,
    VerifiedAnswerIn,
    VerifiedAnswerOut,
    VerifiedAnswerReviewIn,
    VerifiedCitationOut,
)
from app.students import crypto
from app.students import rotation as key_rotation
from app.tenancy import service as tenancy

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext

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
        question = mask_aadhaar(nfc(request.question).strip())
        if not question or len(question) > MAX_QUESTION_CHARS:
            raise ValidationFailed([_error("question", "question_length")])
        return question

    def earlier_questions(
        self, session: Session, ctx: UserContext, session_id: uuid.UUID
    ) -> tuple[str, ...]:
        """The caller's OWN earlier questions in this Ask session, oldest first (docs/06 §5
        conversation rules, FR-KB-012): at most ``max_earlier_questions``, none older than
        ``max_age_minutes``, only completed ones. Never another user's, never answers."""
        rules = self.runtime.llm_config.conversation
        since = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=rules.max_age_minutes)
        rows = repo.earlier_questions(
            session,
            user_id=ctx.user_id,
            session_id=session_id,
            since=since,
            limit=rules.max_earlier_questions,
        )
        out: list[str] = []
        for row_id, blob in reversed(rows):
            try:
                out.append(
                    crypto.decrypt_value(
                        session, blob, table=QUERIES_TABLE, column=QUESTION_COLUMN, row_id=row_id
                    )
                )
            except crypto.CryptoError:
                log.warning(
                    "kb.query.history_unreadable",
                    tenant_id=ctx.tenant_id,
                    resource_type="kb_query",
                    resource_id=row_id,
                )
        return tuple(out)

    def respond(self, session: Session, ctx: UserContext, request: AskRequest) -> AskResponse:
        """One question answered whole: the validated :class:`Answer`, its events, stored and
        audited (the eval harness and non-streaming callers)."""
        question = self._question(ctx, request)
        started = time.monotonic()
        query_id = new_id()
        earlier = self.earlier_questions(session, ctx, request.session_id)
        school = tenancy.get_tenant(session)
        result = self.runtime.engine.run(
            session, ctx, question, query_id=query_id, school_name=school.name, earlier=earlier
        )
        latency = elapsed_ms(started)
        self._record(
            session,
            ctx,
            request=request,
            query_id=query_id,
            question=question,
            result=result,
            latency_ms=latency,
        )
        events = tuple(self._events(query_id, result, latency))
        return AskResponse(query_id=query_id, answer=result, events=events)

    def start_stream(self, session: Session, ctx: UserContext, request: AskRequest) -> AskStream:
        """Begin a streamed question (``POST /knowledge/ask``; docs/06 §5.1).

        In the CALLER's transaction: checks, the caller's earlier questions of this session,
        and the ``kb.queries`` row (status ``streaming``, question encrypted) with its audit
        event ``kb.query.asked``. The caller commits BEFORE iterating the returned stream, so
        nothing is shown that was not recorded (invariant 7). The stream then runs in its own
        transactions and completes the same row (``kb.query.completed``), or records it
        ``cancelled`` when closed early (``kb.query.cancelled``)."""
        question = self._question(ctx, request)
        started = time.monotonic()
        query_id = new_id()
        earlier = self.earlier_questions(session, ctx, request.session_id)
        language = detect_language(question)
        q_blob, version, digest = self._encrypt_question(session, query_id, question)
        repo.insert_query(
            session,
            {
                "id": query_id,
                "session_id": request.session_id,
                "user_id": ctx.user_id,
                "question_ciphertext": q_blob,
                "question_hmac": digest,
                "key_version": version,
                "language": language,
                "mode": "full",
                "status": "streaming",
            },
        )
        audit.record(
            session,
            action="kb.query.asked",
            resource_type="kb_query",
            resource_id=query_id,
            summary={
                "mode": "full",
                "status": "streaming",
                "language": language,
                "earlier_questions": len(earlier),
            },
            request_id=ctx.request_id,
        )
        return AskStream(
            self,
            ctx,
            query_id=query_id,
            question=question,
            language=language,
            earlier=earlier,
            started=started,
        )

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
        }

    def _record(
        self,
        session: Session,
        ctx: UserContext,
        *,
        request: AskRequest,
        query_id: uuid.UUID,
        question: str,
        result: Answer,
        latency_ms: int,
    ) -> None:
        q_blob, version, digest = self._encrypt_question(session, query_id, question)
        repo.insert_query(
            session,
            {
                "id": query_id,
                "session_id": request.session_id,
                "user_id": ctx.user_id,
                "question_ciphertext": q_blob,
                "question_hmac": digest,
                "answer_ciphertext": self._encrypt_answer(session, query_id, result.text),
                "key_version": version,
                **self._outcome_values(result, latency_ms),
            },
        )
        audit.record(
            session,
            action="kb.query.asked",
            resource_type="kb_query",
            resource_id=query_id,
            summary=self._summary(result),
            request_id=ctx.request_id,
        )
        self._log_answered(ctx, query_id, result, latency_ms)

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
    ) -> None:
        """A streamed question finished: the row gets the validated answer (encrypted), codes
        and counts; audit ``kb.query.completed`` in the same transaction."""
        repo.update_query(
            session,
            query_id,
            {
                "answer_ciphertext": self._encrypt_answer(session, query_id, result.text),
                **self._outcome_values(result, latency_ms),
            },
        )
        audit.record(
            session,
            action="kb.query.completed",
            resource_type="kb_query",
            resource_id=query_id,
            summary={**self._summary(result), "streamed": True, "replaced": replaced},
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

    @staticmethod
    def _events(query_id: uuid.UUID, result: Answer, latency_ms: int) -> Iterator[AskEvent]:
        yield MetaEvent(query_id=query_id, language=result.language, mode=result.mode)
        yield from SchoolKnowledgeService._closing_events(result, latency_ms)

    @staticmethod
    def _closing_events(result: Answer, latency_ms: int) -> Iterator[AskEvent]:
        if result.error_code is not None and result.message_key is not None:
            yield ErrorEvent(type=result.error_code, message_key=result.message_key)
        for segment in result.segments:
            if segment.text.strip():
                yield TokenEvent(text=segment.text.strip())
        for c in result.cited:
            yield CitationEvent(index=c.index, source=c.source, title=c.title, snippet=c.snippet)
        yield DoneEvent(
            latency_ms=latency_ms,
            cited_sources=len(result.cited),
            status=result.status,
            mode=result.mode,
        )

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
        if row is None:
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


class AskStream:
    """The SSE events of one streamed question (docs/06 §5.1). Iterate it once; call
    :meth:`close` when the client goes away (the question is then recorded ``cancelled``).

    Order: ``meta`` at once (the row and audit event are already committed; ``mode`` is
    ``full`` until the answer says otherwise), ``delta``* (preview), then after the answer is
    validated, stored and audited (its transaction commits first): ``error``?, ``final``,
    ``token``* (the validated answer again, for clients that predate ``final``),
    ``citation``*, ``done`` (with the final ``status`` and ``mode``)."""

    def __init__(
        self,
        service: SchoolKnowledgeService,
        ctx: UserContext,
        *,
        query_id: uuid.UUID,
        question: str,
        language: Locale,
        earlier: tuple[str, ...],
        started: float,
    ) -> None:
        self._service = service
        self._ctx = ctx
        self.query_id = query_id
        self._question = question
        self._language: Locale = language
        self._earlier = earlier
        self._started = started
        self.progress = Progress()
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

    def _meta(self, mode: AskMode) -> MetaEvent:
        return MetaEvent(query_id=self.query_id, language=self._language, mode=mode)

    def _run(self) -> Generator[AskEvent, None, None]:
        svc, ctx = self._service, self._ctx
        # First, before any work: the client (and a BFF waiting for the response to start)
        # hears at once that the question was accepted and recorded.
        yield self._meta("full")
        try:
            with tenant_session(ctx.tenant_id, ctx.user_id) as session:
                school = tenancy.get_tenant(session)
                engine = svc.runtime.engine.stream(
                    session,
                    ctx,
                    self._question,
                    query_id=self.query_id,
                    school_name=school.name,
                    progress=self.progress,
                    earlier=self._earlier,
                )
                with closing(engine):
                    while True:
                        try:
                            delta = next(engine)
                        except StopIteration as done:
                            result: Answer = done.value
                            break
                        yield DeltaEvent(text=delta.text)
                latency = elapsed_ms(self._started)
                replaced = was_replaced(self.progress.text, result)
                svc.complete_stream(
                    session, ctx, self.query_id, result, latency_ms=latency, replaced=replaced
                )
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
        if result.error_code is not None and result.message_key is not None:
            yield ErrorEvent(type=result.error_code, message_key=result.message_key)
        yield FinalEvent(
            text=result.text, replaced=replaced, status=result.status, mode=result.mode
        )
        for segment in result.segments:
            if segment.text.strip():
                yield TokenEvent(text=segment.text.strip())
        for c in result.cited:
            yield CitationEvent(index=c.index, source=c.source, title=c.title, snippet=c.snippet)
        yield DoneEvent(
            latency_ms=latency,
            cited_sources=len(result.cited),
            status=result.status,
            mode=result.mode,
        )


_service: SchoolKnowledgeService | None = None


def get_service() -> SchoolKnowledgeService:
    global _service  # noqa: PLW0603 - a stateless facade over the process runtime
    if _service is None:
        _service = SchoolKnowledgeService()
    return _service


def circulars_config() -> CircularsConfig:
    """Limits for circular reading and notice drafting (``knowledge/config/circulars.yaml``)."""
    return circular_ai.config()


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
    "IndexedHook",
    "IngestionPipeline",
    "KnowledgeService",
    "Locale",
    "MetaEvent",
    "NoticeDraft",
    "NoticeSource",
    "Passage",
    "PassageCitation",
    "RankedChunk",
    "ReadingOutcome",
    "SchoolKnowledgeService",
    "SearchFilters",
    "TokenEvent",
    "circular_passages",
    "circulars_config",
    "draft_notice",
    "get_service",
    "has_personal_numbers",
    "purge_old_queries",
    "purge_tenant_data",
    "read_circular",
    "reencrypt_queries",
    "tenant_data_counts",
]


# DEK rotation (SEC-012) covers the query log: rotation batches re-encrypt kb.queries too.
key_rotation.register_reencryptor("kb_queries", reencrypt_queries_batch)


# --- query log retention (docs/05 §13, docs/08 §7; FR-ADM-002 shows it) -------------------------

QUERY_RETENTION_CATEGORY: Final = "kb_queries"
"""The ``app/admin/retention.yaml`` category of the query log (fixed: not school-configurable)."""


def purge_old_queries(session: Session, *, now: dt.datetime | None = None) -> int:
    """Delete the current school's questions and answers older than the query-log retention
    (``query_log.retention_days`` in models.yaml, 180 days; a school setting would win through
    :mod:`app.core.retention`, but the category is fixed). Call inside the school's
    ``tenant_session``; returns the number of rows deleted. Audit events of the questions stay
    (ids and counts only); the metering ledger ``kb.llm_calls`` keeps its own rows."""
    keep = retention.days(
        session, QUERY_RETENTION_CATEGORY, default=load_llm_config().query_log.retention_days
    )
    cutoff = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=keep)
    return repo.delete_queries_before(session, cutoff)


# --- offboarding purge (FR-PLT-005, ADR-0029) ------------------------------------------------
# Ask-the-school data: chunks, verified answers, the query and model-call logs, the
# embedding cache.
# Registered with app.tenancy at import; the offboarding job counts them as sos_app and deletes
# them as sos_purger (children before parents) inside the school's tenant_session.
_PURGE = purging.PurgeTables(
    deleted=(
        "kb.document_chunks",
        "kb.verified_answers",
        "kb.queries",
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
