"""ops service: outbox dispatch, job runs, tenant idempotency keys (FR-OPS-004, docs/09 §2)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.audit.schemas import SummaryError
from app.authz.kv import InMemoryKV
from app.core.db import context_free_session, tenant_session
from app.core.errors import Conflict, ValidationFailed
from app.ops import service
from app.ops.idempotency import KVIdempotencyStore, request_hash
from app.ops.tasks import beat_schedule

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


def _drain() -> None:
    with context_free_session() as s:
        while s.execute(text("SELECT count(*) FROM ops.claim_outbox(500)")).scalar_one():
            pass


@pytest.fixture
def engines(app_engine: Engine, platform_engine: Engine) -> None:
    """Bind core.db engines."""


def test_FR_OPS_004_outbox_payloads_carry_ids_only(admin_engine: Engine, engines: None) -> None:
    tid = _tenant(admin_engine)
    with pytest.raises(SummaryError), tenant_session(tid) as s:
        service.enqueue_event(s, "student.updated", {"student_name": "Synthetic"})
    with tenant_session(tid) as s:
        assert service.enqueue_event(s, "student.updated", {"student_id": str(uuid.uuid4())})


def test_FR_OPS_004_dispatch_routes_by_event_type(
    admin_engine: Engine, engines: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _drain()
    tid = _tenant(admin_engine)
    monkeypatch.setitem(service.OUTBOX_ROUTES, "test.routed", "tests.consumer")
    with tenant_session(tid) as s:
        service.enqueue_event(s, "test.routed", {"resource_id": str(uuid.uuid4())})
        service.enqueue_event(s, "test.unrouted", {"resource_id": str(uuid.uuid4())})
    sent: list[tuple[str, dict[str, Any]]] = []
    result = service.dispatch_outbox(lambda task, kwargs: sent.append((task, kwargs)), batch=50)
    assert (result.claimed, result.sent, result.unrouted) == (2, 1, 1)
    assert sent[0][0] == "tests.consumer"
    assert sent[0][1]["tenant_id"] == str(tid)
    assert service.dispatch_outbox(lambda *_: None).claimed == 0


def test_FR_OPS_004_job_runs_resume_and_die(admin_engine: Engine, engines: None) -> None:
    tid = _tenant(admin_engine)
    with tenant_session(tid) as s:
        job = service.start_job(s, task_name="exports.board", idempotency_key="exports:2026-09")
    assert job.created
    assert job.attempts == 1
    for expected in range(2, service.MAX_ATTEMPTS + 1):
        with tenant_session(tid) as s:
            status = service.fail_job(s, job.id, "synthetic_error")
            again = service.start_job(
                s, task_name="exports.board", idempotency_key="exports:2026-09"
            )
        assert status == "failed"
        assert (again.id, again.created, again.attempts) == (job.id, False, expected)
    with tenant_session(tid) as s:
        assert service.fail_job(s, job.id, "synthetic_error") == "dead"
    with tenant_session(_tenant(admin_engine)) as s:  # other school: its own run, same key
        assert service.start_job(
            s, task_name="exports.board", idempotency_key="exports:2026-09"
        ).created


def test_FR_OPS_004_tenant_idempotency_keys(admin_engine: Engine, engines: None) -> None:
    tid, uid = _tenant(admin_engine), uuid.uuid4()
    digest = request_hash("POST", "/api/v1/exports", b'{"a":1}')
    with tenant_session(tid) as s:
        assert (
            service.begin_idempotent(
                s,
                user_id=uid,
                key="key-000001",
                method="POST",
                route="POST /api/v1/exports",
                request_sha256=digest,
            )
            is None
        )
    with pytest.raises(Conflict, match="still running"), tenant_session(tid) as s:
        service.begin_idempotent(
            s,
            user_id=uid,
            key="key-000001",
            method="POST",
            route="POST /api/v1/exports",
            request_sha256=digest,
        )
    rid = uuid.uuid4()
    with tenant_session(tid) as s:
        service.complete_idempotent(
            s,
            user_id=uid,
            key="key-000001",
            status_code=202,
            resource_type="job",
            resource_id=rid,
            location=f"/api/v1/jobs/{rid}",
        )
    with tenant_session(tid) as s:
        record = service.begin_idempotent(
            s,
            user_id=uid,
            key="key-000001",
            method="POST",
            route="POST /api/v1/exports",
            request_sha256=digest,
        )
    assert record is not None
    assert (record.status_code, record.resource_id) == (202, str(rid))
    other = request_hash("POST", "/api/v1/exports", b'{"a":2}')
    with pytest.raises(ValidationFailed), tenant_session(tid) as s:
        service.begin_idempotent(
            s,
            user_id=uid,
            key="key-000001",
            method="POST",
            route="POST /api/v1/exports",
            request_sha256=other,
        )
    with pytest.raises(ValidationFailed), tenant_session(tid) as s:
        service.begin_idempotent(
            s,
            user_id=uid,
            key="short",
            method="POST",
            route="POST /api/v1/exports",
            request_sha256=digest,
        )
    assert service.purge_idempotency_keys() >= 0


def test_FR_OPS_004_kv_idempotency_store_for_the_control_plane() -> None:
    store = KVIdempotencyStore(InMemoryKV())
    assert store.begin("op:1", "key-000001", "h1") is None
    pending = store.begin("op:1", "key-000001", "h1")
    assert pending is not None
    assert pending.state == "in_progress"
    store.abandon("op:1", "key-000001")
    assert store.begin("op:1", "key-000001", "h1") is None
    assert store.begin("op:2", "key-000001", "h1") is None  # per operator


def test_AA_14_control_plane_in_progress_marker_expires_after_a_minute() -> None:
    """A claim whose request died (or whose ``complete`` failed after the commit) must not
    block retries for a day: the in-progress marker lives 60 s, as on the tenant routes
    (``app.authz.http.PENDING_TTL_S``); a completed record is kept for 24 h."""
    from app.authz.http import PENDING_TTL_S
    from app.ops.idempotency import IdempotencyRecord

    now = [1000.0]
    store = KVIdempotencyStore(InMemoryKV(clock=lambda: now[0]))
    assert store.begin("op:1", "key-000001", "h1") is None
    now[0] += PENDING_TTL_S - 1
    held = store.begin("op:1", "key-000001", "h1")
    assert held is not None
    assert held.state == "in_progress"
    now[0] += 2  # past the pending TTL: the key can be claimed again
    assert store.begin("op:1", "key-000001", "h1") is None
    store.complete("op:1", "key-000001", IdempotencyRecord("h1", "completed", 201))
    now[0] += 23 * 3600
    done = store.begin("op:1", "key-000001", "h1")
    assert done is not None
    assert done.state == "completed"


def test_FR_OPS_004_beat_entries() -> None:
    tasks = {v["task"] for v in beat_schedule().values()}
    assert tasks == {"ops.dispatch_outbox", "ops.purge_idempotency_keys"}
