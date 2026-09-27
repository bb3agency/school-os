"""Documents -> outbox -> knowledge tasks (docs/06 §4, docs/04 §6; FR-OPS-004, FR-DOC-007).

The documents hooks enqueue ``kb.*`` events in the caller's transaction only while the knowledge
feature is on; the events route to registered ``knowledge.*`` tasks, which run the configured
pipeline, retry transient failures and refuse to run unwired.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from app.documents import service as documents
from app.knowledge import tasks
from app.knowledge.ingestion import hooks, runtime
from app.ops import service as ops


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


S = _load("sos_test_ingestion_support", Path(__file__).with_name("ingestion_support.py"))
DOC = uuid.UUID("0190e000-0000-7000-8000-0000000000d1")


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, str, dict[str, Any]]]:
    calls: list[tuple[Any, str, dict[str, Any]]] = []
    monkeypatch.setattr(
        ops, "enqueue_event", lambda s, event, payload: calls.append((s, event, dict(payload)))
    )
    return calls


@pytest.fixture
def wired() -> Any:
    w = S.world()
    runtime.configure(lambda: w.pipeline)
    yield w
    runtime.configure(None)


def kb(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
    monkeypatch.setattr(hooks, "get_settings", lambda: SimpleNamespace(kb_enabled=enabled))


def test_FR_OPS_004_hooks_and_routes_are_installed_once() -> None:
    hooks.install()
    hooks.install()
    assert documents.READY_HOOKS.count(hooks.on_version_ready) == 1
    assert documents.ACL_CHANGED_HOOKS.count(hooks.on_acl_changed) == 1
    assert ops.OUTBOX_ROUTES[hooks.READY_EVENT] == hooks.INGEST_TASK
    assert ops.OUTBOX_ROUTES[hooks.ACL_EVENT] == hooks.ACL_TASK
    # documents' own consumers are untouched (one task per event type).
    assert ops.OUTBOX_ROUTES[documents.SCAN_EVENT] == documents.SCAN_TASK
    assert ops.OUTBOX_ROUTES[documents.DELETED_EVENT] == documents.PURGE_TASK


def test_FR_OPS_004_ready_and_acl_hooks_enqueue_ids_in_the_same_transaction(
    monkeypatch: pytest.MonkeyPatch, enqueued: list[Any]
) -> None:
    kb(monkeypatch, enabled=True)
    session = object()
    version_id = uuid.uuid4()
    hooks.on_version_ready(session, DOC, version_id)  # type: ignore[arg-type]
    hooks.on_acl_changed(session, DOC)  # type: ignore[arg-type]
    assert enqueued == [
        (session, "kb.version.ready", {"document_id": DOC, "version_id": version_id}),
        (session, "kb.document.acl_changed", {"document_id": DOC}),
    ]


def test_nothing_is_enqueued_while_knowledge_is_off(
    monkeypatch: pytest.MonkeyPatch, enqueued: list[Any]
) -> None:
    kb(monkeypatch, enabled=False)
    hooks.on_version_ready(object(), DOC, uuid.uuid4())  # type: ignore[arg-type]
    hooks.on_acl_changed(object(), DOC)  # type: ignore[arg-type]
    assert enqueued == []


def test_tasks_accept_the_dispatcher_kwargs_and_run_the_pipeline(wired: Any) -> None:
    wired.source.put(S.TENANT_A, S.facts(DOC, [S.version(1)]), {1: S.circular_docx()})
    tenant = str(S.TENANT_A)
    payload = {"document_id": str(DOC), "version_id": str(S.version(1).id)}
    assert tasks.ingest_version(tenant_id=tenant, event_id="e1", payload=payload) == "indexed"
    assert wired.store.rows
    wired.source.update(S.TENANT_A, DOC, acl=())
    assert tasks.refresh_acl(tenant_id=tenant, event_id="e2", payload={"document_id": str(DOC)})
    assert {r.acl.roles for r in wired.store.rows} == {()}
    tasks.remove_document(tenant_id=tenant, event_id="e3", payload={"document_id": str(DOC)})
    assert wired.store.rows == []


def test_unwired_pipeline_fails_loudly() -> None:
    runtime.configure(None)
    with pytest.raises(runtime.PipelineNotConfigured):
        tasks.ingest_version(
            tenant_id=str(S.TENANT_A),
            event_id="e",
            payload={"document_id": str(DOC), "version_id": str(DOC)},
        )


def test_transient_failures_retry_then_give_up(monkeypatch: pytest.MonkeyPatch, wired: Any) -> None:
    def broken(*_: Any) -> str:
        raise ConnectionError("embeddings provider unavailable")

    monkeypatch.setattr(wired.pipeline, "ingest", broken)
    kwargs: dict[str, Any] = {
        "tenant_id": str(S.TENANT_A),
        "event_id": "e",
        "payload": {"document_id": str(DOC), "version_id": str(DOC)},
    }
    # Called directly, Celery re-raises instead of scheduling the retry.
    with pytest.raises(ConnectionError):
        tasks.ingest_version(**kwargs)
    last = tasks.ingest_version.apply(kwargs=kwargs, retries=tasks.MAX_RETRIES)
    assert last.get() == "failed"


def test_tasks_route_to_the_ingest_queue() -> None:
    for task in (tasks.ingest_version, tasks.refresh_acl, tasks.remove_document):
        assert getattr(task, "queue", None) == "ingest"
        assert task.name.startswith("knowledge.")
