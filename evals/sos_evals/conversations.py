"""Ask conversation evaluation (ADR-0034; docs/06 §13.5; FR-KB-012, FR-KB-010, invariant 8).

A fourth, small set of scripted conversations. Each case is its own tiny school: a few
documents with a unique ``marker`` each and an audience, a few people (role, optional section),
and ordered steps: ``ask`` (optionally in a named conversation), ``regenerate`` / ``edit`` (of
an earlier step), ``remember`` ("remember that ..."), ``memory_off``, ``revoke`` (a document
becomes visible to the owner only), ``revise`` (a document gets a new version) and
``delete_conversation``. The system under test runs the steps and reports, per step, what the
model was sent (``model_input``), the answer, its citations (document keys, or ``chat:<step>``
for one of the asker's own earlier questions), follow-ups, memory events, whether the answer was
reused from the cache (and from which step), and the asker's stored memory items afterwards.

The harness keeps its own state (who may see what after every revoke, which questions were
superseded, whose memory is on) and judges every step against it, never against the system:

- **leakage** (hard = 0): a step whose model input, answer, follow-ups, memory events or
  citations hold the marker of a document the asker may not see at that step, another person's
  question or memory item, or a chat-search hit that is another person's or from a deleted
  conversation.
- **scope violations** (hard = 0): a superseded (edited-away) question sent again, a memory item
  sent while the asker's memory is off, a memory item about other people stored, or a cached
  answer reused when the rules forbid it (another access, a document revised since, a question
  with history or with memory in use, a regenerate).
- **context accuracy** (soft): steps that name ``expect_sources`` cite all of them (a follow-up
  understood through the conversation).
- **follow-up language** (soft): follow-ups written in the step's language.
- **memory preference applied** (soft): steps that name ``expect_script`` answered in it.

Pure; no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from sos_evals.schema import Locale

Audience = Literal["all", "owner_only", "section_9a", "member_u1"]
Action = Literal[
    "ask",
    "regenerate",
    "edit",
    "remember",
    "memory_off",
    "revoke",
    "revise",
    "delete_conversation",
]
PersonRole = Literal["office_staff", "principal", "class_teacher"]
Category = Literal[
    "context", "long", "permission", "revision", "followups", "memory", "cache", "chats"
]

_TELUGU: Final = re.compile(r"[ఀ-౿]")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConvDoc(_Model):
    key: str = Field(pattern=r"^D[0-9]$")
    title: str = Field(min_length=3, max_length=80)
    content: str = Field(min_length=10, max_length=400)
    marker: str = Field(pattern=r"^[A-Z]{3,}-[0-9]{3}$")
    audience: Audience = "all"
    revised: str | None = None
    """The content of the new version a ``revise`` step stores (holds ``marker`` too)."""


class ConvPerson(_Model):
    key: str = Field(pattern=r"^U[0-9]$")
    role: PersonRole = "office_staff"
    section: Literal["9A"] | None = None


class ConvStep(_Model):
    action: Action
    who: str = "U1"
    conversation: str = "C1"
    question: str | None = None
    target: int | None = None
    """Index of an earlier ``ask`` step (regenerate, edit)."""
    doc: str | None = None
    """Document key (revoke, revise)."""
    expect_sources: tuple[str, ...] = ()
    expect_script: Literal["te", "latin"] | None = None
    expect_memory: Literal["saved", "suggested", "refused"] | None = None
    expect_cached: bool | None = None
    """Informational: whether a reuse is allowed here (the harness decides violations)."""
    forbidden_memory: str | None = None
    """A phrase about another person that must never be stored as a memory item."""


class ConversationCase(_Model):
    id: str = Field(pattern=r"^conv-[a-z0-9-]{2,60}$")
    category: Category
    locale: Locale
    docs: tuple[ConvDoc, ...] = Field(min_length=1)
    people: tuple[ConvPerson, ...] = Field(min_length=1)
    steps: tuple[ConvStep, ...] = Field(min_length=1)
    note: str = ""


class TurnResult(_Model):
    step: int
    text: str = ""
    citations: tuple[str, ...] = ()
    """Document keys, or ``chat:<step>`` for the asker's own earlier question."""
    provided: tuple[str, ...] = ()
    model_input: str = ""
    """Everything the model was sent for this step (all calls), as text."""
    followups: tuple[str, ...] = ()
    memory: tuple[tuple[str, str], ...] = ()
    """(action, text) of each ``memory`` event."""
    cached_from: int | None = None
    """The step whose answer was reused (exact-repeat cache), else None."""
    memories_after: tuple[str, ...] = ()
    """The asker's stored memory items after the step."""
    title: str | None = None
    """The conversation title shown with the step (English-first pass, ADR-0036)."""
    language: str | None = None
    """The answer's language the system reported (SSE ``meta.language``)."""


