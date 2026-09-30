"""The offline provider's answers for the Ask conversation roles (dev and CI only; ADR-0033).

Deterministic stand-ins, so the product's plumbing (redaction, metering, validation, storage,
events) runs end to end without a model. They are rules, not quality: the real behaviour is the
prompt's (``prompts/*.v1.txt``), measured by ``make eval``.

- ``query_rewrite``: the follow-up plus the content words of the latest earlier question (or the
  summary) it does not already contain, so offline retrieval sees what "it" refers to.
- ``summary``: "Earlier the user asked about: ..." listing the earlier questions.
- ``followups``: up to two generic follow-ups in the latest question's script (no names, no
  numbers); a memory candidate only when the latest question says "I prefer ..." or
  "I am the ..." about the user.
- ``memory_screen``: ``others`` for anything about students, parents, marks, health or with a
  digit; ``self`` for preferences and the user's own role; else ``unsure``.

Pure; the transport calls :func:`conversation_reply` for a known schema tag.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

_TELUGU: Final = re.compile(r"[ఀ-౿]")
_WORD: Final = re.compile(r"[\w']+", re.UNICODE)
_STOP: Final = frozenset(
    {
        "what",
        "when",
        "where",
        "which",
        "who",
        "how",
        "does",
        "is",
        "are",
        "the",
        "a",
        "an",
        "it",
        "its",
        "that",
        "this",
        "there",
        "about",
        "and",
        "for",
        "of",
        "on",
        "in",
        "to",
        "do",
        "did",
        "was",
        "be",
        "held",
    }
)
_OTHERS: Final = (
    "student",
    "parent",
    "guardian",
    "father",
    "mother",
    "marks",
    "health",
    "born",
    "birth",
    "phone",
    "address",
    "aadhaar",
    "fee",
    "attendance",
)
_SELF: Final = ("prefer", "i am", "i handle", "keep answers", "answers", "my ", "i teach", "telugu")


def _content(text: str) -> list[str]:
    return [w for w in _WORD.findall(text) if len(w) > 2 and w.casefold() not in _STOP]


def _section(text: str, header: str) -> list[str]:
    if header not in text:
        return []
    body = text.split(header, 1)[1]
    lines = []
    for line in body.splitlines()[1:]:
        if not line.strip():
            break
        lines.append(line.strip())
    return lines


def rewrite(text: str) -> dict[str, Any]:
    question = text.rsplit("Follow-up question:", 1)[-1].strip()
    earlier = [
        line.removeprefix("- User:").strip()
        for line in _section(text, "Recent turns:")
        if line.startswith("- User:")
    ]
    source = earlier[-1] if earlier else " ".join(_section(text, "Conversation summary:"))
    have = {w.casefold() for w in _WORD.findall(question)}
    extra = [w for w in _content(source) if w.casefold() not in have][:6]
    return {"question": f"{question} {' '.join(extra)}".strip() if extra else question}


def summary(text: str) -> dict[str, Any]:
    asked = [
        line.split("User asked:", 1)[1].strip()
        for line in text.splitlines()
        if "User asked:" in line
    ]
    previous = text.split("Previous summary:", 1)[-1].split("Older turns:", 1)[0].strip()
    parts = [previous] if previous and previous != "(none)" else []
    if asked:
        parts.append("Earlier the user asked about: " + "; ".join(asked))
    return {"summary": " ".join(parts)}


def followups(text: str) -> dict[str, Any]:
    questions = [line[2:] for line in _section(text, "Recent questions (oldest first):")]
    latest = questions[-1] if questions else ""
    if _TELUGU.search(latest):
        out = ["దీని గురించి మరిన్ని వివరాలు ఉన్నాయా?", "ఇది ఏ పత్రంలో ఉంది?"]
    else:
        out = ["Are there more details about this?", "Which document says this?"]
    memory = None
    if "memory must be null" not in text:
        folded = latest.casefold()
        for marker, lead in (("i prefer ", "Prefers "), ("i am the ", "Is the ")):
            if marker in folded:
                rest = latest[folded.index(marker) + len(marker) :].strip(" .?!")
                memory = f"{lead}{rest}" if rest else None
                break
    return {"questions": out, "memory": memory}


def memory_screen(text: str) -> dict[str, Any]:
    folded = text.casefold()
    if any(ch.isdigit() for ch in folded) or any(w in folded for w in _OTHERS):
        return {"verdict": "others"}
    if any(w in folded for w in _SELF):
        return {"verdict": "self"}
    return {"verdict": "unsure"}


_REPLIES: Final = {
    "sos:ask.query_rewrite.v1": rewrite,
    "sos:ask.conversation_summary.v1": summary,
    "sos:ask.followups.v1": followups,
    "sos:ask.memory_screen.v1": memory_screen,
}


def conversation_reply(schema: Mapping[str, Any], request_text: str) -> dict[str, Any] | None:
    """The stand-in's JSON for an Ask conversation schema, or None for any other schema."""
    reply = _REPLIES.get(str(schema.get("description")))
    return reply(request_text) if reply is not None else None


__all__ = ["conversation_reply"]
