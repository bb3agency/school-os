"""Ask conversations: titles, context, rolling summary, query rewrite and follow-ups.

docs/06 §5 (conversation rules, prompt layout, cost and performance design); ADR-0033;
FR-KB-008, FR-KB-009, FR-KB-012 (as amended). Used only by :mod:`app.knowledge.service` (and the
worker task of the rolling summary). Every stored text here is ciphertext under the school's
key (``kb.conversations`` title and summary; ``kb.queries`` citations and follow-ups); nothing
here logs a title, question, answer, summary or suggestion (invariant 5): ids, codes and counts.

- **Titles**: a new conversation's title is its first question, NFC, whitespace collapsed,
  Aadhaar-like numbers masked, cut at a word boundary to ``titles.derived_max_chars`` (no model
  call). A title the user sets is refused (422) when it holds an Aadhaar-like number.
- **Context** (:func:`build_context`): the caller's own conversation only. The last
  ``max_earlier_questions`` current (not superseded), completed turns within
  ``history_token_budget``: each question, and its checked answer only when every source that
  answer cited is still visible to the caller NOW (``visibility.SourceVisibility``;
  invariant 8). The rolling summary only when every source behind it is still visible (else it is
  forgotten and rebuilt later from what is visible).
- **Rolling summary** (:func:`request_summary`, :func:`summarise`): after an answer, when current
  turns older than the recent window are not covered yet, the answer's transaction queues the
  worker job ``knowledge.summarise_conversation`` (outbox; ids only in the payload, including
  which earlier answers were visible to the caller then). The job calls the ``summary`` role
  through the gateway (metered, budget-checked) outside any transaction and stores the result
  encrypted with the sources it rests on. A question never waits for it.
- **Query rewrite** (:func:`rewrite`): a follow-up (with earlier turns or a summary) becomes a
  standalone question through the ``query_rewrite`` role before the answer loop; the model and
  the search-only fallback search with it. The original question is what is shown, stored and
  audited. Any failure: the original question is used.
- **Follow-ups** (:func:`suggest`): after an ``answered`` question, one ``followups`` call gives
  0-3 short questions in the answer's language (and at most one memory suggestion, checked by
  :mod:`app.knowledge.memory`). Suggestions pass the output sanitiser; one with an Aadhaar-like,
  phone number or email, in another script, too long, or naming a number or name the
  conversation does not contain is dropped.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from app.core.logging import get_logger
from app.core.redaction import contains_full_aadhaar, mask_aadhaar, redact
from app.knowledge import repository as repo
from app.knowledge.answer import AskContext, detect_language, sanitise
from app.knowledge.config.conversations import ConversationsConfig, load_conversations_config
from app.knowledge.config.llm import LlmConfig
from app.knowledge.domain import HistoryTurn, Locale, Metering
from app.knowledge.gateway.errors import GatewayError, GatewayMisuse
from app.knowledge.interfaces import LlmGateway
from app.knowledge.models import Conversation, Query
from app.knowledge.prompts.registry import load_prompt
from app.knowledge.sealed import (
    ANSWER_COLUMN,
    CITATIONS_COLUMN,
    CONVERSATIONS_TABLE,
    FOLLOWUPS_COLUMN,
    QUERIES_TABLE,
    QUESTION_COLUMN,
    SUMMARY_COLUMN,
    TITLE_COLUMN,
    StoredCitation,
    answer_of,
    cited_sources_of,
    encode_citations,
    question_of,
    seal,
    stored_citations,
    stored_followups,
    strip_markers,
    title_of,
    unseal,
)
from app.knowledge.visibility import SourceVisibility
from app.ops import service as ops

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

SUMMARY_EVENT: Final = "kb.conversation.summary_requested"
SUMMARY_TASK: Final = "knowledge.summarise_conversation"
MAX_PENDING: Final = 50
"""At most this many not-yet-summarised older turns go into one summary job (the newest)."""
CONTEXT_STATUSES: Final = repo.EARLIER_STATUSES

_WS: Final = re.compile(r"\s+")
_CONTROL: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SEP: Final = r"[\s\-\u2010-\u2013]?"
_AADHAAR_LIKE: Final = re.compile(rf"(?<!\d)\d{{4}}{_SEP}\d{{4}}{_SEP}\d{{4}}(?!\d)")
_TELUGU: Final = re.compile(r"[ఀ-౿]")
_DIGITS: Final = re.compile(r"\w*\d[\w/.\-]*")
_NAME: Final = re.compile(r"\b[A-Z][a-z]{2,}\b")

SCHEMA_TAGS: Final = {
    "rewrite": "sos:ask.query_rewrite.v1",
    "summary": "sos:ask.conversation_summary.v1",
    "followups": "sos:ask.followups.v1",
}
REWRITE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "description": SCHEMA_TAGS["rewrite"],
    "additionalProperties": False,
    "required": ["question"],
    "properties": {"question": {"type": "string", "description": "The standalone question"}},
}
SUMMARY_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "description": SCHEMA_TAGS["summary"],
    "additionalProperties": False,
    "required": ["summary"],
    "properties": {"summary": {"type": "string", "description": "The updated summary"}},
}
FOLLOWUPS_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "description": SCHEMA_TAGS["followups"],
    "additionalProperties": False,
    "required": ["questions", "memory"],
    "properties": {
        "questions": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "memory": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
}


def config() -> ConversationsConfig:
    return load_conversations_config()


# --- text rules ---------------------------------------------------------------------------------


def aadhaar_like(text: str) -> bool:
    """Any 12-digit number (4-4-4, with or without separators) or a Verhoeff-valid Aadhaar."""
    normalised = unicodedata.normalize("NFC", text)
    return bool(_AADHAAR_LIKE.search(normalised)) or contains_full_aadhaar(normalised)


def mask_numbers(text: str) -> str:
    """Aadhaar numbers masked as elsewhere, and any other 12-digit run too (titles, summaries)."""
    return _AADHAAR_LIKE.sub("XXXX XXXX XXXX", mask_aadhaar(text))


def tidy(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip()


def cut(text: str, limit: int) -> str:
    """``text`` within ``limit`` characters, the "…" included: at the last space that leaves
    room for it, else mid-word."""
    if len(text) <= limit:
        return text
    window = text[:limit]
    head = window.rsplit(" ", 1)[0] if " " in window else ""
    return (head.rstrip(" ,;:") or window[: limit - 1]) + "…"


def derive_title(question: str, limit: int | None = None) -> str:
    """A new conversation's title: its first question, tidied, numbers masked, cut at a word."""
    return cut(mask_numbers(tidy(question)), limit or config().titles.derived_max_chars)


