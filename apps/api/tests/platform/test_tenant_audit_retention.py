"""School-chain copy retention (lead decision D51 for the control plane; FR-AUD-001, ADR-0020,
migration 0074_tenant_audit_retention).

Delivered copies in ``platform.tenant_audit_outbox`` are deleted 30 days after ``delivered_at`` by
the nightly ``platform.purge_tenant_audit`` (both deployment modes). Undelivered copies are always
kept: the database refuses to delete them, even for an unfiltered ``DELETE``. A purged copy is
never delivered again (the deliverer claims undelivered rows only, and the school side dedupes on
``platform_event_id``).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core.config import DeploymentMode, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.db import platform_session
from app.platform import tasks, tenant_audit
from app.platform.common import Actor
from app.tenancy import service as tenancy

REVISION = "0074_tenant_audit_retention"
PREVIOUS = "0073_admission_no_ci"
DB = "schoolos_tenant_audit_retention"
OPERATOR = Actor(uuid.UUID("0190a000-0000-7000-8000-00000000a0b2"), request_id="req-retention")


@pytest.fixture
def school(
    wrapper: LocalDevKeyWrapper, platform_engine: Engine, app_engine: Engine
) -> Iterator[uuid.UUID]:
    yield tenancy.provision_tenant(
        code=f"s-{uuid.uuid4().hex[:12]}", name="Synthetic Retention School", wrapper=wrapper
    ).tenant_id
    tenant_audit.deliver_pending()  # leave no queued rows behind for other tests


def _enqueue(tenant_id: uuid.UUID, action: str = "tenant.suspended") -> uuid.UUID:
    with platform_session() as s:
        return tenant_audit.enqueue(s, tenant_id, OPERATOR, action, {"cause": "billing"})


def _age(admin: Engine, event_id: uuid.UUID, delivered_days_ago: int) -> None:
    """Pretend the copy was delivered ``delivered_days_ago`` days ago (superuser with user
    triggers off, test only: ``delivered_at`` is set once since D68, migration 0079)."""
    with admin.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(
            text(
                "UPDATE platform.tenant_audit_outbox SET delivered_at = "
                "now() - make_interval(days => :d), created_at = "
                "now() - make_interval(days => :d + 1) WHERE id = :i"
            ),
            {"i": event_id, "d": delivered_days_ago},
        )


def _ids(admin: Engine, tenant_id: uuid.UUID) -> set[uuid.UUID]:
    with admin.connect() as c:
        return set(
            c.execute(
                text("SELECT id FROM platform.tenant_audit_outbox WHERE tenant_id = :t"),
                {"t": tenant_id},
            ).scalars()
        )


def _school_copies(admin: Engine, tenant_id: uuid.UUID, event_id: uuid.UUID) -> int:
    with admin.connect() as c:
        return int(
            c.execute(
                text(
                    "SELECT count(*) FROM audit.events WHERE tenant_id = :t "
                    "AND summary ->> 'platform_event_id' = :k"
                ),
                {"t": tenant_id, "k": str(event_id)},
            ).scalar_one()
        )


def test_D51_tenant_audit_retention_comes_from_billing_yaml() -> None:
    assert tenant_audit.retention_days() == 30
    with pytest.raises(ValueError, match="at least 1"):
        tenant_audit.retention_days({"tenant_audit": {"delivered_retention_days": 0}})


def test_D51_purge_task_is_scheduled_nightly_in_both_modes() -> None:
    for mode in (DeploymentMode.SHARED, DeploymentMode.DEDICATED):
        entry = tasks.beat_schedule(Settings(deployment_mode=mode))["platform-purge-tenant-audit"]
        assert entry["task"] == "platform.purge_tenant_audit"
        assert entry["options"] == {"queue": "maintenance"}
        assert entry["schedule"].hour == {21}  # 02:50 IST
        assert entry["schedule"].minute == {20}


@pytest.mark.db
def test_FR_AUD_001_D51_purge_deletes_only_delivered_copies_older_than_30_days(
    school: uuid.UUID, admin_engine: Engine
) -> None:
    old, recent = _enqueue(school), _enqueue(school, "tenant.reactivated")
    tenant_audit.deliver_pending(tenant_id=school)
    pending = _enqueue(school, "tenant.activated")  # stays undelivered (not delivered here)
    _age(admin_engine, old, 31)
    _age(admin_engine, recent, 29)

    out = tasks.purge_tenant_audit.apply().get()

    assert out["purged"] >= 1
    assert set(out) == {"purged", "kept_unconfirmed"}
    assert _ids(admin_engine, school) == {recent, pending}
    with admin_engine.connect() as c:
        audited = c.execute(
            text(
                "SELECT actor_type, summary FROM platform.audit_events "
                "WHERE action = 'tenant_audit.copies_purged' ORDER BY occurred_at DESC LIMIT 1"
            )
        ).one()
    assert audited.actor_type == "system"
    assert set(audited.summary) == {"count", "kept_unconfirmed", "retention_days"}  # counts only


@pytest.mark.db
def test_FR_AUD_001_D51_undelivered_copies_are_refused_even_by_an_unfiltered_delete(
    school: uuid.UUID, admin_engine: Engine
) -> None:
    delivered = _enqueue(school)
    tenant_audit.deliver_pending(tenant_id=school)
    pending = _enqueue(school, "tenant.reactivated")
    _age(admin_engine, delivered, 60)
    with pytest.raises(DBAPIError) as exc, platform_session() as s:
        s.execute(text("DELETE FROM platform.tenant_audit_outbox"))
    assert "undelivered school-chain copies cannot be deleted" in str(exc.value)
    assert {delivered, pending} <= _ids(admin_engine, school)  # the whole statement rolled back


@pytest.mark.db
def test_FR_AUD_001_D51_a_purged_copy_is_never_delivered_again(
    school: uuid.UUID, admin_engine: Engine
) -> None:
    """The deliverer claims undelivered rows only, and the school side dedupes on
    ``platform_event_id``: purging a delivered copy cannot duplicate the school's event."""
    event_id = _enqueue(school)
    assert tenant_audit.deliver_pending(tenant_id=school).delivered == 1
    assert _school_copies(admin_engine, school, event_id) == 1
    _age(admin_engine, event_id, 31)

    assert tenant_audit.purge_delivered().purged >= 1
    assert event_id not in _ids(admin_engine, school)

    again = tenant_audit.deliver_pending(tenant_id=school)
    assert (again.delivered, again.already_present, again.failed) == (0, 0, 0)
    assert _school_copies(admin_engine, school, event_id) == 1


