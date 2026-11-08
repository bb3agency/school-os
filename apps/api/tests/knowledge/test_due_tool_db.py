"""``list_due_tasks`` on the real database through the ask route (D74; docs/06 §7; ADR-0008 as
amended by D74; FR-TASK-*, FR-KB-004, FR-KB-005; invariants 3, 5, 8, 9; DL-08).

A scripted model calls ``list_due_tasks`` once and cites every passage it gets back. Each test
checks what reached the model (the recorded request bodies): the caller's own tasks unless they
hold ``task.read_all`` school-wide; only open and in-progress tasks due in the range; "you" or
the assignee's role, never a name; never a task's details; a circular's title only when the
caller may open that circular; citations ``sos://task/{id}`` that validate and resolve; nothing
of a task in the logs. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine
from structlog.testing import capture_logs

from app.circulars import service as circulars
from app.circulars.schemas import TaskCreate, TaskStatusIn
from app.core.db import tenant_session
from app.knowledge import composition

pytestmark = pytest.mark.db


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


TESTS = Path(__file__).resolve().parents[1]
K = _load("sos_test_ask_support", TESTS / "knowledge" / "ask_support.py")
R = _load("sos_test_record_tools_db", TESTS / "knowledge" / "test_record_tools_db.py")
C = _load("sos_test_circulars_support", TESTS / "circulars" / "support.py")
W = K.W
world = W.world
api = W.api
ask_with = R.ask_with

TEACHER_TASK = "Synthetic due task for the teacher"
STAFF_TASK = "Synthetic due task for the office"
LATER_TASK = "Synthetic task due next month"
DONE_TASK = "Synthetic task already done"
OPEN_CIRCULAR_TASK = "Synthetic circular follow up A"
HIDDEN_CIRCULAR_TASK = "Synthetic circular follow up B"
DETAILS = "Synthetic private details that never reach Ask"
OPEN_TITLE = "Synthetic open circular on sports day"
HIDDEN_TITLE = "Synthetic principal-only circular"


def _task(
    school: Any,
    owner: Any,
    title: str,
    days: int,
    *,
    document_id: uuid.UUID | None = None,
) -> uuid.UUID:
    actor = C.ctx(school, "owner")
    with tenant_session(school.tenant_id, actor.user_id) as db:
        out = circulars.create_task(
            db,
            actor,
            TaskCreate(
                title=title,
                details=DETAILS,
                owner_membership_id=owner.membership_id,
                due_on=circulars.today_ist() + dt.timedelta(days=days),
                document_id=document_id,
            ),
        )
    return out.id


@pytest.fixture(scope="module")
def due(world: Any, admin_engine: Engine) -> Iterator[dict[str, uuid.UUID]]:
    C.install()
    K.SW.ensure_students(world)
    K.enable_ai(admin_engine, world.a.tenant_id)
    composition.set_runtime(None)
    a = world.a
    teacher, office = a.people["teacher"], a.people["office_staff"]
    open_doc = C.circular(admin_engine, a, title=OPEN_TITLE)
    hidden_doc = C.circular(admin_engine, a, title=HIDDEN_TITLE, acl=[("role", "principal")])
    ids = {
        "teacher": _task(a, teacher, TEACHER_TASK, 2),
        "staff": _task(a, office, STAFF_TASK, 3),
        "later": _task(a, teacher, LATER_TASK, 20),
        "done": _task(a, teacher, DONE_TASK, 1),
        "open_circular": _task(a, teacher, OPEN_CIRCULAR_TASK, 4, document_id=open_doc),
        "hidden_circular": _task(a, teacher, HIDDEN_CIRCULAR_TASK, 5, document_id=hidden_doc),
        "b": _task(world.b, world.b.people["owner"], TEACHER_TASK, 2),
    }
    actor = C.ctx(a, "teacher")
    with tenant_session(a.tenant_id, actor.user_id) as db:
        current = circulars.get_task(db, actor, ids["done"])
        circulars.set_task_status(
            db, actor, ids["done"], TaskStatusIn(status="done"), current.version
        )
    yield ids
    composition.set_runtime(None)


def _offered(transport: Any) -> set[str]:
    return {t["name"] for t in transport.sent[0]["tools"]}


def _text(transport: Any) -> str:
    return " ".join(r["content"][0]["text"] for r in transport.results)


def _cited(events: Any) -> list[str]:
    return [d["source"] for e, d in events if e == "citation"]


def test_D74_offered_to_task_readers_only(world: Any, api: Any, due: dict[str, uuid.UUID]) -> None:
    for role in ("owner", "principal", "office_staff", "teacher", "accountant"):
        transport, _ = ask_with(api, world.person(role), "count_students", {})
        assert "list_due_tasks" in _offered(transport), role


def test_D74_a_permission_limited_caller_sees_only_their_own_tasks(
    world: Any, api: Any, due: dict[str, uuid.UUID]
) -> None:
    transport, events = ask_with(api, world.person("teacher"), "list_due_tasks", {})
    text = _text(transport)
    assert TEACHER_TASK in text
    assert OPEN_CIRCULAR_TASK in text
    assert "Assigned to you" in text
    assert "your own tasks" in text
    sent = str(transport.sent)
    assert STAFF_TASK not in sent  # someone else's task never reaches the model
    assert LATER_TASK not in sent  # outside the default range (today + 7 days)
    assert DONE_TASK not in sent  # done tasks are not "due"
    cited = _cited(events)
    assert f"sos://task/{due['teacher']}" in cited
    assert f"sos://task/{due['staff']}" not in cited
    assert f"sos://task/{due['b']}" not in cited


def test_D74_school_wide_readers_see_every_task_by_role_never_by_name(
    world: Any, api: Any, due: dict[str, uuid.UUID]
) -> None:
    transport, events = ask_with(api, world.person("owner"), "list_due_tasks", {})
    text = _text(transport)
    assert TEACHER_TASK in text
    assert STAFF_TASK in text
    assert "every task of the school" in text
    assert "with the role office staff" in text
    sent = str(transport.sent)
    for person in ("teacher", "office_staff"):
        assert world.a.people[person].display_name not in sent
    assert f"sos://task/{due['staff']}" in _cited(events)


def test_D74_DL_08_no_details_and_circular_title_only_when_visible(
    world: Any, api: Any, due: dict[str, uuid.UUID]
) -> None:
    transport, _ = ask_with(api, world.person("teacher"), "list_due_tasks", {})
    text = _text(transport)
    sent = str(transport.sent)
    assert DETAILS not in sent
    assert f"From the circular: {OPEN_TITLE}" in text
    assert HIDDEN_CIRCULAR_TASK in text
    assert "From a circular you cannot open" in text
    assert HIDDEN_TITLE not in sent


def test_D74_a_range_reaches_later_tasks_and_dates_are_quoted(
    world: Any, api: Any, due: dict[str, uuid.UUID]
) -> None:
    today = circulars.today_ist()
    start = (today + dt.timedelta(days=15)).isoformat()
    end = (today + dt.timedelta(days=25)).isoformat()
    transport, events = ask_with(
        api, world.person("teacher"), "list_due_tasks", {"from_date": start, "to_date": end}
    )
    text = _text(transport)
    assert LATER_TASK in text
    assert TEACHER_TASK not in text
    assert (today + dt.timedelta(days=20)).strftime("%d/%m/%Y") in text
    assert _cited(events) == [f"sos://task/{due['later']}"]


def test_D74_a_bad_range_is_an_error_result(
    world: Any, api: Any, due: dict[str, uuid.UUID]
) -> None:
    transport, _ = ask_with(
        api,
        world.person("teacher"),
        "list_due_tasks",
        {"from_date": "2026-12-10", "to_date": "2026-12-01"},
    )
    assert transport.error


def test_D74_a_cited_task_resolves_for_who_may_see_it_only(
    world: Any, api: Any, due: dict[str, uuid.UUID]
) -> None:
    source = f"sos://task/{due['staff']}"
    owner = api.call(
        world.person("owner"), "GET", "/api/v1/knowledge/resolve", params={"source": source}
    )
    assert owner.status_code == 200, owner.text
    assert owner.json()["href"] == "/tasks"
    assert owner.json()["kind"] == "task"
    teacher = api.call(
        world.person("teacher"), "GET", "/api/v1/knowledge/resolve", params={"source": source}
    )
    assert teacher.status_code == 404


def test_D74_invariant_5_no_task_text_in_logs(
    world: Any, api: Any, due: dict[str, uuid.UUID]
) -> None:
    with capture_logs() as logs:
        ask_with(api, world.person("owner"), "list_due_tasks", {})
    flat = repr(logs)
    for secret in (TEACHER_TASK, STAFF_TASK, DETAILS, OPEN_TITLE, OPEN_CIRCULAR_TASK):
        assert secret not in flat
