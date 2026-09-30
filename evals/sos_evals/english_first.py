"""English first (ADR-0036; product owner 2026-09-30): the default run with Telugu hidden.

The main run measures every gate with the Telugu switch ON (``SOS_TELUGU_ENABLED=true``), so the
dormant Telugu capability (answers, follow-ups, summaries and notices in Telugu, FR-KB-006) keeps
its datasets and gates and cannot rot. This pass then switches Telugu OFF (the product default)
and asks again what a Telugu or code-mixed user would ask, reads the Telugu and code-mixed
circulars, drafts a notice from each and replays the Telugu conversations. It collects every
text the system itself wrote and a person would see:

- Ask: the answer's prose (not the cited passages: those are the school's own words, shown as
  sources), the ``language`` it reports, the follow-up suggestions and the conversation title;
- circular reading: the summaries, the suggested issuer, reference and subject, and each
  deadline's title and details (not the quote: it is copied from the circular as evidence);
- notice drafts: all four fields (the Telugu ones must be empty);
- conversations: each step's reply, follow-ups, memory items and the title.

Scoring (hard gates in ``gates.toml``):

- **english_first_telugu_outputs** (== 0): texts containing Telugu script (U+0C00-U+0C7F), plus a
  reported ``language`` other than ``en``, plus probes that showed nothing at all (an adapter
  that returns nothing proves nothing: missing evidence is not a pass);
- **english_first_english_answer_rate** (>= 1.0): Ask answers to Telugu and code-mixed questions
  that are English (Latin letters, no Telugu script), including "not found".

Pure scoring; the adapter does the work. The adapter is switched back ON afterwards.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from sos_evals.adapters import AskAdapter, AskResult
from sos_evals.circulars import CircularAdapter, CircularCase, CircularResult
from sos_evals.conversations import ConversationAdapter, ConversationCase, ConversationRun
from sos_evals.schema import EvalItem

TELUGU = re.compile(r"[ఀ-౿]")
LATIN = re.compile(r"[A-Za-z]")
Kind = Literal["ask", "circular", "notice", "conversation"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoticeResult(_Model):
    """A parent-notice draft made from a circular (FR-NOTICE-003)."""

    title_en: str = ""
    body_en: str = ""
    title_te: str = ""
    body_te: str = ""
    failed: bool = False


class EnglishFirstAdapter(AskAdapter, CircularAdapter, ConversationAdapter, Protocol):
    """An adapter whose Telugu switch the harness can set (``SOS_TELUGU_ENABLED``)."""

    def set_telugu(self, enabled: bool) -> None: ...

    def draft_notice(self, case: CircularCase) -> NoticeResult: ...


class EnglishFirstOutcome(_Model):
    id: str
    kind: Kind
    fields: int
    telugu_fields: tuple[str, ...]
    """Names of shown fields with Telugu script (or a non-English language, or ``nothing``)."""
    english_answer: bool | None = None
    """Ask probes only: the answer is English."""


class EnglishFirstMetrics(_Model):
    english_first_items: int = 0
    english_first_fields: int = 0
    english_first_telugu_outputs: int | None = None
    english_first_english_answer_rate: float | None = None


Shown = list[tuple[str, str]]


def has_telugu(text: str) -> bool:
    return bool(TELUGU.search(text))


def is_english(text: str) -> bool:
    return bool(LATIN.search(text)) and not has_telugu(text)


def _outcome(case_id: str, kind: Kind, shown: Shown) -> EnglishFirstOutcome:
    bad = [name for name, text in shown if has_telugu(text)]
    bad += [name for name, text in shown if name == "language" and text != "en"]
    if not any(text for _, text in shown):
        bad.append("nothing")
    return EnglishFirstOutcome(
        id=case_id, kind=kind, fields=len(shown), telugu_fields=tuple(dict.fromkeys(bad))
    )


def ask_shown(result: AskResult) -> Shown:
    prose = result.text if result.mode == "full" else ""
    shown: Shown = [("answer", prose), ("language", result.language or "missing")]
    shown += [(f"followup[{i}]", q) for i, q in enumerate(result.followups)]
    if result.title is not None:
        shown.append(("title", result.title))
    return shown


def circular_shown(result: CircularResult) -> Shown:
    shown: Shown = [
        (name, value)
        for name, value in (
            ("summary_en", result.summary_en),
            ("summary_te", result.summary_te),
            ("issuer", result.issuer),
            ("reference_no", result.reference_no),
            ("subject", result.subject),
        )
        if value
    ]
    for i, d in enumerate(result.deadlines):
        shown.append((f"deadline[{i}].title", d.title))
        if d.details:
            shown.append((f"deadline[{i}].details", d.details))
    return shown


def notice_shown(result: NoticeResult) -> Shown:
    return [
        ("title_en", result.title_en),
        ("body_en", result.body_en),
        ("title_te", result.title_te),
        ("body_te", result.body_te),
    ]


def conversation_shown(run: ConversationRun) -> Shown:
    shown: Shown = []
    for turn in run.turns:
        shown.append((f"step[{turn.step}].text", turn.text))
        shown += [(f"step[{turn.step}].followup", q) for q in turn.followups]
        shown += [(f"step[{turn.step}].memory", text) for _, text in turn.memory]
        if turn.title is not None:
            shown.append((f"step[{turn.step}].title", turn.title))
        if turn.language is not None:
            shown.append(("language", turn.language))
    return shown


def telugu_items(items: Iterable[EvalItem]) -> list[EvalItem]:
    return [i for i in items if i.locale != "en"]


def telugu_circulars(cases: Iterable[CircularCase]) -> list[CircularCase]:
    return [c for c in cases if c.locale != "en"]


def telugu_conversations(cases: Iterable[ConversationCase]) -> list[ConversationCase]:
    """Conversations in Telugu or code-mixed, or whose user asks for Telugu answers."""
    return [
        c
        for c in cases
        if c.locale != "en"
        or any(s.expect_script == "te" or has_telugu(s.question or "") for s in c.steps)
    ]


def aggregate(outcomes: Sequence[EnglishFirstOutcome]) -> EnglishFirstMetrics:
    if not outcomes:
        return EnglishFirstMetrics()
    answers = [o.english_answer for o in outcomes if o.english_answer is not None]
    return EnglishFirstMetrics(
        english_first_items=len(outcomes),
        english_first_fields=sum(o.fields for o in outcomes),
        english_first_telugu_outputs=sum(len(o.telugu_fields) for o in outcomes),
        english_first_english_answer_rate=sum(answers) / len(answers) if answers else None,
    )


def run(
    adapter: EnglishFirstAdapter,
    *,
    items: Sequence[EvalItem] = (),
    circular_cases: Sequence[CircularCase] = (),
    conversation_cases: Sequence[ConversationCase] = (),
) -> tuple[EnglishFirstMetrics, tuple[EnglishFirstOutcome, ...]]:
    """Switch Telugu off, probe, switch it back on."""
    outcomes: list[EnglishFirstOutcome] = []
    adapter.set_telugu(False)
    try:
        for item in telugu_items(items):
            result = adapter.ask(item.question, item.asker)
            outcome = _outcome(item.id, "ask", ask_shown(result))
            english = result.mode == "full" and is_english(result.text)
            outcomes.append(outcome.model_copy(update={"english_answer": english}))
        for case in telugu_circulars(circular_cases):
            reading = adapter.read_circular(case)
            outcomes.append(_outcome(case.id, "circular", circular_shown(reading)))
            notice = adapter.draft_notice(case)
            outcomes.append(_outcome(case.id, "notice", notice_shown(notice)))
        for conv in telugu_conversations(conversation_cases):
            talk = adapter.run_conversation(conv)
            outcomes.append(_outcome(conv.id, "conversation", conversation_shown(talk)))
    finally:
        adapter.set_telugu(True)
    return aggregate(outcomes), tuple(outcomes)


__all__ = [
    "EnglishFirstAdapter",
    "EnglishFirstMetrics",
    "EnglishFirstOutcome",
    "NoticeResult",
    "aggregate",
    "ask_shown",
    "circular_shown",
    "conversation_shown",
    "has_telugu",
    "is_english",
    "notice_shown",
    "run",
    "telugu_circulars",
    "telugu_conversations",
    "telugu_items",
]