class _Recorder:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict[str, Any]]] = []

    def _add(self, event: str, **fields: Any) -> None:
        self.lines.append((event, fields))

    info = warning = error = _add


@pytest.mark.db
def test_FR_AUD_001_D68_purge_keeps_a_delivered_copy_missing_from_the_school_chain(
    school: uuid.UUID, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy marked delivered without being written into the school's chain is never purged:
    the purge confirms ``platform_event_id`` in that school's chain first (invariant 7)."""
    confirmed = _enqueue(school)
    tenant_audit.deliver_pending(tenant_id=school)
    unconfirmed = _enqueue(school, "tenant.reactivated")
    with platform_session() as s:  # the control plane's role claims a delivery it never made
        s.execute(
            text(
                "UPDATE platform.tenant_audit_outbox SET delivered_at = clock_timestamp() "
                "WHERE id = :i"
            ),
            {"i": unconfirmed},
        )
    _age(admin_engine, confirmed, 31)
    _age(admin_engine, unconfirmed, 31)
    assert _school_copies(admin_engine, school, unconfirmed) == 0
    recorder = _Recorder()
    monkeypatch.setattr(tenant_audit, "log", recorder)

    result = tenant_audit.purge_delivered()

    assert result.purged >= 1
    assert result.kept_unconfirmed >= 1
    remaining = _ids(admin_engine, school)
    assert confirmed not in remaining
    assert unconfirmed in remaining
    kept = [f for e, f in recorder.lines if e == "platform.tenant_audit.kept_unconfirmed"]
    assert {"tenant_id": str(school), "count": 1, "outcome": "kept"} in kept
    for _event, fields in recorder.lines:  # ids of schools and counts only, no personal data
        assert set(fields) <= {"tenant_id", "count", "outcome", "error_type"}
        assert str(unconfirmed) not in str(fields)
    with admin_engine.connect() as c:
        summary: dict[str, Any] = c.execute(
            text(
                "SELECT summary FROM platform.audit_events "
                "WHERE action = 'tenant_audit.copies_purged' ORDER BY occurred_at DESC LIMIT 1"
            )
        ).scalar_one()
    assert summary["kept_unconfirmed"] >= 1

    # Kept, not lost: once the school chain has the event, the next purge deletes the copy.
    with admin_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(
            text("UPDATE platform.tenant_audit_outbox SET delivered_at = NULL WHERE id = :i"),
            {"i": unconfirmed},
        )
    assert tenant_audit.deliver_pending(tenant_id=school).delivered == 1
    _age(admin_engine, unconfirmed, 31)
    assert tenant_audit.purge_delivered().purged >= 1
    assert unconfirmed not in _ids(admin_engine, school)


@pytest.mark.db
def test_ADR_0020_D51_platform_role_still_has_no_tenant_table_privileges(
    platform_engine: Engine, admin_engine: Engine
) -> None:
    """The DELETE grant is on the platform outbox only (CLAUDE.md invariant 1; the full
    catalog check is tests/security/test_rls_catalog.py)."""
    with admin_engine.connect() as c:
        deletable: list[str] = list(
            c.execute(
                text(
                    "SELECT n.nspname || '.' || c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relkind IN ('r','p') AND n.nspname <> 'platform' "
                    "AND n.nspname NOT IN ('pg_catalog', 'pg_toast', 'information_schema') "
                    "AND has_table_privilege('sos_platform', c.oid, 'DELETE')"
                )
            ).scalars()
        )
        app_grants = c.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = 'sos_app' AND table_schema = 'platform' "
                "AND table_name = 'tenant_audit_outbox'"
            )
        ).all()
    assert deletable == []
    assert app_grants == []


