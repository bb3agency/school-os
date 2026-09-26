"""Tenant-side ops tables (migration 0006_ops): RLS, the 8-hour break-glass window and the
``ops.claim_outbox`` definer function under concurrency (FR-OPS-004, SEC-001, ADR-0013)."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.core.db import context_free_session, platform_session, tenant_session

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


def _enqueue(tenant: uuid.UUID, n: int) -> set[uuid.UUID]:
    ids = {uuid.uuid4() for _ in range(n)}
    with tenant_session(tenant) as s:
        for i in ids:
            s.execute(
                text(
                    "INSERT INTO ops.outbox (id, tenant_id, event_type, payload) "
                    "VALUES (:i, :t, 'test.event', CAST(:p AS jsonb))"
                ),
                {"i": i, "t": tenant, "p": json.dumps({"resource_id": str(i)})},
            )
    return ids


@pytest.fixture
def engines(app_engine: Engine, platform_engine: Engine) -> None:
    """Bind core.db engines."""


def test_FR_OPS_004_outbox_rows_are_tenant_isolated(admin_engine: Engine, engines: None) -> None:
    a, b = _tenant(admin_engine), _tenant(admin_engine)
    _drain()
    _enqueue(a, 2)
    with tenant_session(b) as s:
        assert s.execute(text("SELECT count(*) FROM ops.outbox")).scalar_one() == 0
    with pytest.raises(ProgrammingError, match="row-level security"), tenant_session(b) as s:
        s.execute(
            text(
                "INSERT INTO ops.outbox (id, tenant_id, event_type, payload) "
                "VALUES (gen_random_uuid(), :t, 'test.event', '{}')"
            ),
            {"t": a},
        )
    with pytest.raises(ProgrammingError, match="permission denied"), tenant_session(a) as s:
        s.execute(text("UPDATE ops.outbox SET dispatched_at = now()"))
    _drain()


def test_FR_OPS_004_claim_outbox_skip_locked_under_concurrency(
    admin_engine: Engine, engines: None
) -> None:
    tenants = [_tenant(admin_engine) for _ in range(3)]
    _drain()
    expected: set[uuid.UUID] = set()
    for t in tenants:
        expected |= _enqueue(t, 40)
    claimed: list[uuid.UUID] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            while True:
                with context_free_session() as s:
                    rows = s.execute(text("SELECT id FROM ops.claim_outbox(7)")).scalars().all()
                if not rows:
                    return
                with lock:
                    claimed.extend(rows)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert len(claimed) == len(set(claimed)), "a row was claimed twice"
    assert set(claimed) == expected
    with tenant_session(tenants[0]) as s:
        pending = s.execute(
            text("SELECT count(*) FROM ops.outbox WHERE dispatched_at IS NULL")
        ).scalar_one()
    assert pending == 0


def test_ADR_0013_claim_outbox_denied_to_platform(engines: None) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), platform_session() as s:
        s.execute(text("SELECT * FROM ops.claim_outbox(1)"))


def test_SEC_001_break_glass_grant_at_most_8_hours(admin_engine: Engine, engines: None) -> None:
    tenant = _tenant(admin_engine)
    start = datetime.now(UTC)
    insert = text(
        "INSERT INTO ops.break_glass_grants (id, tenant_id, platform_user_id, reason, scope, "
        "status, starts_at, expires_at) VALUES (gen_random_uuid(), :t, gen_random_uuid(), "
        "'synthetic support reason', '{}', 'active', :s, :e)"
    )
    with tenant_session(tenant) as s:
        s.execute(insert, {"t": tenant, "s": start, "e": start + timedelta(hours=8)})
    with (
        pytest.raises(IntegrityError, match="break_glass_grants_max_8h"),
        tenant_session(tenant) as s,
    ):
        s.execute(insert, {"t": tenant, "s": start, "e": start + timedelta(hours=8, minutes=1)})


def test_FR_OPS_004_job_runs_idempotency_key_unique_per_tenant(
    admin_engine: Engine, engines: None
) -> None:
    tenant = _tenant(admin_engine)
    insert = text(
        "INSERT INTO ops.job_runs (id, tenant_id, task_name, idempotency_key, status) "
        "VALUES (gen_random_uuid(), :t, 'test.job', 'k-1', 'pending')"
    )
    with tenant_session(tenant) as s:
        s.execute(insert, {"t": tenant})
    with pytest.raises(IntegrityError, match="job_runs_tenant_key"), tenant_session(tenant) as s:
        s.execute(insert, {"t": tenant})
    other = _tenant(admin_engine)
    with tenant_session(other) as s:
        s.execute(insert, {"t": other})