def title_problem(title: str) -> str | None:
    """Why a title the user sends is refused (a code), or None."""
    if _CONTROL.search(title):
        return "title_control_characters"
    if aadhaar_like(title):
        return "title_personal_number"
    return None


# --- ciphertext -----------------------------------------------------------------------------------


# --- context (docs/06 §5 prompt layout) -----------------------------------------------------------


def visible_answer(
    session: Session, row: Query, visibility: SourceVisibility, limit: int
) -> str | None:
    """The checked answer of an earlier turn, markers removed and cut, only when every source it
    cited is still visible to the caller (invariant 8); None otherwise or when there is none."""
    if row.status != "answered" or limit <= 0:
        return None
    if not visibility.all_visible(cited_sources_of(session, row)):
        return None
    text = answer_of(session, row)
    return cut(strip_markers(text), limit) if text else None


def build_context(
    session: Session,
    conversation: Conversation,
    user_id: uuid.UUID,
    visibility: SourceVisibility,
    llm: LlmConfig,
    *,
    before: Sequence[Query] | None = None,
) -> tuple[tuple[HistoryTurn, ...], str | None]:
    """Recent turns (oldest first) and the usable summary of the caller's conversation.

    ``before``: the thread to use (the current turns before an edited or regenerated message);
    default: every current, completed turn."""
    rules = llm.conversation
    rows = (
        list(before)
        if before is not None
        else repo.completed_turns(session, conversation.id, user_id)
    )
    rows = [r for r in rows if r.status in CONTEXT_STATUSES]
    recent = rows[-rules.max_earlier_questions :] if rules.max_earlier_questions else []
    budget = rules.history_token_budget * llm.limits.chars_per_token_estimate
    turns: list[HistoryTurn] = []
    used = 0
    for row in reversed(recent):
        question = question_of(session, row)
        if question is None:
            continue
        answer = visible_answer(session, row, visibility, rules.earlier_answer_max_chars)
        size = len(question) + len(answer or "")
        if used + size > budget:
            if used + len(question) > budget:
                break
            answer, size = None, len(question)
        used += size
        turns.append(HistoryTurn(question=question, answer=answer))
    turns.reverse()
    return tuple(turns), usable_summary(session, conversation, visibility)