class ConversationRun(_Model):
    turns: tuple[TurnResult, ...]


class ConversationAdapter(Protocol):
    name: str

    def run_conversation(self, case: ConversationCase) -> ConversationRun: ...


# --- the harness's own state ---------------------------------------------------------------------


def sees(person: ConvPerson, doc: ConvDoc, revoked: set[str]) -> bool:
    """Whether ``person`` may see ``doc`` now (the harness's rule; the bridge maps it to ACLs)."""
    if doc.key in revoked:
        return False
    match doc.audience:
        case "all":
            return True
        case "owner_only":
            return False
        case "section_9a":
            return person.role == "class_teacher" and person.section == "9A"
        case "member_u1":
            return person.key == "U1"
    return False  # pragma: no cover


def access(person: ConvPerson) -> tuple[str, str | None]:
    """What decides document reach apart from membership entries (the cache fingerprint)."""
    return person.role, person.section


def script_of(text: str) -> Literal["te", "latin"]:
    return "te" if _TELUGU.search(text) else "latin"


class StepOutcome(_Model):
    id: str
    step: int
    category: Category
    leaks: tuple[str, ...]
    violations: tuple[str, ...]
    context_ok: bool | None
    followups_match: bool | None
    preference_ok: bool | None


def _question_of(step: ConvStep, asked: dict[int, tuple[str, str, str]]) -> str:
    if step.action == "regenerate" and step.target is not None:
        return asked[step.target][2]
    return step.question or ""


def _leaks(  # noqa: PLR0917 - everything a step may not show
    who: ConvPerson,
    turn: TurnResult,
    question: str,
    docs: Iterable[ConvDoc],
    revoked: set[str],
    asked: dict[int, tuple[str, str, str]],
    memories: dict[str, list[str]],
    deleted: set[tuple[str, str]],
) -> list[str]:
    found: list[str] = []
    shown = " ".join([turn.model_input, turn.text, *turn.followups, *(t for _, t in turn.memory)])
    for doc in docs:
        if not sees(who, doc, revoked):
            if doc.marker in shown:
                found.append(f"marker:{doc.key}")
            if doc.key in turn.citations or doc.key in turn.provided:
                found.append(f"source:{doc.key}")
    for other_step, (owner, _conv, earlier) in asked.items():
        if owner != who.key and earlier and earlier != question and earlier in turn.model_input:
            found.append(f"question:{other_step}")
    for owner, items in memories.items():
        if owner != who.key and any(item in turn.model_input for item in items):
            found.append(f"memory:{owner}")
    for cited in turn.citations:
        if cited.startswith("chat:"):
            origin = int(cited.split(":", 1)[1])
            owner, conv, _q = asked.get(origin, ("?", "?", ""))
            if owner != who.key or (owner, conv) in deleted:
                found.append(f"chat:{origin}")
    return found


