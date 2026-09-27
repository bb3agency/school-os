"""Every piece of background work reaches a worker (docs/04 §6, FR-OPS-004).

A task name typed wrong in ``ops.register_outbox_route``, a beat entry for a module missing from
``TASK_MODULES`` or an event nobody consumes fails silently in production (the outbox logs
``ops.outbox.unrouted``; beat sends to a task no worker knows). These tests find such gaps.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import app
from app.ops import service as ops
from sos_worker.celery_app import QUEUES, celery_app

APP_DIR = Path(app.__file__).resolve().parent


def _loaded() -> set[str]:
    celery_app.loader.import_default_modules()
    return {name for name in celery_app.tasks if not name.startswith("celery.")}


def _queue(task: str) -> str:
    # send_task (outbox dispatcher, beat) honours task_routes, not the task's own queue.
    return str(celery_app.amqp.router.route({}, task)["queue"].name)


def test_FR_OPS_004_every_outbox_route_names_a_registered_task_on_a_consumed_queue() -> None:
    tasks = _loaded()
    assert ops.OUTBOX_ROUTES, "no outbox routes registered"
    problems = [
        f"{event} -> {task}"
        for event, task in ops.OUTBOX_ROUTES.items()
        if task not in tasks or _queue(task) not in QUEUES
    ]
    assert problems == []


def test_every_beat_entry_names_a_registered_task_on_a_consumed_queue() -> None:
    tasks = _loaded()
    problems = [
        f"{key} -> {entry['task']}"
        for key, entry in celery_app.conf.beat_schedule.items()
        if entry["task"] not in tasks or _queue(entry["task"]) not in QUEUES
    ]
    assert problems == []


def test_every_registered_task_is_routed_to_a_consumed_queue() -> None:
    assert sorted(t for t in _loaded() if _queue(t) not in QUEUES) == []


def _enqueued_event_types() -> dict[str, str]:
    """Event types passed to ``ops.enqueue_event`` anywhere in app code (literal or module
    constant), mapped to where they are enqueued."""
    found: dict[str, str] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        if path.parent.name == "ops":
            continue
        tree = ast.parse(path.read_text("utf-8"))
        module = None
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and len(node.args) >= 2):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            # ops.enqueue_event(session, <type>, ...) and module helpers that forward their
            # event type to it, such as changes.service._event(session, <type>, row).
            if name not in ("enqueue_event", "_event"):
                continue
            arg = node.args[1]
            where = f"{path.relative_to(APP_DIR)}:{node.lineno}"
            candidates: list[ast.expr] = (
                [arg.body, arg.orelse] if isinstance(arg, ast.IfExp) else [arg]
            )
            for expr in candidates:
                if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
                    found[expr.value] = where
                elif isinstance(expr, ast.Name):
                    if module is None:
                        rel = path.relative_to(APP_DIR.parent).with_suffix("")
                        module = importlib.import_module(".".join(rel.parts))
                    value = getattr(module, expr.id, None)
                    if isinstance(value, str):
                        found[value] = where
                # Anything else (a forwarded parameter) is covered where the helper is
                # called with a literal.
    return found


def test_FR_OPS_004_every_enqueued_event_has_a_consumer() -> None:
    _loaded()
    events = _enqueued_event_types()
    # The scan finds module constants and literals forwarded through helpers.
    assert "document.version.registered" in events
    assert "change_request.expired" in events
    missing = sorted(
        f"{event} ({where})" for event, where in events.items() if event not in ops.OUTBOX_ROUTES
    )
    assert missing == []
