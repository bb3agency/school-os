"""Queued work of a suspended school is held, not run and not dropped (audit 2026-10-05 A-12).

Suspension is enforced on HTTP routes by the resolver (BR-08). Before this fix, jobs queued
before a suspension still ran after it (imports, OCR, LLM ingestion and summaries, PDF
rendering, invitation emails) and Celery retries stretched the window. Every outbox consumer
now has the ``TenantTask`` base: it checks the school before each delivery and retry and sends
the same message again later while the school is suspended or offboarding. Synthetic data only.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from celery import Celery
from sqlalchemy import Engine, text

from app.ops import service
from app.ops.service import RUN_WHILE_SUSPENDED, TenantTask

pytestmark = pytest.mark.db


def _tenant(admin: Engine, status: str = "active") -> uuid.UUID:
    tid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'S', 'active')"
            ),
            {"i": tid, "c": f"t-{uuid.uuid4().hex[:12]}"},
        )
    _set_status(admin, tid, status)
    return tid


def _set_status(admin: Engine, tid: uuid.UUID, status: str) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE core.tenants SET status = :s WHERE id = :i"), {"s": status, "i": tid}
        )


@pytest.fixture
def engines(app_engine: Engine, platform_engine: Engine) -> None:
    """Bind core.db engines."""


def test_A_12_every_outbox_consumer_checks_the_school_first() -> None:
    from sos_worker.celery_app import celery_app

    celery_app.loader.import_default_modules()
    assert service.OUTBOX_ROUTES, "consumers register their routes at import"
    missing = sorted(
        name
        for name in set(service.OUTBOX_ROUTES.values())
        if not isinstance(celery_app.tasks[name], TenantTask)
    )
    assert missing == []
    assert set(celery_app.tasks) >= RUN_WHILE_SUSPENDED


def test_A_12_decision_follows_the_school_status(admin_engine: Engine, engines: None) -> None:
    tid = _tenant(admin_engine)
    assert service.school_work_decision(tid, "extraction.process_batch") == "run"
    _set_status(admin_engine, tid, "suspended")
    for task in ("extraction.process_batch", "imports.commit", "notifications.send_email"):
        assert service.school_work_decision(tid, task) == "hold"
    # The owner's export (BR-08) and security work still run while suspended.
    assert service.school_work_decision(tid, "admin.tenant_export") == "run"
    assert service.school_work_decision(tid, "maintenance.reencrypt_tenant") == "run"
    _set_status(admin_engine, tid, "active")  # reactivation resumes held work
    assert service.school_work_decision(tid, "imports.commit") == "run"
    _set_status(admin_engine, tid, "suspended")
    _set_status(admin_engine, tid, "offboarding")
    assert service.school_work_decision(tid, "imports.commit") == "hold"


def _probe(app: Celery, ran: list[dict[str, Any]]) -> Any:
    @app.task(name="tests.a12_probe", base=TenantTask, shared=False)
    def probe(tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
        ran.append({"tenant_id": tenant_id, "event_id": event_id, "payload": payload})
        return "done"

    del probe
    return app.tasks["tests.a12_probe"]


def test_A_12_held_work_is_sent_again_and_runs_after_reactivation(
    admin_engine: Engine, engines: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = Celery("a12-test", set_as_current=False)
    ran: list[dict[str, Any]] = []
    probe = _probe(app, ran)
    resent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        probe,
        "apply_async",
        lambda args=None, kwargs=None, **opts: resent.append({"kwargs": kwargs, **opts}),
    )
    tid = _tenant(admin_engine, "suspended")
    kwargs = {"tenant_id": str(tid), "event_id": str(uuid.uuid4()), "payload": {"x": "1"}}

    assert probe(**kwargs) is None
    assert ran == []
    assert resent == [{"kwargs": kwargs, "countdown": service.HOLD_COUNTDOWN_SECONDS}]

    _set_status(admin_engine, tid, "active")
    assert probe(**kwargs) == "done"
    assert ran == [kwargs]
    assert len(resent) == 1

    # A deleted school's work is dropped, never re-sent.
    _set_status(admin_engine, tid, "suspended")
    _set_status(admin_engine, tid, "offboarding")
    _set_status(admin_engine, tid, "deleted")
    assert probe(**kwargs) is None
    assert (len(ran), len(resent)) == (1, 1)


def test_A_12_suspended_schools_get_no_task_reminders() -> None:
    from app.circulars import tasks as circular_tasks

    assert circular_tasks.TENANT_STATUSES == ("active",)


def test_L_14_an_event_delivered_twice_has_its_outside_effects_once(
    admin_engine: Engine, engines: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delivery is at least once (L-05, acks_late). A consumer with outside effects (email,
    PDF, OCR or LLM calls) runs once per event: a second delivery of the same ``event_id`` is
    skipped, a failed run frees the event for its retry, and other events still run."""
    from app.authz.kv import InMemoryKV, set_kv_store

    set_kv_store(InMemoryKV())
    try:
        app = Celery("l14-test", set_as_current=False)
        ran: list[dict[str, Any]] = []
        probe = _probe(app, ran)
        monkeypatch.setattr(service, "DEDUPE_BY_EVENT", frozenset({"tests.a12_probe"}))
        tid = _tenant(admin_engine)
        first = {"tenant_id": str(tid), "event_id": str(uuid.uuid4()), "payload": {}}
        assert probe(**first) == "done"
        assert probe(**first) is None  # the same event again: skipped
        assert len(ran) == 1
        other = {**first, "event_id": str(uuid.uuid4())}
        assert probe(**other) == "done"
        assert len(ran) == 2

        failing = {**first, "event_id": str(uuid.uuid4()), "payload": {"fail": "1"}}

        @app.task(name="tests.l14_flaky", base=TenantTask, shared=False)
        def flaky(tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
            ran.append(payload)
            if len([p for p in ran if p == payload]) == 1:
                raise RuntimeError("provider down")
            return "done"

        monkeypatch.setattr(
            service, "DEDUPE_BY_EVENT", frozenset({"tests.a12_probe", "tests.l14_flaky"})
        )
        with pytest.raises(RuntimeError):
            flaky(**failing)
        assert flaky(**failing) == "done"  # the retry is not mistaken for a duplicate
        assert flaky(**failing) is None
    finally:
        set_kv_store(None)


def test_L_14_every_deduplicated_consumer_is_a_registered_outbox_consumer() -> None:
    from sos_worker.celery_app import celery_app

    celery_app.loader.import_default_modules()
    consumers = set(service.OUTBOX_ROUTES.values())
    assert consumers >= service.DEDUPE_BY_EVENT
    assert all(isinstance(celery_app.tasks[n], TenantTask) for n in service.DEDUPE_BY_EVENT)