def _state(engine: Engine) -> tuple[set[str], int, int]:
    with engine.connect() as c:
        grants: set[str] = set(
            c.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = 'sos_platform' AND table_schema = 'platform' "
                    "AND table_name = 'tenant_audit_outbox'"
                )
            ).scalars()
        )
        triggers = int(
            c.execute(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
                    "AND tgrelid = 'platform.tenant_audit_outbox'::regclass "
                    "AND tgname = 'tenant_audit_outbox_delivered_only'"
                )
            ).scalar_one()
        )
        index = int(
            c.execute(
                text(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname = 'platform' "
                    "AND indexname = 'tenant_audit_outbox_delivered'"
                )
            ).scalar_one()
        )
    return grants, triggers, index


@pytest.mark.db
def test_D51_migration_0074_round_trip(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> None:
    url = test_database.create_fresh(DB)
    cfg = make_alembic_config(url)
    command.upgrade(cfg, REVISION)
    engine = create_engine(make_url(test_database.admin_url).set(database=DB))
    try:
        grants, triggers, index = _state(engine)
        assert "DELETE" in grants
        assert (triggers, index) == (1, 1)

        command.downgrade(cfg, PREVIOUS)
        grants, triggers, index = _state(engine)
        assert "DELETE" not in grants
        assert {"SELECT", "INSERT"} <= grants
        assert (triggers, index) == (0, 0)
        with engine.connect() as c:
            fn: int = c.execute(
                text(
                    "SELECT count(*) FROM pg_proc "
                    "WHERE proname = 'tg_tenant_audit_outbox_delivered_only'"
                )
            ).scalar_one()
        assert fn == 0

        command.upgrade(cfg, "head")
        grants, triggers, index = _state(engine)
        assert "DELETE" in grants
        assert (triggers, index) == (1, 1)
    finally:
        engine.dispose()
