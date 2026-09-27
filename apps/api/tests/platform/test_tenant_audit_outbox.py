"""Guaranteed school-chain copies of platform actions (FR-AUD-001, CLAUDE.md invariant 7;
ADR-0020, docs/16 §16).

The copy is queued in ``platform.tenant_audit_outbox`` inside the platform transaction and
delivered into the school's own chain exactly once, in order per school, by
``platform.tenant_audit.deliver_pending`` (Celery ``platform.deliver_tenant_audit``).
"""

from __future__ import annotations

import datetime as dt
import threading
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.core.crypto import LocalDevKeyWrapper
from app.core.db import platform_session
from app.platform import repository as repo
from app.platform import tasks, tenant_audit, tenants
from app.platform.common import SYSTEM, Actor
from app.tenancy import service as tenancy

pytestmark = pytest.mark.db

OPERATOR = Actor(uuid.UUID("0190a000-0000-7000-8000-00000000a0a1"), request_id="req-audit-copy")


@pytest.fixture
def school(wrapper: LocalDevKeyWrapper, platform_engine: Engine, app_engine: Engine) -> uuid.UUID:
    return tenancy.provision_tenant(
        code=f"s-{uuid.uuid4().hex[:12]}", name="Synthetic Outbox School", wrapper=wrapper
    ).tenant_id


@pytest.fixture
def make_school(
    wrapper: LocalDevKeyWrapper, platform_engine: Engine, app_engine: Engine
) -> Callable[[], uuid.UUID]:
    def _make() -> uuid.UUID:
        return tenancy.provision_tenant(
            code=f"s-{uuid.uuid4().hex[:12]}", name="Synthetic Outbox School", wrapper=wrapper
        ).tenant_id

    return _make


def _enqueue(tenant_id: uuid.UUID, action: str, summary: dict[str, Any] | None = None) -> uuid.UUID:
    with platform_session() as s:
        return tenant_audit.enqueue(s, tenant_id, OPERATOR, action, summary or {"step": 1})


def _school_events(admin: Engine, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT seq, action, actor_type, actor_id, resource_type, resource_id, "
                    "summary, request_id FROM audit.events "
                    "WHERE tenant_id = :t AND actor_type = 'platform' ORDER BY seq"
                ),
                {"t": tenant_id},
            )
        ]


def _outbox(admin: Engine, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT id, seq, action, delivered_at, attempts, last_error "
                    "FROM platform.tenant_audit_outbox WHERE tenant_id = :t ORDER BY seq"
                ),
                {"t": tenant_id},
            )
        ]


def _key(event: dict[str, Any]) -> str:
    return str(event["summary"]["platform_event_id"])


# --- queueing is atomic with the platform change ---------------------------------------------


def test_FR_AUD_001_copy_is_queued_in_the_platform_transaction(
    school: uuid.UUID, admin_engine: Engine
) -> None:
    class Boom(Exception):
        pass

    def failing_change() -> None:
        with platform_session() as s:
            tenant_audit.enqueue(s, school, OPERATOR, "tenant.suspended", {"cause": "billing"})
            raise Boom  # the platform change fails after queueing: nothing may remain

    with pytest.raises(Boom):
        failing_change()
    assert _outbox(admin_engine, school) == []
    event_id = _enqueue(school, "tenant.suspended", {"cause": "billing"})
    rows = _outbox(admin_engine, school)
    assert [(r["id"], r["delivered_at"], r["attempts"]) for r in rows] == [(event_id, None, 0)]
    assert _school_events(admin_engine, school) == []  # not delivered yet


def test_FR_AUD_001_delivered_event_carries_ids_only(
    school: uuid.UUID, admin_engine: Engine
) -> None:
    event_id = _enqueue(school, "tenant.activated", {"from": "provisioning", "to": "active"})
    result = tenant_audit.deliver_pending(tenant_id=school)
    assert (result.delivered, result.already_present, result.failed) == (1, 0, 0)
    (event,) = _school_events(admin_engine, school)
    assert event["action"] == "tenant.activated"
    assert event["actor_type"] == "platform"
    assert event["actor_id"] == OPERATOR.operator_id
    assert event["request_id"] == "req-audit-copy"
    assert (event["resource_type"], event["resource_id"]) == ("tenant", school)
    assert event["summary"] == {
        "from": "provisioning",
        "to": "active",
        "platform_event_id": str(event_id),
    }
    (row,) = _outbox(admin_engine, school)
    assert row["delivered_at"] is not None
    assert row["last_error"] is None


