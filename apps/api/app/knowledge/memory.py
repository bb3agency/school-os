"""Per-user Ask memory (ADR-0034; docs/06 §5, docs/08 §4). Used by :mod:`app.knowledge.service`.

A memory item is one user's own preference or work context in one school ("prefers answers in
Telugu", "is the class teacher of IX-A"), never data about anyone else. Items are ciphertext
under the school's key (``kb.user_memories``); confirmed (``active``) items go into the prompt
as a stable system block right after the static system prompt, as context only: never evidence,
never cited, never able to widen what the user may see (retrieval stays filtered by the caller's
current permissions in SQL).

**Screen** (:func:`screen`), for every item before it is stored (explicit, edited or suggested):
1. local rules (no model): no Aadhaar-like number, phone number or email (``core.redaction``),
   no date, no run of 5+ digits, at most ``memory.max_chars``;
2. no name or value from the RECORDS the user retrieved in that conversation (titles and
   snippets of student, finding, change and fee sources the answers cited);
3. the ``memory_screen`` role through the gateway (metered, budget-checked) must answer
   ``self``; ``others`` or ``unsure`` refuse, and when the model cannot be asked (AI off, budget
   used up, outage) the item is NOT stored ("when in doubt, don't store").

**Switches**: the school's ``ai_memory_enabled`` setting (default on) and the user's own switch
(``kb.user_memory_settings``, no row = on). With either off nothing is stored, suggested or used;
existing items stay listed so the user can still delete them.

**Retention**: pending suggestions expire after ``memory.pending_ttl_hours`` (hidden at once,
deleted by the daily job); confirmed items stay until the user deletes them, forgets
everything, or their membership of the school ends (the daily job, and the membership FK), and
with the school's offboarding purge.

Nothing here logs item text: ids, codes and counts only (invariant 5).
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.redaction import redact
from app.knowledge import repository as repo
from app.knowledge import sources
from app.knowledge.config.conversations import MemoryConfig, load_conversations_config
from app.knowledge.conversations import (
    StoredCitation,
    aadhaar_like,
    seal,
    stored_citations,
    tidy,
    unseal,
)
from app.knowledge.domain import Metering
from app.knowledge.gateway.errors import GatewayError, GatewayMisuse
from app.knowledge.interfaces import LlmGateway
from app.knowledge.models import UserMemory
from app.knowledge.prompts.registry import load_prompt

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

TABLE: Final = "kb.user_memories"
TEXT_COLUMN: Final = "text_ciphertext"
SCHEMA_TAG: Final = "sos:ask.memory_screen.v1"
SCREEN_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "description": SCHEMA_TAG,
    "additionalProperties": False,
    "required": ["verdict"],
    "properties": {"verdict": {"type": "string", "enum": ["self", "others", "unsure"]}},
}
RECORD_KINDS: Final = frozenset({"student", "finding", "change", "fee"})

_DATE: Final = re.compile(
    r"\b\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}\b"
    r"|\b\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
    re.IGNORECASE,
)
_LONG_NUMBER: Final = re.compile(r"\d{5,}")
_WORD: Final = re.compile(r"[\w][\w+\-]*", re.UNICODE)
_COMMON: Final = frozenset(
    {
        "student",
        "record",
        "records",
        "source",
        "verified",
        "not",
        "the",
        "and",
        "class",
        "section",
        "date",
        "birth",
        "name",
        "father",
        "mother",
        "admission",
        "register",
        "school",
        "finding",
        "change",
        "request",
        "fee",
        "fees",
        "dues",
        "tally",
        "roll",
        "gender",
        "status",
        "current",
        "value",
        "recorded",
        "hidden",
        "sensitive",
        "field",
        "as",
        "of",
        "no",
    }
)

Verdict = Literal[
    "ok",
    "personal_number",
    "date",
    "long_number",
    "too_long",
    "seen_record",
    "others",
    "unsure",
    "unavailable",
    "empty",
]


def config() -> MemoryConfig:
    return load_conversations_config().memory


# --- "remember that ..." ------------------------------------------------------------------------


def remember_text(question: str) -> str | None:
    """The note of a "remember that ..." instruction, or None when the question is not one."""
    text = tidy(question)
    folded = text.casefold()
    for prefix in sorted(config().remember_prefixes, key=len, reverse=True):
        if folded.startswith(prefix):
            rest = text[len(prefix) :].lstrip(" :,-\u2013\u2014").strip()
            return rest or None
    return None


# --- screen ------------------------------------------------------------------------------------


def _record_terms(citations: Iterable[StoredCitation]) -> set[str]:
    """Names and values of RECORD sources the user was shown (never document wording)."""
    terms: set[str] = set()
    for c in citations:
        try:
            kind = sources.parse(c.source).kind
        except ValueError:
            continue
        if kind not in RECORD_KINDS:
            continue
        for text in (c.title or "", c.snippet or ""):
            for word in _WORD.findall(text):
                folded = word.casefold()
                if (
                    len(folded) >= 3 or any(ch.isdigit() for ch in folded)
                ) and folded not in _COMMON:
                    terms.add(folded)
    return terms


def seen_record_terms(
    session: Session, conversation_id: uuid.UUID | None, user_id: uuid.UUID
) -> set[str]:
    """Record names and values cited in the user's conversation (the "seen" screen)."""
    if conversation_id is None:
        return set()
    terms: set[str] = set()
    for row in repo.conversation_messages(session, conversation_id, user_id):
        terms |= _record_terms(stored_citations(session, row))
    return terms


