"""``list_due_tasks``: open tasks and circular deadlines due in a date range (D74; docs/06 §7;
ADR-0008 as amended by D74; invariants 8, 9).

Reads ``circulars.service.due_tasks_for_ask`` under the caller's ``UserContext`` (permission
``task.read``): the caller's own open and in-progress tasks, or every task of the school for a
school-wide ``task.read_all`` holder (the Tasks screen rule). Arguments are IST dates
``from_date`` and ``to_date`` (both optional, inclusive): ``from_date`` defaults to today (IST)
and ``to_date`` to ``from_date`` + ``default_days`` (``tools.yaml``); a range longer than
``max_days`` is cut to ``max_days`` and the passages say so. One ``search_result`` block per task
(at most ``max_results``, soonest first), source ``sos://task/{id}`` (§8): the title, the due date,
the status (overdue when the date has passed), "you" or the assignee's role (never a name), and
where it comes from (the circular's title only when the caller may open that circular, the
portal calendar, or staff). Never the task's details and never a circular's AI summary (DL-08);
tasks hold no student data. No task in range: no passage (the answer says nothing was found).

**Why a registered reader.** ``circulars.service`` itself calls ``knowledge.service`` (circular
reading, notice drafts), so this package cannot import it (the knowledge layers contract and an
import cycle). ``circulars.service`` registers its two public read functions in
:data:`TASK_READERS` on import (re-exported by ``knowledge.service``, like ``INDEXED_HOOKS``);
without a registered reader the tool is not offered and a ``task`` source is not visible (fail
closed). Read-only; no network.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.errors import DomainError
from app.knowledge import sources
from app.knowledge.config.tools import ToolConfig
from app.knowledge.domain import SearchResultBlock, ToolOutcome, ToolSpec

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext
    from app.circulars.schemas import AskDueTask, AskDueTasks

NAME: Final = "list_due_tasks"
IST: Final = ZoneInfo("Asia/Kolkata")
_DATE_PATTERN: Final = r"^\d{4}-\d{2}-\d{2}$"
_STATUS: Final = {"open": "open", "in_progress": "in progress"}


@dataclass(frozen=True, slots=True)
class TaskReader:
    """The two public read functions of ``circulars.service`` the tool and source visibility
    use (registered by that module on import)."""

    due: Callable[..., AskDueTasks]
    """``due_tasks_for_ask(session, ctx, *, start, end, limit)``."""
    visible: Callable[[Session, UserContext, uuid.UUID], bool]
    """``task_visible_for_ask(session, ctx, task_id)``."""


TASK_READERS: list[TaskReader] = []
"""Registered on import by ``circulars.service``; the first one is used."""


def task_reader() -> TaskReader | None:
    return TASK_READERS[0] if TASK_READERS else None


class DueArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    from_date: str | None = Field(default=None, pattern=_DATE_PATTERN)
    to_date: str | None = Field(default=None, pattern=_DATE_PATTERN)


def today_ist(now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(dt.UTC)).astimezone(IST).date()


def date_range(
    args: DueArgs, *, today: dt.date, default_days: int, max_days: int
) -> tuple[dt.date, dt.date, bool]:
    """``(start, end, cut)``: the inclusive IST range asked for; ``cut`` when it was longer
    than ``max_days`` and was shortened. Raises ``ValueError`` for a bad date or an end before
    the start."""
    start = dt.date.fromisoformat(args.from_date) if args.from_date else today
    end = (
        dt.date.fromisoformat(args.to_date)
        if args.to_date
        else start + dt.timedelta(days=default_days)
    )
    if end < start:
        raise ValueError("to_date before from_date")
    cut = (end - start).days > max_days
    if cut:
        end = start + dt.timedelta(days=max_days)
    return start, end, cut


def _date(value: dt.date) -> str:
    return value.strftime("%d/%m/%Y")


def _assignee(task: AskDueTask) -> str:
    if task.mine:
        return "you"
    if not task.assignee_roles:
        return "a staff member who is no longer active"
    roles = ", ".join(r.replace("_", " ") for r in task.assignee_roles)
    return f"a staff member with the role {roles}"


def _origin(task: AskDueTask) -> str:
    if task.linked_circular or task.source == "circular":
        if task.circular_title:
            return f"From the circular: {task.circular_title}."
        return "From a circular you cannot open."
    if task.source == "portal_calendar":
        return "From the portal calendar."
    return "Added by staff."


class ListDueTasksTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``list_due_tasks``."""

    def __init__(self, config: ToolConfig) -> None:
        if config.description is None:
            raise ValueError("list_due_tasks needs a description in tools.yaml")
        self._max = config.max_results or 30
        self._default_days = config.default_days or 7
        self._max_days = config.max_days or 31
        self._spec = ToolSpec(
            name=NAME,
            description=config.description,
            input_schema={
                "type": "object",
                "properties": {
                    "from_date": {
                        "type": "string",
                        "pattern": _DATE_PATTERN,
                        "description": "First due date, YYYY-MM-DD (IST); default today",
                    },
                    "to_date": {
                        "type": "string",
                        "pattern": _DATE_PATTERN,
                        "description": (
                            f"Last due date, YYYY-MM-DD (IST); default {self._default_days} "
                            f"days after from_date; at most {self._max_days} days after it"
                        ),
                    },
                },
                "additionalProperties": False,
            },
            permission=config.permission,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def allowed(self, ctx: UserContext) -> bool:
        return ctx.has(self._spec.permission)

    def available(self, session: Session, ctx: UserContext) -> bool:
        """Offered only while ``circulars.service`` has registered its reader (fail closed)."""
        return self.allowed(ctx) and task_reader() is not None

    def run(
        self,
        session: Session,
        ctx: UserContext,
        call_id: str,
        arguments: Mapping[str, object],
        *,
        today: dt.date | None = None,
    ) -> ToolOutcome:
        error = ToolOutcome(call_id=call_id, blocks=(), is_error=True)
        reader = task_reader()
        if reader is None or not self.allowed(ctx):
            return error
        try:
            args = DueArgs.model_validate(dict(arguments))
            start, end, cut = date_range(
                args,
                today=today or today_ist(),
                default_days=self._default_days,
                max_days=self._max_days,
            )
            found = reader.due(session, ctx, start=start, end=end, limit=self._max)
        except (ValidationError, ValueError, DomainError):
            return error
        notes: list[str] = []
        if cut:
            notes.append(
                f"Only due dates from {_date(start)} to {_date(end)} were checked "
                f"(at most {self._max_days} days at a time)."
            )
        if found.more:
            notes.append(
                "More tasks are due in this range than are listed here; the Tasks screen "
                "shows all of them."
            )
        blocks = [self._block(t, found) for t in found.tasks]
        if blocks and notes:
            last = blocks[-1]
            blocks[-1] = SearchResultBlock(
                source=last.source, title=last.title, text=f"{last.text} {' '.join(notes)}"
            )
        return ToolOutcome(call_id=call_id, blocks=tuple(blocks))

    @staticmethod
    def _block(task: AskDueTask, found: AskDueTasks) -> SearchResultBlock:
        status = _STATUS.get(task.status, task.status)
        if task.overdue:
            status += ", overdue"
        whose = "every task of the school" if found.school_wide else "your own tasks"
        text = (
            f"Task: {task.title}. Due on {_date(task.due_on)} ({task.due_on.strftime('%A')}). "
            f"Status: {status}. Assigned to {_assignee(task)}. {_origin(task)} "
            f"(Listed from {whose} due {_date(found.start)} to {_date(found.end)}.)"
        )
        return SearchResultBlock(
            source=sources.task(task.id),
            title=f"Task · {task.title} · due {_date(task.due_on)}",
            text=text,
        )


__all__ = [
    "NAME",
    "TASK_READERS",
    "DueArgs",
    "ListDueTasksTool",
    "TaskReader",
    "date_range",
    "task_reader",
    "today_ist",
]