def score(case: ConversationCase, run: ConversationRun) -> tuple[StepOutcome, ...]:  # noqa: PLR0912, PLR0915 - one state machine
    people = {p.key: p for p in case.people}
    docs = {d.key: d for d in case.docs}
    turns = {t.step: t for t in run.turns}
    revoked: set[str] = set()
    revised_at: dict[str, int] = {}
    memory_off: set[str] = set()
    memories: dict[str, list[str]] = {p: [] for p in people}
    superseded: set[str] = set()
    asked: dict[int, tuple[str, str, str]] = {}  # step -> (who, conversation, question)
    deleted: set[tuple[str, str]] = set()
    history: dict[tuple[str, str], list[int]] = {}
    outcomes: list[StepOutcome] = []
    for index, step in enumerate(case.steps):
        who = people[step.who]
        turn = turns.get(index, TurnResult(step=index))
        if step.action == "revoke" and step.doc:
            revoked.add(step.doc)
            continue
        if step.action == "revise" and step.doc:
            revised_at[step.doc] = index
            continue
        if step.action == "memory_off":
            memory_off.add(who.key)
            continue
        if step.action == "delete_conversation":
            deleted.add((who.key, step.conversation))
            continue
        question = _question_of(step, asked)
        leaks = _leaks(who, turn, question, docs.values(), revoked, asked, memories, deleted)
        cache = (
            _cache_violations(step, turn, asked, people, revised_at, history, memories)
            if turn.cached_from is not None
            else []
        )
        conversation = step.conversation
        if step.action in ("regenerate", "edit") and step.target is not None:
            target_who, conversation, _target_q = asked[step.target]
            thread = history.get((target_who, conversation), [])
            if step.target in thread:
                position = thread.index(step.target)
                if step.action == "edit":
                    superseded.update(asked[later][2] for later in thread[position:])
                history[(target_who, conversation)] = thread[:position]
        violations = ["superseded_question_sent" for q in superseded if q and q in turn.model_input]
        if who.key in memory_off and any(m in turn.model_input for m in memories[who.key]):
            violations.append("memory_used_while_off")
        if step.forbidden_memory and any(step.forbidden_memory in m for m in turn.memories_after):
            violations.append("memory_about_others_stored")
        violations += cache
        if step.action == "remember" and who.key not in memory_off:
            memories[who.key] += [text for action, text in turn.memory if action == "saved"]
        memories[who.key] += [text for action, text in turn.memory if action == "suggested"]
        asked[index] = (who.key, conversation, question)
        if step.action != "remember":
            history.setdefault((who.key, conversation), []).append(index)
        context_ok = (
            all(s in turn.citations for s in step.expect_sources) if step.expect_sources else None
        )
        followups_match = None
        if turn.followups:
            wanted = script_of(question)
            followups_match = all(
                case.locale == "mixed" or script_of(f) == wanted for f in turn.followups
            )
        preference_ok = (
            script_of(turn.text) == step.expect_script if step.expect_script is not None else None
        )
        outcomes.append(
            StepOutcome(
                id=case.id,
                step=index,
                category=case.category,
                leaks=tuple(leaks),
                violations=tuple(violations),
                context_ok=context_ok,
                followups_match=followups_match,
                preference_ok=preference_ok,
            )
        )
    return tuple(outcomes)


def _cache_violations(  # noqa: PLR0917 - the state the cache rules need
    step: ConvStep,
    turn: TurnResult,
    asked: dict[int, tuple[str, str, str]],
    people: dict[str, ConvPerson],
    revised_at: dict[str, int],
    history: dict[tuple[str, str], list[int]],
    memories: dict[str, list[str]],
) -> list[str]:
    origin = turn.cached_from
    if origin is None or origin not in asked:
        return ["cache_unknown_origin"]
    owner = people[asked[origin][0]]
    who = people[step.who]
    found: list[str] = []
    if access(owner) != access(who):
        found.append("cache_other_access")
    if any(at > origin for at in revised_at.values()):
        found.append("cache_after_revision")
    if step.action == "regenerate":
        found.append("cache_on_regenerate")
    if history.get((who.key, step.conversation)):
        found.append("cache_with_history")
    if memories[who.key]:
        found.append("cache_with_memory")
    return found