@pytest.mark.parametrize(
    "summary",
    [
        {"name": "Synthetic Owner"},
        {"owner_email": "owner@example.test"},
        {"reference": "9876543210"},
        {"contact": "owner@example.test"},
    ],
)
def test_FR_AUD_001_personal_data_is_refused_before_anything_is_written(
    school: uuid.UUID, admin_engine: Engine, summary: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError), platform_session() as s:
        tenant_audit.enqueue(s, school, OPERATOR, "tenant.suspended", summary)
    assert _outbox(admin_engine, school) == []


# --- exactly once ------------------------------------------------------------------------------


def test_FR_AUD_001_crash_after_school_commit_is_delivered_exactly_once_on_retry(
    school: uuid.UUID, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulated crash between the school-chain commit and marking the row delivered."""
    _enqueue(school, "tenant.suspended", {"cause": "security"})

    def crash(_session: Any, _event_id: uuid.UUID) -> None:
        raise RuntimeError("worker killed")

    with monkeypatch.context() as m:
        m.setattr(repo, "mark_tenant_audit_delivered", crash)
        with pytest.raises(RuntimeError):
            tenant_audit.deliver_pending(tenant_id=school)
    assert len(_school_events(admin_engine, school)) == 1  # written ...
    assert _outbox(admin_engine, school)[0]["delivered_at"] is None  # ... but not marked
    retry = tenant_audit.deliver_pending(tenant_id=school)
    assert (retry.delivered, retry.already_present) == (0, 1)
    again = tenant_audit.deliver_pending(tenant_id=school)
    assert (again.delivered, again.already_present) == (0, 0)
    assert len(_school_events(admin_engine, school)) == 1
    assert _outbox(admin_engine, school)[0]["delivered_at"] is not None


def test_FR_AUD_001_crash_before_delivery_is_delivered_by_the_task(
    make_school: Callable[[], uuid.UUID],
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The platform action commits and the process dies before the inline delivery."""
    tid = make_school()
    with platform_session() as s:
        tenancy.activate_tenant(s, tid)
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.deployments (id, tenant_id, tenant_code, school_name, "
                "boards, mode, tenant_status, status) VALUES (:i, :t, :c, "
                "'Synthetic Outbox School', '{}', 'shared', 'active', 'healthy')"
            ),
            {"i": uuid.uuid4(), "t": tid, "c": f"o-{uuid.uuid4().hex[:10]}"},
        )
    with monkeypatch.context() as m:
        m.setattr(tenant_audit, "deliver_now", lambda _tenant_id: None)
        tenants.suspend(OPERATOR, tid, "security incident")
    assert _school_events(admin_engine, tid) == []
    with admin_engine.connect() as c:
        status = c.execute(text("SELECT status FROM core.tenants WHERE id = :t"), {"t": tid})
        assert status.scalar_one() == "suspended"
    out = tasks.deliver_tenant_audit.apply().get()
    assert out["delivered"] >= 1
    (event,) = _school_events(admin_engine, tid)
    assert event["action"] == "tenant.suspended"
    assert event["summary"]["from"] == "active"
    assert event["summary"]["to"] == "suspended"


def test_FR_AUD_001_concurrent_deliverers_never_duplicate_and_keep_order(
    make_school: Callable[[], uuid.UUID], admin_engine: Engine
) -> None:
    schools = [make_school() for _ in range(3)]
    queued: dict[uuid.UUID, list[uuid.UUID]] = {t: [] for t in schools}
    for step in range(4):
        for tid in schools:  # interleaved across schools
            queued[tid].append(_enqueue(tid, f"tenant.step_{'abcd'[step]}", {"step": step}))
    errors: list[BaseException] = []

    def run() -> None:
        try:
            for tid in schools:
                tenant_audit.deliver_pending(tenant_id=tid)
            for tid in reversed(schools):
                tenant_audit.deliver_pending(tenant_id=tid)
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    for tid in schools:
        events = _school_events(admin_engine, tid)
        assert [_key(e) for e in events] == [str(i) for i in queued[tid]]
        assert [e["summary"]["step"] for e in events] == [0, 1, 2, 3]
        assert all(r["delivered_at"] is not None for r in _outbox(admin_engine, tid))


# --- ordering per school -----------------------------------------------------------------------