def usable_summary(
    session: Session, conversation: Conversation, visibility: SourceVisibility
) -> str | None:
    """The rolling summary, when every source behind it is still visible to the caller; a
    summary resting on something the caller can no longer see is forgotten (it is rebuilt from
    what is visible after the next answer)."""
    if conversation.summary_ciphertext is None:
        return None
    if not visibility.all_visible(str(s) for s in conversation.summary_sources or []):
        forget_summary(session, conversation.id)
        log.info(
            "kb.conversation.summary_withheld",
            resource_type="kb_conversation",
            resource_id=conversation.id,
        )
        return None
    return unseal(
        session,
        conversation.summary_ciphertext,
        table=CONVERSATIONS_TABLE,
        column=SUMMARY_COLUMN,
        row_id=conversation.id,
    )


def forget_summary(session: Session, conversation_id: uuid.UUID) -> None:
    repo.update_conversation(
        session,
        conversation_id,
        {
            "summary_ciphertext": None,
            "summary_oldest_at": None,
            "summary_through": None,
            "summary_sources": [],
        },
    )


# --- rolling summary (worker) ---------------------------------------------------------------------


def request_summary(
    session: Session,
    conversation: Conversation,
    user_id: uuid.UUID,
    visibility: SourceVisibility,
    llm: LlmConfig,
) -> bool:
    """Queue the summary job when current turns older than the recent window are not covered
    yet (in the caller's transaction, after the answer is stored). The payload names which
    earlier answers the caller could see at this moment; the job summarises only those."""
    rows = repo.completed_turns(session, conversation.id, user_id)
    keep = llm.conversation.max_earlier_questions
    older = rows[: max(0, len(rows) - keep)]
    covered = conversation.summary_through
    pending = [r for r in older if covered is None or r.created_at > covered][-MAX_PENDING:]
    if not pending:
        return False
    answers = [
        str(r.id)
        for r in pending
        if r.status == "answered" and visibility.all_visible(cited_sources_of(session, r))
    ]
    ops.enqueue_event(
        session,
        SUMMARY_EVENT,
        {
            "conversation_id": str(conversation.id),
            "user_id": str(user_id),
            "through": str(pending[-1].id),
            "answers": answers,
        },
    )
    return True


@dataclass(frozen=True, slots=True)
class _SummaryInput:
    previous: str | None
    previous_through: dt.datetime | None
    oldest: dt.datetime
    through: dt.datetime
    text: str
    sources: tuple[str, ...]


def _summary_input(
    session: Session, payload: Mapping[str, Any], cfg: ConversationsConfig
) -> _SummaryInput | None:
    conversation_id = uuid.UUID(str(payload["conversation_id"]))
    user_id = uuid.UUID(str(payload["user_id"]))
    allowed = {str(a) for a in payload.get("answers") or ()}
    conversation = repo.get_conversation(session, conversation_id, user_id)
    last = repo.query_by_id(session, uuid.UUID(str(payload["through"])))
    if conversation is None or last is None or last.conversation_id != conversation_id:
        return None
    through = last.created_at
    start = conversation.summary_through
    if start is not None and start >= through:
        return None
    rows = [
        r
        for r in repo.completed_turns(session, conversation_id, user_id)
        if (start is None or r.created_at > start) and r.created_at <= through
    ]
    if not rows:
        return None
    previous = unseal(
        session,
        conversation.summary_ciphertext,
        table=CONVERSATIONS_TABLE,
        column=SUMMARY_COLUMN,
        row_id=conversation.id,
    )
    parts: list[str] = []
    sources: set[str] = {str(s) for s in conversation.summary_sources or []}
    for row in rows:
        question = question_of(session, row)
        if question is None:
            continue
        parts.append(f"User asked: {question}")
        if str(row.id) in allowed:
            answer = answer_of(session, row)
            if answer:
                parts.append(f"Assistant answered: {strip_markers(answer)}")
                sources.update(cited_sources_of(session, row))
    head = f"Previous summary:\n{previous or '(none)'}\n\nOlder turns:\n"
    body = "\n".join(parts)
    room = max(0, cfg.summary.max_input_chars - len(head))
    return _SummaryInput(
        previous=previous,
        previous_through=start,
        oldest=min(conversation.summary_oldest_at or rows[0].created_at, rows[0].created_at),
        through=rows[-1].created_at,
        text=head + body[-room:],
        sources=tuple(sorted(sources)),
    )


