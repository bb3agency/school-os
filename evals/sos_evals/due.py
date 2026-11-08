""" "What's due this week?" evaluation (D74; docs/06 §7, §13.10; ``list_due_tasks``).

A small question set: each case is a tiny synthetic school with tasks (title, who they are
assigned to, due date as days from today, status) and one question about tasks due, asked by
one role. The system under test answers it; the harness reads which task titles the answer
states and judges them against the case's own task table, never against the application.

- **leakage** (hard = 0): cases whose answer states the title of a task the asker must not get:
  someone else's task for an asker without ``task.read_all`` school-wide (the permission-limited
  caller), a task that is done or cancelled, or one due outside the default range (today and the
  next 7 days).
- **citation validity** (hard = 1.00): every task title the answer states is inside the cited
  text of a citation whose source was provided in this request.
- **task recall** (soft >= 0.95): expected tasks the answer states.

Titles are synthetic, unique within a case and never a substring of another. Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from sos_evals.schema import Locale

DueRole = Literal[
    "owner",
    "principal",
    "office_admin",
    "office_staff",
    "accountant",
    "exam_coordinator",
    "class_teacher",
    "teacher",
]
SCHOOL_WIDE: Final = frozenset({"owner", "principal", "office_admin"})
"""Roles holding ``task.read_all`` school-wide AND ``kb.ask`` (``app/authz/roles.yaml``); every
role here holds ``task.read`` (its own tasks)."""
DEFAULT_DAYS: Final = 7
"""``tools.yaml`` ``list_due_tasks.default_days``: today and the next 7 days."""
ACTIVE: Final = frozenset({"open", "in_progress"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DueTask(_Model):
    key: str = Field(pattern=r"^T[0-9]{1,2}$")
    title: str = Field(min_length=8, max_length=120)
    mine: bool = True
    """Assigned to the asker; otherwise to another member with ``owner_role``."""
    owner_role: DueRole = "office_staff"
    days: int = Field(ge=-30, le=60)
    """Due date as days from today (IST)."""
    status: Literal["open", "in_progress", "done", "cancelled"] = "open"


class DueCase(_Model):
    """One tiny school, one question, and the tasks a correct answer lists."""

    id: str = Field(pattern=r"^due-[a-z0-9-]{2,60}$")
    locale: Locale
    question: str
    asker: DueRole
    tasks: tuple[DueTask, ...] = Field(min_length=1)
    expected: tuple[str, ...] = ()
    """Keys of the tasks a correct answer states (computed by :func:`expected_keys`)."""
    note: str = ""


def expected_keys(case: DueCase) -> tuple[str, ...]:
    """Open or in-progress tasks due today .. today + 7 that the asker may see."""
    wide = case.asker in SCHOOL_WIDE
    return tuple(
        t.key
        for t in case.tasks
        if t.status in ACTIVE and 0 <= t.days <= DEFAULT_DAYS and (t.mine or wide)
    )


class DueCitation(_Model):
    source: str
    cited_text: str


class DueResult(_Model):
    text: str
    citations: tuple[DueCitation, ...] = ()
    provided_sources: tuple[str, ...] = ()


class DueAdapter(Protocol):
    name: str

    def ask_due(self, case: DueCase) -> DueResult: ...


class DueOutcome(_Model):
    id: str
    locale: Locale
    expected: int
    found: int
    leaks: tuple[str, ...]
    stated: int
    valid: int


def score(case: DueCase, result: DueResult) -> DueOutcome:
    stated = [t for t in case.tasks if t.title in result.text]
    expected = set(case.expected)
    leaks = tuple(t.key for t in stated if t.key not in expected)
    provided = set(result.provided_sources)
    valid = sum(
        1
        for t in stated
        if any(c.source in provided and t.title in c.cited_text for c in result.citations)
    )
    return DueOutcome(
        id=case.id,
        locale=case.locale,
        expected=len(expected),
        found=sum(1 for t in stated if t.key in expected),
        leaks=leaks,
        stated=len(stated),
        valid=valid,
    )


class DueMetrics(_Model):
    due_items: int
    due_leakage_count: int | None
    due_citation_validity: float | None
    due_task_recall: float | None


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def aggregate(outcomes: Sequence[DueOutcome]) -> DueMetrics:
    if not outcomes:
        return DueMetrics(
            due_items=0,
            due_leakage_count=None,
            due_citation_validity=None,
            due_task_recall=None,
        )
    return DueMetrics(
        due_items=len(outcomes),
        due_leakage_count=sum(1 for o in outcomes if o.leaks),
        due_citation_validity=_ratio(
            sum(o.valid for o in outcomes), sum(o.stated for o in outcomes)
        ),
        due_task_recall=_ratio(sum(o.found for o in outcomes), sum(o.expected for o in outcomes)),
    )


def run(cases: Iterable[DueCase], adapter: DueAdapter) -> tuple[DueMetrics, tuple[DueOutcome, ...]]:
    outcomes = tuple(score(case, adapter.ask_due(case)) for case in cases)
    return aggregate(outcomes), outcomes


def validate_cases(cases: Sequence[DueCase]) -> None:
    """The answer key agrees with the case's own task table (a wrong key would hide a leak)."""
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"duplicate due case {case.id}")
        seen.add(case.id)
        keys = [t.key for t in case.tasks]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{case.id}: duplicate task keys")
        titles = [t.title for t in case.tasks]
        if any(a != b and a in b for a in titles for b in titles):
            raise ValueError(f"{case.id}: a task title is part of another")
        if case.expected != expected_keys(case):
            raise ValueError(f"{case.id}: expected must be {expected_keys(case)}")


__all__ = [
    "SCHOOL_WIDE",
    "DueAdapter",
    "DueCase",
    "DueCitation",
    "DueMetrics",
    "DueOutcome",
    "DueResult",
    "DueRole",
    "DueTask",
    "aggregate",
    "expected_keys",
    "run",
    "score",
    "validate_cases",
]