def local_screen(text: str, seen: set[str]) -> Verdict:  # noqa: PLR0911 - one code per rule
    cfg = config()
    if not text:
        return "empty"
    if len(text) > cfg.max_chars:
        return "too_long"
    if aadhaar_like(text) or redact(text) != text:
        return "personal_number"
    if _DATE.search(text):
        return "date"
    if _LONG_NUMBER.search(text):
        return "long_number"
    words = {w.casefold() for w in _WORD.findall(text)}
    if words & seen:
        return "seen_record"
    return "ok"


def model_screen(gateway: LlmGateway, metering: Metering, text: str) -> Verdict:
    """The ``memory_screen`` role's verdict; ``unavailable`` when it cannot be asked."""
    cfg = config()
    prompt = load_prompt(cfg.screen_prompt.id, cfg.screen_prompt.version)
    try:
        raw = gateway.generate_json(metering, "memory_screen", prompt.render(), text, SCREEN_SCHEMA)
    except GatewayMisuse:
        raise
    except GatewayError as exc:
        log.info("kb.memory.screen_unavailable", tenant_id=metering.tenant_id, error_code=exc.code)
        return "unavailable"
    verdict = raw.get("verdict")
    if verdict == "self":
        return "ok"
    return "others" if verdict == "others" else "unsure"


def screen(gateway: LlmGateway, metering: Metering, text: str, seen: set[str]) -> Verdict:
    """``ok`` or why the item must not be stored (local rules first, then the model)."""
    verdict = local_screen(text, seen)
    if verdict != "ok":
        return verdict
    return model_screen(gateway, metering, text)


# --- items ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Item:
    row: UserMemory
    text: str


def text_of(session: Session, row: UserMemory) -> str:
    return (
        unseal(session, row.text_ciphertext, table=TABLE, column=TEXT_COLUMN, row_id=row.id) or ""
    )


def items(session: Session, user_id: uuid.UUID, now: dt.datetime) -> list[Item]:
    return [Item(r, text_of(session, r)) for r in repo.list_memories(session, user_id, now)]


def prompt_items(session: Session, user_id: uuid.UUID) -> tuple[str, ...]:
    """Confirmed items, oldest first, capped (the prompt's memory block)."""
    rows = repo.active_memories(session, user_id)[: config().max_items]
    return tuple(t for r in rows if (t := text_of(session, r)))


def insert(
    session: Session,
    *,
    user_id: uuid.UUID,
    text: str,
    source: Literal["explicit", "suggested"],
    status: Literal["active", "pending"],
    now: dt.datetime,
    conversation_id: uuid.UUID | None = None,
    query_id: uuid.UUID | None = None,
) -> UserMemory:
    item_id = new_id()
    blob, version = seal(session, text, table=TABLE, column=TEXT_COLUMN, row_id=item_id)
    expires = now + dt.timedelta(hours=config().pending_ttl_hours) if status == "pending" else None
    return repo.insert_memory(
        session,
        {
            "id": item_id,
            "user_id": user_id,
            "text_ciphertext": blob,
            "key_version": version,
            "source": source,
            "status": status,
            "conversation_id": conversation_id,
            "query_id": query_id,
            "expires_at": expires,
            "created_at": now,
            "updated_at": now,
        },
    )


def duplicate(existing: Sequence[Item], text: str) -> Item | None:
    folded = tidy(text).casefold()
    return next((i for i in existing if tidy(i.text).casefold() == folded), None)


__all__ = [
    "SCHEMA_TAG",
    "SCREEN_SCHEMA",
    "TABLE",
    "TEXT_COLUMN",
    "Item",
    "Verdict",
    "config",
    "duplicate",
    "insert",
    "items",
    "local_screen",
    "model_screen",
    "prompt_items",
    "remember_text",
    "screen",
    "seen_record_terms",
    "text_of",
]