def summarise(
    tenant_id: uuid.UUID,
    payload: Mapping[str, Any],
    gateway: LlmGateway,
    *,
    session_factory: Any,
) -> str:
    """Worker job ``knowledge.summarise_conversation``: read (one transaction), call the
    ``summary`` role (no transaction open), store encrypted (another transaction, only if the
    conversation still stands where it was read). Returns an outcome code."""
    cfg = config()
    conversation_id = uuid.UUID(str(payload["conversation_id"]))
    with session_factory(tenant_id) as session:
        found = _summary_input(session, payload, cfg)
    if found is None:
        return "nothing_to_do"
    prompt = load_prompt(cfg.summary.prompt.id, cfg.summary.prompt.version)
    try:
        raw = gateway.generate_json(
            Metering(tenant_id=tenant_id, feature="ask"),
            "summary",
            prompt.render(),
            found.text,
            SUMMARY_SCHEMA,
        )
    except GatewayMisuse:
        raise
    except GatewayError as exc:
        log.warning(
            "kb.conversation.summary_failed",
            tenant_id=tenant_id,
            resource_type="kb_conversation",
            resource_id=conversation_id,
            error_code=exc.code,
        )
        return exc.code
    text = cut(mask_numbers(tidy(sanitise(str(raw.get("summary", ""))))), cfg.summary.max_chars)
    if not text:
        return "empty"
    user_id = uuid.UUID(str(payload["user_id"]))
    with session_factory(tenant_id) as session:
        conversation = repo.get_conversation(session, conversation_id, user_id, for_update=True)
        if conversation is None or conversation.summary_through != found.previous_through:
            return "superseded"
        blob, _ = seal(
            session,
            text,
            table=CONVERSATIONS_TABLE,
            column=SUMMARY_COLUMN,
            row_id=conversation_id,
        )
        repo.update_conversation(
            session,
            conversation_id,
            {
                "summary_ciphertext": blob,
                "summary_oldest_at": found.oldest,
                "summary_through": found.through,
                "summary_sources": list(found.sources),
            },
        )
    log.info(
        "kb.conversation.summarised",
        tenant_id=tenant_id,
        resource_type="kb_conversation",
        resource_id=conversation_id,
        count=len(found.sources),
    )
    return "summarised"


# --- query rewrite ------------------------------------------------------------------------------


def rewrite_request(question: str, turns: Sequence[HistoryTurn], summary: str | None) -> str:
    lines = []
    if summary:
        lines += ["Conversation summary:", summary, ""]
    if turns:
        lines.append("Recent turns:")
        for turn in turns:
            lines.append(f"- User: {turn.question}")
            if turn.answer:
                lines.append(f"  Assistant: {turn.answer}")
        lines.append("")
    lines += ["Follow-up question:", question]
    return "\n".join(lines)


def rewrite(
    gateway: LlmGateway,
    metering: Metering,
    question: str,
    context: AskContext,
) -> str | None:
    """The standalone form of a follow-up, or None (no history, disabled, a failure, or a
    rewrite that is too long, holds an Aadhaar-like number or changes nothing)."""
    cfg = config().query_rewrite
    if not cfg.enabled or not context.has_history:
        return None
    prompt = load_prompt(cfg.prompt.id, cfg.prompt.version)
    try:
        raw = gateway.generate_json(
            metering,
            "query_rewrite",
            prompt.render(),
            rewrite_request(question, context.turns, context.summary),
            REWRITE_SCHEMA,
        )
    except GatewayMisuse:
        raise
    except GatewayError as exc:
        log.info(
            "kb.query.rewrite_skipped",
            tenant_id=metering.tenant_id,
            resource_type="kb_query",
            resource_id=metering.query_id,
            error_code=exc.code,
        )
        return None
    text = tidy(sanitise(str(raw.get("question", ""))))
    if not text or len(text) > cfg.max_chars or aadhaar_like(text):
        return None
    if tidy(question).casefold() == text.casefold():
        return None
    return text


# --- follow-up suggestions -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Suggestions:
    questions: tuple[str, ...] = ()
    memory: str | None = None


