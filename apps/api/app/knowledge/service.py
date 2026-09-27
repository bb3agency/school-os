"""Knowledge public API ("Ask the school"; docs/06). Other modules import only this module.

What other modules and the routes use:

- :func:`get_service` -> :class:`SchoolKnowledgeService`, the :class:`KnowledgeService`:
  ``ask`` (the SSE events of docs/06 §5.1 for one question), ``search_documents`` (search-only
  retrieval, also the fallback mode), query feedback and verified answers (FR-KB-030).
- :func:`reencrypt_queries` (DEK rotation, SEC-012): re-encrypts ``kb.queries`` to the
  school's active key version in the caller's ``tenant_session``; register it with
  ``register_reencryptor("kb_queries", reencrypt_queries)``.
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
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from app.audit import service as audit
from app.authz.kv import KVUnavailable, kv_store
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.redaction import mask_aadhaar
from app.core.textnorm import nfc
from app.documents import service as documents
from app.knowledge import composition, sources
from app.knowledge import repository as repo
from app.knowledge.answer import Answer, elapsed_ms, normalise
from app.knowledge.domain import (
    AclKeys,
    AnswerSegment,
    AskEvent,
    AskMode,
    AskRequest,
    Citation,
    CitationEvent,
    DoneEvent,
    ErrorEvent,
    Locale,
    MetaEvent,
    RankedChunk,
    SearchFilters,
    TokenEvent,
)
from app.knowledge.gateway.errors import AiRateLimited
from app.knowledge.interfaces import IngestionPipeline, KnowledgeService
from app.knowledge.keys import (
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
QUERIES_TABLE: Final = "kb.queries"
MAX_QUESTION_CHARS: Final = 1000


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


def _verified_out(row: VerifiedAnswer) -> VerifiedAnswerOut:
    return VerifiedAnswerOut(
        id=row.id,
        question=row.question_canonical,
        language=row.language,
        answer_text=row.answer_text,
        citations=[VerifiedCitationOut.model_validate(c) for c in row.citations],
        status=row.status,
        verified_by=row.verified_by,
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

    def respond(self, session: Session, ctx: UserContext, request: AskRequest) -> AskResponse:
        """One question: the validated :class:`Answer`, its events, stored and audited."""
        if not ctx.has(ASK):
            raise Forbidden()
        started = time.monotonic()
        question = mask_aadhaar(nfc(request.question).strip())
        if not question or len(question) > MAX_QUESTION_CHARS:
            raise ValidationFailed([_error("question", "question_length")])
        query_id = new_id()
        school = tenancy.get_tenant(session)
        result = self.runtime.engine.run(
            session, ctx, question, query_id=query_id, school_name=school.name
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
        q_blob, version = crypto.encrypt_value(
            session,
            question,
            table=QUERIES_TABLE,
            column="question_ciphertext",
            row_id=query_id,
        )
        a_blob = None
        if result.text:
            a_blob, _ = crypto.encrypt_value(
                session,
                result.text,
                table=QUERIES_TABLE,
                column="answer_ciphertext",
                row_id=query_id,
            )
        digest, _ = crypto.blind_index(
            session, question_key(question), purpose=QUESTION_PURPOSE, key_version=version
        )
        cited = {c.source for c in result.cited}
        repo.insert_query(
            session,
            {
                "id": query_id,
                "session_id": request.session_id,
                "user_id": ctx.user_id,
                "question_ciphertext": q_blob,
                "question_hmac": digest,
                "answer_ciphertext": a_blob,
                "key_version": version,
                "language": result.language,
                "mode": result.mode,
                "route": result.route,
                "status": result.status,
                "error": result.error_code,
                "tools": [
                    {"tool": t.name, "results": t.results, "error": t.error} for t in result.tools
                ],
                "retrieved": [{"source": s} for s in result.provided],
                "citations": [{"source": s} for s in sorted(cited)],
                "model_ids": list(result.model_ids),
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "latency_ms": latency_ms,
            },
        )
        audit.record(
            session,
            action="kb.query.asked",
            resource_type="kb_query",
            resource_id=query_id,
            summary={
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
            },
            request_id=ctx.request_id,
        )
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

    @staticmethod
    def _events(query_id: uuid.UUID, result: Answer, latency_ms: int) -> Iterator[AskEvent]:
        yield MetaEvent(query_id=query_id, language=result.language, mode=result.mode)
        if result.error_code is not None and result.message_key is not None:
            yield ErrorEvent(type=result.error_code, message_key=result.message_key)
        for segment in result.segments:
            if segment.text:
                yield TokenEvent(text=segment.text)
        for c in result.cited:
            yield CitationEvent(index=c.index, source=c.source, title=c.title, snippet=c.snippet)
        yield DoneEvent(latency_ms=latency_ms, cited_sources=len(result.cited))

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
    def _cites_visible(session: Session, ctx: UserContext, row: VerifiedAnswer) -> bool:
        for citation in row.citations:
            try:
                ref = sources.parse(str(citation.get("source", "")))
            except ValueError:
                return False
            if ref.kind != "doc" or not documents.is_visible(session, ctx, ref.object_id):
                return False
        return True

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
        page = [_verified_out(r) for r in rows if self._cites_visible(session, ctx, r)]
        return page, (rows[-1].id if more and rows else None)

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
        try:
            doc = documents.get_document(session, ctx, ref.object_id)
        except NotFound:
            return _error(field, "citation_not_found")
        current = doc.current_version
        if doc.status != "active" or current is None or current.version_no != ref.version_no:
            return _error(field, "citation_not_current")
        texts = repo.latest_page_texts(session, current.id, ref.page)
        wanted = normalise(cited_text)
        if not wanted or not any(wanted in normalise(t) for t in texts):
            return _error(f"citations[{index}].cited_text", "citation_text_not_found")
        return None

    def create_verified_answer(
        self, session: Session, ctx: UserContext, data: VerifiedAnswerIn
    ) -> VerifiedAnswerOut:
        """Store an approved answer (``kb.verified_answer.manage``). Every citation must quote
        the current version of a document the caller can read (docs/06 §8-9)."""
        if not ctx.has(MANAGE_VERIFIED):
            raise Forbidden()
        errors = [
            e
            for i, c in enumerate(data.citations)
            if (e := self._check_citation(session, ctx, i, c.source, c.cited_text)) is not None
        ]
        if errors:
            raise ValidationFailed(errors)
        now = dt.datetime.now(dt.UTC)
        row = repo.insert_verified_answer(
            session,
            {
                "id": new_id(),
                "question_canonical": mask_aadhaar(nfc(data.question)),
                "language": data.language,
                "answer_text": mask_aadhaar(nfc(data.answer_text)),
                "citations": [
                    {"source": c.source, "cited_text": nfc(c.cited_text)} for c in data.citations
                ],
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
        return _verified_out(row)


_service: SchoolKnowledgeService | None = None


def get_service() -> SchoolKnowledgeService:
    global _service  # noqa: PLW0603 - a stateless facade over the process runtime
    if _service is None:
        _service = SchoolKnowledgeService()
    return _service


__all__ = [
    "ASK",
    "MANAGE_VERIFIED",
    "SEARCH",
    "AclKeys",
    "AnswerSegment",
    "AskEvent",
    "AskMode",
    "AskRequest",
    "AskResponse",
    "Citation",
    "CitationEvent",
    "DoneEvent",
    "ErrorEvent",
    "IngestionPipeline",
    "KnowledgeService",
    "Locale",
    "MetaEvent",
    "RankedChunk",
    "SchoolKnowledgeService",
    "SearchFilters",
    "TokenEvent",
    "get_service",
    "reencrypt_queries",
]


# DEK rotation (SEC-012) covers the query log: rotation batches re-encrypt kb.queries too.
key_rotation.register_reencryptor("kb_queries", reencrypt_queries_batch)