class ConversationMetrics(_Model):
    conversation_items: int
    conversation_leakage_count: int | None
    conversation_scope_violations: int | None
    conversation_context_accuracy: float | None
    followup_language_match: float | None
    memory_preference_applied: float | None


def _ratio(values: Sequence[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate(outcomes: Sequence[StepOutcome]) -> ConversationMetrics:
    if not outcomes:
        return ConversationMetrics(
            conversation_items=0,
            conversation_leakage_count=None,
            conversation_scope_violations=None,
            conversation_context_accuracy=None,
            followup_language_match=None,
            memory_preference_applied=None,
        )
    return ConversationMetrics(
        conversation_items=len({o.id for o in outcomes}),
        conversation_leakage_count=sum(1 for o in outcomes if o.leaks),
        conversation_scope_violations=sum(1 for o in outcomes if o.violations),
        conversation_context_accuracy=_ratio(
            [o.context_ok for o in outcomes if o.context_ok is not None]
        ),
        followup_language_match=_ratio(
            [o.followups_match for o in outcomes if o.followups_match is not None]
        ),
        memory_preference_applied=_ratio(
            [o.preference_ok for o in outcomes if o.preference_ok is not None]
        ),
    )


def run(
    cases: Iterable[ConversationCase], adapter: ConversationAdapter
) -> tuple[ConversationMetrics, tuple[StepOutcome, ...]]:
    outcomes: list[StepOutcome] = []
    for case in cases:
        outcomes += score(case, adapter.run_conversation(case))
    return aggregate(outcomes), tuple(outcomes)


def validate_cases(cases: Sequence[ConversationCase]) -> None:  # noqa: PLR0912 - one check per rule
    """Keys, markers and step references agree (a wrong case would hide a leak)."""
    seen: set[str] = set()
    markers: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"duplicate conversation case {case.id}")
        seen.add(case.id)
        people = {p.key for p in case.people}
        docs = {d.key: d for d in case.docs}
        for doc in case.docs:
            if doc.marker in markers:
                raise ValueError(f"{case.id}: marker {doc.marker} is used twice")
            markers.add(doc.marker)
            if doc.marker not in doc.content or (doc.revised and doc.marker not in doc.revised):
                raise ValueError(f"{case.id}: {doc.key} must contain its marker")
        for index, step in enumerate(case.steps):
            if step.who not in people:
                raise ValueError(f"{case.id}: step {index} by an unknown person")
            if step.action in ("ask", "edit", "remember") and not step.question:
                raise ValueError(f"{case.id}: step {index} needs a question")
            if step.action in ("regenerate", "edit"):
                if step.target is None or not 0 <= step.target < index:
                    raise ValueError(f"{case.id}: step {index} targets no earlier step")
                if case.steps[step.target].action not in ("ask", "edit", "regenerate"):
                    raise ValueError(f"{case.id}: step {index} targets a step that asked nothing")
            if step.action in ("revoke", "revise") and step.doc not in docs:
                raise ValueError(f"{case.id}: step {index} names an unknown document")
            if step.action == "revise" and docs[str(step.doc)].revised is None:
                raise ValueError(f"{case.id}: step {index} revises a document without a revision")
            for key in step.expect_sources:
                if key not in docs:
                    raise ValueError(f"{case.id}: step {index} expects an unknown document")
                revoked = {
                    s.doc for s in case.steps[:index] if s.action == "revoke" and s.doc is not None
                }
                person = next(p for p in case.people if p.key == step.who)
                if not sees(person, docs[key], revoked):
                    raise ValueError(
                        f"{case.id}: step {index} expects a document its asker cannot see"
                    )


__all__ = [
    "ConvDoc",
    "ConvPerson",
    "ConvStep",
    "ConversationAdapter",
    "ConversationCase",
    "ConversationMetrics",
    "ConversationRun",
    "StepOutcome",
    "TurnResult",
    "access",
    "aggregate",
    "run",
    "score",
    "script_of",
    "sees",
    "validate_cases",
]
