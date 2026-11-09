"""Which model role answers a question: the answer role, or the light role for a question one
tool answers (FR-KB-049, FR-KB-050; docs/06 §5, FA-02).

Pure and deterministic: no model call decides the route (a classifier call would cost what the
route saves). ``models.yaml`` ``routing`` holds every phrase and limit (invariant 13); it is OFF
until the live evaluation shows the light role passes every hard gate on routed questions
(docs/06 §13.7). Routing changes only which model writes the answer: retrieval, tool
permissions, citation validation and the "not found" rule are the same for both roles
(invariant 8), and a routed question is still a billable answer like any other (ADR-0038).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

from app.knowledge.config.llm import AnswerRouting
from app.knowledge.domain import ModelRole

ANSWER_ROLE: Final[ModelRole] = "answer"
_RIGHT_QUOTE: Final = "\u2019"
_TELUGU: Final = re.compile("[\u0c00-\u0c7f]")
_SPACE: Final = re.compile(r"\s+")

RouteReason = Literal[
    "disabled",
    "has_history",
    "too_long",
    "telugu_script",
    "no_intent",
    "several_intents",
    "single_tool",
]


@dataclass(frozen=True, slots=True)
class RouteDecision:
    role: ModelRole
    reason: RouteReason
    tool: str | None = None
    """The one tool whose phrases matched (``single_tool``), else None."""
    max_tool_rounds: int | None = None
    """The routed question's tool-round cap; None keeps ``limits.max_tool_rounds``."""


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold().replace(_RIGHT_QUOTE, "'")
    return _SPACE.sub(" ", text).strip()


def _matches(question: str, phrase: str) -> bool:
    return re.search(rf"(?<![\w']){re.escape(phrase)}(?![\w'])", question) is not None


def _matched_tools(routing: AnswerRouting, text: str) -> list[str]:
    return [
        tool
        for tool, phrases in routing.single_tool_intents.items()
        if any(_matches(text, phrase) for phrase in phrases)
    ]


def choose_answer_role(
    routing: AnswerRouting, question: str, *, has_history: bool
) -> RouteDecision:
    """The role for ``question``. Anything not clearly a single-tool question keeps the answer
    role, so a wrong guess can only cost money, never route a hard question to the light
    model."""
    text = _normalise(question)
    tools = _matched_tools(routing, text)
    reason: RouteReason
    if not routing.enabled:
        reason = "disabled"
    elif has_history and not routing.route_with_history:
        reason = "has_history"
    elif len(text) > routing.max_question_chars:
        reason = "too_long"
    elif _TELUGU.search(text):
        reason = "telugu_script"
    elif not tools:
        reason = "no_intent"
    elif len(tools) > 1:
        reason = "several_intents"
    else:
        return RouteDecision(
            routing.light_role,
            "single_tool",
            tool=tools[0],
            max_tool_rounds=routing.max_tool_rounds,
        )
    return RouteDecision(ANSWER_ROLE, reason)


__all__ = ["RouteDecision", "RouteReason", "choose_answer_role"]
