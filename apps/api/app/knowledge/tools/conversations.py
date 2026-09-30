"""``search_my_conversations``: the caller's own earlier Ask conversations (ADR-0034; docs/06 §7).

Read-only and scoped to ONE person in ONE school: the caller's own conversations that are not
deleted, in the school of the request (RLS), the newest ``max_conversations`` by activity. Their
titles, questions and answers are decrypted in memory only and scored by shared words (casefold,
Telugu script kept) plus character-trigram overlap with the question; no plaintext index is ever
stored. An earlier answer is included only when every source it cited is still visible to the
caller NOW (invariant 8); otherwise only the question is returned. Results are ``search_result``
blocks with ``sos://conversation/{id}#q{query_id}`` sources, validated like any other citation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.knowledge import repository as repo
from app.knowledge import sources
from app.knowledge.config.tools import ToolConfig
from app.knowledge.domain import SearchResultBlock, ToolOutcome, ToolSpec
from app.knowledge.sealed import (
    answer_of,
    cited_sources_of,
    question_of,
    strip_markers,
    title_of,
)
from app.knowledge.visibility import SourceVisibility

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext

NAME: Final = "search_my_conversations"
IST: Final = ZoneInfo("Asia/Kolkata")
ANSWER_CHARS: Final = 500
_WORD: Final = re.compile(r"\w+", re.UNICODE)
_STOP: Final = frozenset(
    {
        "a",
        "an",
        "the",
        "i",
        "me",
        "my",
        "we",
        "you",
        "what",
        "when",
        "where",
        "which",
        "who",
        "how",
        "did",
        "do",
        "does",
        "is",
        "are",
        "was",
        "were",
        "ask",
        "asked",
        "about",
        "last",
        "week",
        "month",
        "earlier",
        "before",
        "previous",
        "chat",
        "chats",
        "question",
        "questions",
        "of",
        "on",
        "in",
        "for",
        "to",
        "and",
        "or",
        "it",
    }
)


class ChatSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=300)


def _terms(text: str) -> set[str]:
    return {w for w in (m.casefold() for m in _WORD.findall(text)) if w not in _STOP and len(w) > 1}


def _trigrams(text: str) -> set[str]:
    folded = " ".join(text.casefold().split())
    return {folded[i : i + 3] for i in range(max(0, len(folded) - 2))}


def score(query: str, text: str) -> float:
    """Shared words first, trigram overlap as a tie-break; 0 without a shared word."""
    wanted = _terms(query)
    if not wanted:
        return 0.0
    shared = wanted & _terms(text)
    if not shared:
        return 0.0
    grams_q, grams_t = _trigrams(query), _trigrams(text)
    overlap = len(grams_q & grams_t) / len(grams_q | grams_t) if grams_q and grams_t else 0.0
    return len(shared) / len(wanted) + 0.5 * overlap


class SearchMyConversationsTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``search_my_conversations``."""

    def __init__(self, config: ToolConfig) -> None:
        if config.description is None:
            raise ValueError("search_my_conversations needs a description in tools.yaml")
        self._max = config.max_results or 5
        self._conversations = config.max_conversations or 30
        self._spec = ToolSpec(
            name=NAME,
            description=config.description,
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "maxLength": 300}},
                "required": ["query"],
                "additionalProperties": False,
            },
            permission=config.permission,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def allowed(self, ctx: UserContext) -> bool:
        return ctx.has(self._spec.permission)

    def run(
        self, session: Session, ctx: UserContext, call_id: str, arguments: Mapping[str, object]
    ) -> ToolOutcome:
        error = ToolOutcome(call_id=call_id, blocks=(), is_error=True)
        if not self.allowed(ctx):
            return error
        try:
            args = ChatSearchArgs.model_validate(dict(arguments))
        except ValidationError:
            return error
        conversations = {
            c.id: c for c in repo.recent_conversations(session, ctx.user_id, self._conversations)
        }
        titles = {cid: title_of(session, c) for cid, c in conversations.items()}
        visibility = SourceVisibility(session, ctx)
        found: list[tuple[float, SearchResultBlock]] = []
        for row in repo.thread_messages(session, list(conversations), ctx.user_id):
            if row.status not in repo.EARLIER_STATUSES or row.conversation_id is None:
                continue
            question = question_of(session, row)
            if question is None:
                continue
            title = titles.get(row.conversation_id, "")
            answer = None
            if row.status == "answered" and visibility.all_visible(cited_sources_of(session, row)):
                text = answer_of(session, row)
                answer = strip_markers(text)[:ANSWER_CHARS] if text else None
            value = score(args.query, f"{title} {question} {answer or ''}")
            if value <= 0:
                continue
            when = row.created_at.astimezone(IST).strftime("%d/%m/%Y")
            body = f"You asked: {question}"
            if answer:
                body += f"\nAnswer you were given then: {answer}"
            found.append(
                (
                    value,
                    SearchResultBlock(
                        source=sources.conversation_question(row.conversation_id, row.id),
                        title=f"Your earlier question · {title} · {when}",
                        text=body,
                    ),
                )
            )
        found.sort(key=lambda item: item[0], reverse=True)
        return ToolOutcome(call_id=call_id, blocks=tuple(b for _, b in found[: self._max]))


__all__ = ["NAME", "SearchMyConversationsTool", "score"]
