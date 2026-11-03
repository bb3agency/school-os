"""Background job logic (deep audit E, 2026-11-03; FR-OPS-004). Synthetic data only.

- A deduplicated outbox consumer whose earlier run died (worker lost: the message is re-queued
  while the lease still says ``running``) must not lose the event: the delivery is deferred and
  runs once the lease has ended; a delivery of a finished event is still skipped (L-14).
- The daily idempotency-key purge keeps going when one school fails.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from celery import Celery
from sqlalchemy import Engine, text

from app.authz.kv import InMemoryKV, kv_store, set_kv_store
from app.ops import service
from app.ops.service import TenantTask

pytestmark = pytest.mark.db


def _tenant(admin: Engine) -> uuid.UUID:
    tid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'S', 'active')"
            ),
            {"i": tid, "c": f"t-{uuid.uuid4().hex[:12]}"},
        )
    return tid


@pytest.fixture
def engines(app_engine: Engine, platform_engine: Engine) -> None:
    """Bind core.db engines."""


def test_FR_OPS_004_a_delivery_after_a_lost_worker_is_deferred_not_dropped(
    admin_engine: Engine, engines: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_kv_store(InMemoryKV())
    try:
        app = Celery("e-dedupe-test", set_as_current=False)
        ran: list[str] = []

        @app.task(name="tests.e_probe", base=TenantTask, shared=False)
        def probe(tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
            ran.append(event_id)
            return "done"

        task = app.tasks["tests.e_probe"]
        resent: list[dict[str, Any]] = []
        monkeypatch.setattr(
            task,
            "apply_async",
            lambda args=None, kwargs=None, **opts: resent.append({"kwargs": kwargs, **opts}),
        )
        monkeypatch.setattr(service, "DEDUPE_BY_EVENT", frozenset({"tests.e_probe"}))
        tid = _tenant(admin_engine)
        kwargs = {"tenant_id": str(tid), "event_id": str(uuid.uuid4()), "payload": {}}

        # The first run claimed the event, then its worker died: the lease stays "running".
        assert service.claim_event("tests.e_probe", kwargs["event_id"])
        assert task(**kwargs) is None
        assert ran == []
        assert resent == [{"kwargs": kwargs, "countdown": service.HOLD_COUNTDOWN_SECONDS}]

        # The lease ends (TTL): the deferred delivery runs the event, once.
        kv_store().delete(service._event_key("tests.e_probe", kwargs["event_id"]))
        assert task(**kwargs) == "done"
        assert ran == [kwargs["event_id"]]

        # A delivery of a finished event is skipped, not deferred.
        assert task(**kwargs) is None
        assert ran == [kwargs["event_id"]]
        assert len(resent) == 1
    finally:
        set_kv_store(None)


def test_FR_OPS_004_idempotency_purge_survives_a_failing_school(
    admin_engine: Engine, engines: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _tenant(admin_engine), _tenant(admin_engine)
    monkeypatch.setattr(service.tenancy, "list_tenant_ids", lambda s, statuses: [first, second])
    seen: list[uuid.UUID] = []

    def purge(session: Any) -> int:
        tid = service.repo.current_tenant(session)
        seen.append(tid)
        if tid == first:
            raise RuntimeError("boom")
        return 2

    monkeypatch.setattr(service.repo, "idem_purge_expired", purge)
    assert service.purge_idempotency_keys() == 2
    assert seen == [first, second]
