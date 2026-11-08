"""``list_due_tasks`` without a database (D74; docs/06 §7, §8; ADR-0008 as amended by D74;
invariants 8, 9, 13; DL-08): the IST date range (default today + 7 days, at most 31), the
passages (titles, due dates, status, "you" or a role; never details or names), the ``sos://task``
source and the fail-closed registration. Synthetic data only."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.authz.context import ScopeGrant
from app.circulars.schemas import AskDueTask, AskDueTasks
from app.knowledge import sources
from app.knowledge.config.tools import load_tools_config
from app.knowledge.resolve import href_for
from app.knowledge.tools import due
from app.knowledge.tools.due import DueArgs, ListDueTasksTool, TaskReader, date_range

TODAY = dt.date(2026, 11, 9)


def _range(**kw: Any) -> tuple[dt.date, dt.date, bool]:
    return date_range(DueArgs(**kw), today=TODAY, default_days=7, max_days=31)


def test_D74_default_range_is_today_and_the_next_7_days() -> None:
    assert _range() == (TODAY, dt.date(2026, 11, 16), False)
    assert _range(from_date="2026-12-01") == (dt.date(2026, 12, 1), dt.date(2026, 12, 8), False)


def test_D74_a_range_longer_than_31_days_is_cut() -> None:
    assert _range(from_date="2026-11-01", to_date="2027-01-31") == (
        dt.date(2026, 11, 1),
        dt.date(2026, 12, 2),
        True,
    )


def test_D74_an_end_before_the_start_or_a_bad_date_is_refused() -> None:
    with pytest.raises(ValueError, match="before"):
        _range(from_date="2026-11-10", to_date="2026-11-01")
    with pytest.raises(ValueError, match="month"):
        _range(from_date="2026-13-40")
    with pytest.raises(ValidationError, match="pattern"):
        DueArgs.model_validate({"from_date": "next week"})
    with pytest.raises(ValidationError, match="Extra inputs"):
        DueArgs.model_validate({"owner": "someone"})


def test_D74_invariant_13_description_and_caps_live_in_tools_yaml() -> None:
    spec = load_tools_config().tools["list_due_tasks"]
    assert spec.permission == "task.read"
    assert spec.description is not None
    assert (spec.max_results, spec.default_days, spec.max_days) == (30, 7, 31)


def test_D74_task_sources_parse_and_open_the_tasks_screen() -> None:
    tid = uuid.uuid4()
    uri = sources.task(tid)
    assert uri == f"sos://task/{tid}"
    ref = sources.parse(uri)
    assert (ref.kind, ref.object_id) == ("task", tid)
    assert href_for(ref) == "/tasks"


class _Ctx:
    def __init__(self, *perms: str) -> None:
        self.perms = set(perms)

    def has(self, permission: str) -> bool:
        return permission in self.perms

    def scope_for(self, permission: str) -> ScopeGrant:  # pragma: no cover - unused
        raise AssertionError


def _task(**kw: Any) -> AskDueTask:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "title": "Synthetic UDISE+ upload",
        "due_on": dt.date(2026, 11, 12),
        "status": "open",
        "overdue": False,
        "mine": True,
        "assignee_roles": (),
        "source": "manual",
        "linked_circular": False,
        "circular_title": None,
    }
    return AskDueTask.model_validate(base | kw)


@pytest.fixture
def readers() -> Any:
    saved = list(due.TASK_READERS)
    due.TASK_READERS.clear()
    yield due.TASK_READERS
    due.TASK_READERS[:] = saved


def _tool() -> ListDueTasksTool:
    return ListDueTasksTool(load_tools_config().tools["list_due_tasks"])


def test_D74_not_offered_and_an_error_without_a_registered_reader(readers: Any) -> None:
    tool = _tool()
    ctx: Any = _Ctx("task.read")
    assert not tool.available(None, ctx)  # type: ignore[arg-type]
    assert tool.run(None, ctx, "c1", {}).is_error  # type: ignore[arg-type]


def test_D74_passages_carry_titles_dates_status_and_roles_only(readers: Any) -> None:
    calls: list[dict[str, Any]] = []
    tasks = (
        _task(),
        _task(
            title="Synthetic nominal rolls",
            due_on=dt.date(2026, 11, 7),
            status="in_progress",
            overdue=True,
            mine=False,
            assignee_roles=("office_staff",),
            source="circular",
            linked_circular=True,
            circular_title="Synthetic DEO circular",
        ),
        _task(title="Synthetic hidden follow up", mine=False, linked_circular=True),
    )

    def reader(_s: Any, _c: Any, *, start: dt.date, end: dt.date, limit: int) -> AskDueTasks:
        calls.append({"start": start, "end": end, "limit": limit})
        return AskDueTasks(start=start, end=end, school_wide=True, tasks=tasks, more=True)

    readers.append(TaskReader(due=reader, visible=lambda *_: True))
    tool = _tool()
    ctx: Any = _Ctx("task.read")
    out = tool.run(None, ctx, "c1", {}, today=TODAY)  # type: ignore[arg-type]
    assert not out.is_error
    assert calls == [{"start": TODAY, "end": dt.date(2026, 11, 16), "limit": 30}]
    first, second, third = out.blocks
    assert first.source == sources.task(tasks[0].id)
    assert first.title == "Task · Synthetic UDISE+ upload · due 12/11/2026"
    assert "Due on 12/11/2026 (Thursday). Status: open. Assigned to you. Added by staff." in (
        first.text
    )
    assert "every task of the school due 09/11/2026 to 16/11/2026" in first.text
    assert "Status: in progress, overdue" in second.text
    assert "a staff member with the role office staff" in second.text
    assert "From the circular: Synthetic DEO circular." in second.text
    assert "a staff member who is no longer active" in third.text
    assert "From a circular you cannot open." in third.text
    assert "More tasks are due in this range" in third.text
    assert "More tasks" not in first.text


def test_D74_permission_is_rechecked_when_the_tool_runs(readers: Any) -> None:
    readers.append(TaskReader(due=lambda *a, **k: None, visible=lambda *_: True))  # type: ignore[arg-type]
    tool = _tool()
    ctx: Any = _Ctx("kb.ask")
    assert not tool.allowed(ctx)
    assert tool.run(None, ctx, "c1", {}).is_error  # type: ignore[arg-type]