def followups_request(
    questions: Sequence[str], answer: str, *, memory_wanted: bool, answer_chars: int
) -> str:
    lines = ["Recent questions (oldest first):"]
    lines += [f"- {q}" for q in questions]
    lines += ["", "Answer the user has just read:", cut(strip_markers(answer), answer_chars), ""]
    lines.append(
        "Also suggest a memory item when rule 4 allows it."
        if memory_wanted
        else "Memory is off for this user: memory must be null."
    )
    return "\n".join(lines)


def _script_matches(text: str, language: Locale) -> bool:
    telugu = bool(_TELUGU.search(text))
    if language == "te":
        return telugu
    if language == "en":
        return not telugu
    return True


def _grounded(text: str, seen: str) -> bool:
    """Numbers and (Latin) proper names in a suggestion must already be in the conversation."""
    folded = seen.casefold()
    for token in _DIGITS.findall(text):
        if token.casefold().strip(".") not in folded:
            return False
    first = text.split(" ", 1)[0]
    names = [m.group(0) for m in _NAME.finditer(text) if m.start() > 0 or m.group(0) != first]
    return all(name.casefold() in folded for name in names)


def clean_followups(
    raw: Sequence[object], *, language: Locale, seen: str, current: str, limit: int, max_chars: int
) -> tuple[str, ...]:
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = tidy(sanitise(item))
        if (
            not text
            or len(text) > max_chars
            or aadhaar_like(text)
            or redact(text) != text
            or not _script_matches(text, language)
            or not _grounded(text, seen)
            or text.casefold() == tidy(current).casefold()
            or text.casefold() in {o.casefold() for o in out}
        ):
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return tuple(out)


def suggest(
    gateway: LlmGateway,
    metering: Metering,
    *,
    questions: Sequence[str],
    answer: str,
    language: Locale,
    memory_wanted: bool,
) -> Suggestions:
    """Follow-up questions (and a memory candidate, unchecked) for an answered question."""
    cfg = config().followups
    if cfg.max_questions == 0 and not memory_wanted:
        return Suggestions()
    prompt = load_prompt(cfg.prompt.id, cfg.prompt.version)
    try:
        raw = gateway.generate_json(
            metering,
            "followups",
            prompt.render(),
            followups_request(
                questions,
                answer,
                memory_wanted=memory_wanted,
                answer_chars=cfg.max_answer_chars,
            ),
            FOLLOWUPS_SCHEMA,
        )
    except GatewayMisuse:
        raise
    except GatewayError as exc:
        log.info(
            "kb.query.followups_skipped",
            tenant_id=metering.tenant_id,
            resource_type="kb_query",
            resource_id=metering.query_id,
            error_code=exc.code,
        )
        return Suggestions()
    seen = " ".join([*questions, answer])
    found = raw.get("questions")
    cleaned = clean_followups(
        found if isinstance(found, list) else [],
        language=language,
        seen=seen,
        current=questions[-1] if questions else "",
        limit=cfg.max_questions,
        max_chars=cfg.max_chars,
    )
    memory = raw.get("memory")
    return Suggestions(
        questions=cleaned,
        memory=tidy(memory)
        if memory_wanted and isinstance(memory, str) and memory.strip()
        else None,
    )


def question_language(question: str) -> Locale:
    return detect_language(question)


__all__ = [
    "ANSWER_COLUMN",
    "CITATIONS_COLUMN",
    "CONVERSATIONS_TABLE",
    "FOLLOWUPS_COLUMN",
    "FOLLOWUPS_SCHEMA",
    "QUERIES_TABLE",
    "QUESTION_COLUMN",
    "REWRITE_SCHEMA",
    "SCHEMA_TAGS",
    "SUMMARY_COLUMN",
    "SUMMARY_EVENT",
    "SUMMARY_SCHEMA",
    "SUMMARY_TASK",
    "TITLE_COLUMN",
    "StoredCitation",
    "Suggestions",
    "aadhaar_like",
    "answer_of",
    "build_context",
    "cited_sources_of",
    "clean_followups",
    "cut",
    "derive_title",
    "encode_citations",
    "forget_summary",
    "mask_numbers",
    "question_of",
    "request_summary",
    "rewrite",
    "seal",
    "stored_citations",
    "stored_followups",
    "strip_markers",
    "suggest",
    "summarise",
    "tidy",
    "title_of",
    "title_problem",
    "unseal",
]