def test_FR_AUD_001_a_failing_event_blocks_only_its_own_school(
    make_school: Callable[[], uuid.UUID],
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, b = make_school(), make_school()
    a1 = _enqueue(a, "tenant.suspended", {"step": 1})
    b1 = _enqueue(b, "tenant.suspended", {"step": 1})
    a2 = _enqueue(a, "tenant.reactivated", {"step": 2})
    b2 = _enqueue(b, "tenant.reactivated", {"step": 2})
    real = tenant_audit._copy_into_school_chain

    def flaky(row: Any) -> bool:
        if row["id"] == a1:
            raise DBAPIError("INSERT", {}, Exception("connection lost"))
        return real(row)

    with monkeypatch.context() as m:
        m.setattr(tenant_audit, "_copy_into_school_chain", flaky)
        for tid in (a, b):
            tenant_audit.deliver_pending(tenant_id=tid)
    assert _school_events(admin_engine, a) == []  # a2 waits behind a1
    assert [_key(e) for e in _school_events(admin_engine, b)] == [str(b1), str(b2)]
    head = _outbox(admin_engine, a)[0]
    assert (head["id"], head["attempts"], head["last_error"]) == (a1, 1, "db_error")
    result = tenant_audit.deliver_pending(tenant_id=a)
    assert (result.delivered, result.failed) == (2, 0)
    assert [_key(e) for e in _school_events(admin_engine, a)] == [str(a1), str(a2)]


class _Recorder:
    def __init__(self) -> None:
        self.errors: list[tuple[str, dict[str, Any]]] = []

    def error(self, event: str, **fields: Any) -> None:
        self.errors.append((event, fields))

    def info(self, event: str, **fields: Any) -> None:
        pass

    def warning(self, event: str, **fields: Any) -> None:
        pass


def test_FR_AUD_001_backlog_alert_after_the_configured_minutes(
    school: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(tenant_audit, "log", recorder)
    _enqueue(school, "tenant.suspended", {"cause": "billing"})
    assert tenant_audit.check_backlog() >= 1
    assert recorder.errors == []  # just queued: no alert
    later = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=16)
    assert tenant_audit.check_backlog(now=later) >= 1
    assert [e for e, _ in recorder.errors] == ["platform.tenant_audit.backlog"]
    assert set(recorder.errors[0][1]) <= {"count", "outcome"}  # counts only, no IDs or data
    tenant_audit.deliver_pending()
    with platform_session() as s:
        count, _oldest = repo.pending_tenant_audit(s)
    assert count == 0


# --- grants ------------------------------------------------------------------------------------


def test_ADR_0020_outbox_grants(school: uuid.UUID, app_engine: Engine) -> None:
    event_id = _enqueue(school, "tenant.suspended", {"cause": "billing"})
    for sql in (
        "DELETE FROM platform.tenant_audit_outbox WHERE id = :i",
        "UPDATE platform.tenant_audit_outbox SET action = 'tenant.deleted' WHERE id = :i",
        "UPDATE platform.tenant_audit_outbox SET summary = '{}' WHERE id = :i",
    ):
        with pytest.raises(DBAPIError) as exc, platform_session() as s:
            s.execute(text(sql), {"i": event_id})
        assert "permission denied" in str(exc.value)
    with pytest.raises(DBAPIError) as exc, app_engine.connect() as c:
        c.execute(text("SELECT count(*) FROM platform.tenant_audit_outbox"))
    assert "permission denied" in str(exc.value)


def test_ADR_0020_dedicated_schools_queue_nothing_in_the_control_plane(
    make_school: Callable[[], uuid.UUID], admin_engine: Engine
) -> None:
    """Dedicated schools' chains live on their host: control-plane lifecycle queues nothing."""
    tid = uuid.uuid4()
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.deployments (id, tenant_id, tenant_code, school_name, "
                "boards, mode, tenant_status, status, heartbeat_key_id, heartbeat_key_ciphertext) "
                "VALUES (:i, :t, :c, 'Synthetic Dedicated School', '{}', 'dedicated', 'active', "
                "'healthy', 'hb-synthetic', '\\x00')"
            ),
            {"i": uuid.uuid4(), "t": tid, "c": f"d-{uuid.uuid4().hex[:10]}"},
        )
    tenants.suspend(OPERATOR, tid, "school request")
    assert _outbox(admin_engine, tid) == []


def test_ADR_0020_system_actor_has_no_actor_id(school: uuid.UUID, admin_engine: Engine) -> None:
    with platform_session() as s:
        tenant_audit.enqueue(s, school, SYSTEM, "tenant.activated", {"to": "active"})
    tenant_audit.deliver_pending(tenant_id=school)
    (event,) = _school_events(admin_engine, school)
    assert event["actor_id"] is None
    assert event["request_id"] is None


@pytest.fixture(autouse=True)
def _drain() -> Iterator[None]:
    """Leave no queued rows behind for other tests' global deliveries."""
    yield
    tenant_audit.deliver_pending()
