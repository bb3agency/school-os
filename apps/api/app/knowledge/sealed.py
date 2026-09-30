"""Sealed (encrypted) Ask texts: one place that reads and writes them (docs/05 §9, ADR-0034).

Every Ask text a person or the model wrote is AES-256-GCM ciphertext under the school's data key
with associated data ``tenant|<table>|<column>|<row id>`` (``app.students.crypto``): questions,
answers, numbered citations (title and snippet quote records and documents) and follow-ups in
``kb.queries``; titles and rolling summaries in ``kb.conversations``; memory items in
``kb.user_memories``. This module seals and unseals them and decodes the stored citation and
follow-up lists. It imports no gateway and no service, so the read-only chat-search tool can use
it. A value that cannot be decrypted is logged by id and column only and read as missing.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from app.core.logging import get_logger
from app.knowledge.models import Conversation, Query
from app.students import crypto

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class CitedLike(Protocol):
    """A numbered citation (``answer.CitedSource``; not imported: this module stays below the
    answer loop and the gateway)."""

    @property
    def index(self) -> int: ...
    @property
    def source(self) -> str: ...
    @property
    def title(self) -> str: ...
    @property
    def snippet(self) -> str: ...


log = get_logger(__name__)

CONVERSATIONS_TABLE: Final = "kb.conversations"
TITLE_COLUMN: Final = "title_ciphertext"
SUMMARY_COLUMN: Final = "summary_ciphertext"
QUERIES_TABLE: Final = "kb.queries"
QUESTION_COLUMN: Final = "question_ciphertext"
ANSWER_COLUMN: Final = "answer_ciphertext"
CITATIONS_COLUMN: Final = "citations_ciphertext"
FOLLOWUPS_COLUMN: Final = "followups_ciphertext"

_MARKER: Final = re.compile(r"\s*\[\d+\]")


def strip_markers(text: str) -> str:
    return _MARKER.sub("", text).strip()


def seal(
    session: Session, text: str, *, table: str, column: str, row_id: uuid.UUID
) -> tuple[bytes, int]:
    return crypto.encrypt_value(session, text, table=table, column=column, row_id=row_id)


def unseal(
    session: Session, blob: bytes | None, *, table: str, column: str, row_id: uuid.UUID
) -> str | None:
    """Decrypted text, or None when there is none or it cannot be read (logged by id only)."""
    if blob is None:
        return None
    try:
        return crypto.decrypt_value(session, bytes(blob), table=table, column=column, row_id=row_id)
    except crypto.CryptoError:
        log.warning(
            "kb.conversation.unreadable",
            resource_type="kb_query" if table == QUERIES_TABLE else "kb_conversation",
            resource_id=row_id,
            error_code=column,
        )
        return None


@dataclass(frozen=True, slots=True)
class StoredCitation:
    index: int
    source: str
    title: str | None
    snippet: str | None


def encode_citations(cited: Sequence[CitedLike]) -> str:
    return json.dumps(
        [
            {"index": c.index, "source": c.source, "title": c.title, "snippet": c.snippet}
            for c in cited
        ],
        ensure_ascii=False,
    )


def stored_citations(session: Session, row: Query) -> list[StoredCitation]:
    """A question's numbered citations: the encrypted details (0038), else (older rows) the
    cited sources only, numbered in their stored order, without title or snippet."""
    raw = unseal(
        session,
        row.citations_ciphertext,
        table=QUERIES_TABLE,
        column=CITATIONS_COLUMN,
        row_id=row.id,
    )
    if raw is not None:
        try:
            items = json.loads(raw)
            return [
                StoredCitation(int(i["index"]), str(i["source"]), i.get("title"), i.get("snippet"))
                for i in items
            ]
        except (ValueError, KeyError, TypeError):
            return []
    return [
        StoredCitation(n, str(c.get("source", "")), None, None)
        for n, c in enumerate(row.citations or [], start=1)
    ]


def stored_followups(session: Session, row: Query) -> tuple[str, ...]:
    raw = unseal(
        session,
        row.followups_ciphertext,
        table=QUERIES_TABLE,
        column=FOLLOWUPS_COLUMN,
        row_id=row.id,
    )
    if not raw:
        return ()
    try:
        values = json.loads(raw)
    except ValueError:
        return ()
    return tuple(str(v) for v in values if isinstance(v, str))


def question_of(session: Session, row: Query) -> str | None:
    return unseal(
        session, row.question_ciphertext, table=QUERIES_TABLE, column=QUESTION_COLUMN, row_id=row.id
    )


def answer_of(session: Session, row: Query) -> str | None:
    return unseal(
        session, row.answer_ciphertext, table=QUERIES_TABLE, column=ANSWER_COLUMN, row_id=row.id
    )


def title_of(session: Session, conversation: Conversation) -> str:
    return (
        unseal(
            session,
            conversation.title_ciphertext,
            table=CONVERSATIONS_TABLE,
            column=TITLE_COLUMN,
            row_id=conversation.id,
        )
        or ""
    )


def cited_sources_of(session: Session, row: Query) -> list[str]:
    return [c.source for c in stored_citations(session, row)]


__all__ = [
    "ANSWER_COLUMN",
    "CITATIONS_COLUMN",
    "CONVERSATIONS_TABLE",
    "FOLLOWUPS_COLUMN",
    "QUERIES_TABLE",
    "QUESTION_COLUMN",
    "SUMMARY_COLUMN",
    "TITLE_COLUMN",
    "StoredCitation",
    "answer_of",
    "cited_sources_of",
    "encode_citations",
    "question_of",
    "seal",
    "stored_citations",
    "stored_followups",
    "strip_markers",
    "title_of",
    "unseal",
]
