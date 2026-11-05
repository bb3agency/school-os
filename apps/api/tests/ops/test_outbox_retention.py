"""Outbox retention (lead decision D51; FR-OPS-004, migration 0072_outbox_retention).

Delivered (dispatched) events are deleted 30 days after delivery by the nightly ops purge, per
school in its own ``tenant_session`` (active, suspended and offboarding schools). Undelivered
events are kept. The database lets ``sos_app`` delete dispatched rows only. A school that fails
does not stop the others.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.core.db import tenant_session
from app.ops import repository as ops_repo
from app.ops import service, tasks
from app.ops.config import load_config, parse_config
from app.tenancy import service as tenancy

REVISION = "0072_outbox_retention"
PREVIOUS = "0071_subscription_billing_anchor"
DB = "schoolos_outbox_retention"


@pytest.fixture
def engines(app_engine: Engine, platform_engine: Engine) -> None:
    """Bind core.db engines."""


def _tenant(admin: Engine, status: str = "active") -> uuid.UUID:
    tid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text("INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'S', :s)"),
            {"i": tid, "c": f"t-{uuid.uuid4().hex[:12]}", "s": status},
        )
    return tid


def _event(admin: Engine, tenant: uuid.UUID, dispatched_days_ago: int | None) -> uuid.UUID:
    """One synthetic event; ``None`` = not yet dispatched (created 90 days ago)."""
    eid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.outbox (id, tenant_id, event_type, payload, created_at, "
                "dispatched_at) VALUES (:i, :t, 'test.event', CAST(:p AS jsonb), "
                "now() - interval '90 days', "
                "CASE WHEN CAST(:d AS int) IS NULL THEN NULL "
                "ELSE now() - make_interval(days => CAST(:d AS int)) END)"
            ),
            {
                "i": eid,
                "t": tenant,
                "p": json.dumps({"resource_id": str(eid)}),
                "d": dispatched_days_ago,
            },
        )
    return eid


def _ids(admin: Engine, tenant: uuid.UUID) -> set[uuid.UUID]:
    with admin.connect() as c:
        return set(
            c.execute(
                text("SELECT id FROM ops.outbox WHERE tenant_id = :t"), {"t": tenant}
            ).scalars()
        )


def test_D51_retention_period_comes_from_versioned_config() -> None:
    assert load_config().outbox_delivered_retention_days == 30
    with pytest.raises(ValueError, match="at least 1"):
        parse_config({"outbox": {"delivered_retention_days": 0}})


@pytest.mark.db
def test_FR_OPS_004_D51_purge_deletes_only_delivered_events_older_than_30_days(
    admin_engine: Engine, engines: None
) -> None:
    tenant, other = _tenant(admin_engine), _tenant(admin_engine)
    old = _event(admin_engine, tenant, 31)
    recent = _event(admin_engine, tenant, 29)
    pending = _event(admin_engine, tenant, None)
    other_old = _event(admin_engine, other, 31)

    with tenant_session(tenant) as s:
        assert ops_repo.purge_delivered_outbox(s, older_than_days=30) == 1

    assert _ids(admin_engine, tenant) == {recent, pending}
    assert old not in _ids(admin_engine, tenant)
    assert _ids(admin_engine, other) == {other_old}  # other school untouched


@pytest.mark.db
def test_FR_OPS_004_D51_nightly_run_covers_active_suspended_and_offboarding_schools(
    admin_engine: Engine, engines: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    schools = {
        status: _tenant(admin_engine, status) for status in ("active", "suspended", "offboarding")
    }
    kept = {status: _event(admin_engine, t, None) for status, t in schools.items()}
    for t in schools.values():
        _event(admin_engine, t, 45)
    asked: list[Any] = []
    real = tenancy.list_tenant_ids

    def listing(session: Any, statuses: Any) -> list[uuid.UUID]:
        asked.append(statuses)
        return [t for t in real(session, statuses) if t in schools.values()]

    monkeypatch.setattr("app.tenancy.service.list_tenant_ids", listing)
    result = service.purge_delivered_outbox()
    assert asked == [("active", "suspended", "offboarding")]
    assert (result.tenants, result.purged, result.failed) == (3, 3, 0)
    for status, t in schools.items():
        assert _ids(admin_engine, t) == {kept[status]}


@pytest.mark.db
def test_FR_OPS_004_D51_app_role_cannot_delete_undelivered_events(
    admin_engine: Engine, engines: None
) -> None:
    """Defence in depth (0072 policy ``outbox_delete_dispatched``): even an unfiltered DELETE as
    ``sos_app`` removes only dispatched rows of the current school."""
    tenant, other = _tenant(admin_engine), _tenant(admin_engine)
    pending = _event(admin_engine, tenant, None)
    _event(admin_engine, tenant, 1)
    other_ids = {_event(admin_engine, other, 60), _event(admin_engine, other, None)}
    with tenant_session(tenant) as s:
        s.execute(text("DELETE FROM ops.outbox"))
    assert _ids(admin_engine, tenant) == {pending}
    assert _ids(admin_engine, other) == other_ids


@pytest.mark.db
def test_FR_OPS_004_D51_outbox_purge_survives_a_failing_school(
    admin_engine: Engine, engines: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _tenant(admin_engine), _tenant(admin_engine)
    _event(admin_engine, first, 40)
    kept_second = _event(admin_engine, second, None)
    _event(admin_engine, second, 40)
    monkeypatch.setattr("app.tenancy.service.list_tenant_ids", lambda s, statuses: [first, second])
    real = ops_repo.purge_delivered_outbox
    seen: list[uuid.UUID] = []

    def purge(session: Any, *, older_than_days: int) -> int:
        tid = ops_repo.current_tenant(session)
        seen.append(tid)
        if tid == first:
            raise RuntimeError("boom")
        return real(session, older_than_days=older_than_days)

    monkeypatch.setattr(ops_repo, "purge_delivered_outbox", purge)
    result = service.purge_delivered_outbox()
    assert seen == [first, second]
    assert (result.tenants, result.purged, result.failed) == (2, 1, 1)
    assert len(_ids(admin_engine, first)) == 1  # rolled back, due again next run
    assert _ids(admin_engine, second) == {kept_second}


def test_D51_nightly_ops_task_runs_both_purges(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> int:
        raise RuntimeError("listing failed")

    monkeypatch.setattr(service, "purge_idempotency_keys", broken)
    monkeypatch.setattr(
        service, "purge_delivered_outbox", lambda: service.OutboxPurgeResult(2, 5, 1)
    )
    assert tasks.purge_idempotency_keys.run() == {
        "purged": 0,
        "outbox_tenants": 2,
        "outbox_purged": 5,
        "outbox_failed": 1,
    }


def _grants(engine: Engine) -> set[str]:
    with engine.connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = 'sos_app' AND table_schema = 'ops' AND table_name = 'outbox'"
                )
            ).scalars()
        )


def _policies(engine: Engine) -> set[str]:
    with engine.connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT policyname FROM pg_policies WHERE schemaname = 'ops' "
                    "AND tablename = 'outbox'"
                )
            ).scalars()
        )


def _index(engine: Engine) -> int:
    with engine.connect() as c:
        return int(
            c.execute(
                text(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname = 'ops' "
                    "AND indexname = 'outbox_dispatched'"
                )
            ).scalar_one()
        )


@pytest.mark.db
def test_D51_migration_0072_round_trip(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> None:
    url = test_database.create_fresh(DB)
    cfg = make_alembic_config(url)
    command.upgrade(cfg, REVISION)
    engine = create_engine(make_url(test_database.admin_url).set(database=DB))
    try:
        assert "DELETE" in _grants(engine)
        assert {"tenant_isolation", "definer_access", "outbox_delete_dispatched"} <= _policies(
            engine
        )
        assert _index(engine) == 1
        with engine.connect() as c:
            rls: object = c.execute(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE oid = 'ops.outbox'::regclass"
                )
            ).scalar_one()
        assert rls is True

        command.downgrade(cfg, PREVIOUS)
        assert "DELETE" not in _grants(engine)
        assert "outbox_delete_dispatched" not in _policies(engine)
        assert _index(engine) == 0

        command.upgrade(cfg, "head")
        assert "DELETE" in _grants(engine)
        assert "outbox_delete_dispatched" in _policies(engine)
    finally:
        engine.dispose()
