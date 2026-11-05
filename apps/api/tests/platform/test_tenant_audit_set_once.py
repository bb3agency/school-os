"""School-chain copies: ``delivered_at`` is set once (lead decision D68; FR-AUD-001, ADR-0020,
CLAUDE.md invariant 7; migration 0079_audit_outbox_set_once).

The control plane's role may set ``delivered_at`` on a copy only once, to a time between the
copy's creation and now; it can never change or clear it, and the copy itself never changes.
The normal deliverer still works.
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

from app.core.crypto import LocalDevKeyWrapper
from app.core.db import platform_session
from app.platform import tenant_audit
from app.platform.common import Actor
from app.tenancy import service as tenancy

REVISION = "0079_audit_outbox_set_once"
PREVIOUS = "0078_accept_invite_suspended"
DB = "schoolos_tenant_audit_set_once"
OPERATOR = Actor(uuid.UUID("0190a000-0000-7000-8000-00000000a0d6"), request_id="req-set-once")


@pytest.fixture
def school(
    wrapper: LocalDevKeyWrapper, platform_engine: Engine, app_engine: Engine
) -> Iterator[uuid.UUID]:
    yield tenancy.provision_tenant(
        code=f"s-{uuid.uuid4().hex[:12]}", name="Synthetic Set Once School", wrapper=wrapper
    ).tenant_id
    tenant_audit.deliver_pending()  # leave no queued rows behind for other tests


def _enqueue(tenant_id: uuid.UUID, action: str = "tenant.suspended") -> uuid.UUID:
    with platform_session() as s:
        return tenant_audit.enqueue(s, tenant_id, OPERATOR, action, {"cause": "billing"})


def _as_platform(sql: str, event_id: uuid.UUID) -> None:
    with platform_session() as s:
        s.execute(text(sql), {"i": event_id})


def _delivered_at(admin: Engine, event_id: uuid.UUID) -> Any:
    with admin.connect() as c:
        return c.execute(
            text("SELECT delivered_at FROM platform.tenant_audit_outbox WHERE id = :i"),
            {"i": event_id},
        ).scalar_one()


@pytest.mark.db
def test_FR_AUD_001_D68_normal_delivery_still_sets_delivered_at_once(
    school: uuid.UUID, admin_engine: Engine
) -> None:
    event_id = _enqueue(school)
    assert tenant_audit.deliver_pending(tenant_id=school).delivered == 1
    assert _delivered_at(admin_engine, event_id) is not None


@pytest.mark.db
@pytest.mark.parametrize(
    ("sql", "message"),
    [
        (
            "UPDATE platform.tenant_audit_outbox SET delivered_at = delivered_at + "
            "interval '1 second' WHERE id = :i",
            "delivered_at is set once and cannot be changed or cleared",
        ),
        (
            "UPDATE platform.tenant_audit_outbox SET delivered_at = NULL WHERE id = :i",
            "delivered_at is set once and cannot be changed or cleared",
        ),
    ],
    ids=["change", "clear"],
)
def test_FR_AUD_001_D68_delivered_at_cannot_change_or_be_cleared(
    school: uuid.UUID, admin_engine: Engine, sql: str, message: str
) -> None:
    event_id = _enqueue(school)
    tenant_audit.deliver_pending(tenant_id=school)
    before = _delivered_at(admin_engine, event_id)
    with pytest.raises(DBAPIError) as exc:
        _as_platform(sql, event_id)
    assert getattr(exc.value.orig, "sqlstate", None) == "55000"
    assert message in str(exc.value)
    assert str(event_id) not in str(exc.value.orig)  # no values in the message
    assert _delivered_at(admin_engine, event_id) == before


@pytest.mark.db
@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("clock_timestamp() + interval '1 hour'", "delivered_at cannot be in the future"),
        ("created_at - interval '1 second'", "delivered_at cannot be before the copy was created"),
    ],
    ids=["future", "before_created"],
)
def test_FR_AUD_001_D68_delivered_at_must_lie_between_creation_and_now(
    school: uuid.UUID, admin_engine: Engine, value: str, message: str
) -> None:
    event_id = _enqueue(school)
    with pytest.raises(DBAPIError) as exc:
        _as_platform(
            f"UPDATE platform.tenant_audit_outbox SET delivered_at = {value} WHERE id = :i",
            event_id,
        )
    assert getattr(exc.value.orig, "sqlstate", None) == "55000"
    assert message in str(exc.value)
    assert _delivered_at(admin_engine, event_id) is None
    tenant_audit.deliver_pending(tenant_id=school)  # the copy is still delivered normally
    assert _delivered_at(admin_engine, event_id) is not None


@pytest.mark.db
def test_FR_AUD_001_D68_the_copy_itself_is_immutable_even_for_the_owner(
    school: uuid.UUID, admin_engine: Engine
) -> None:
    event_id = _enqueue(school)
    with pytest.raises(DBAPIError) as exc, admin_engine.begin() as c:
        c.execute(
            text("UPDATE platform.tenant_audit_outbox SET summary = '{}' WHERE id = :i"),
            {"i": event_id},
        )
    assert "school-chain copies cannot be changed, only their delivery state" in str(exc.value)
    # attempts and last_error stay writable for the deliverer
    _as_platform(
        "UPDATE platform.tenant_audit_outbox SET attempts = attempts + 1, last_error = 'db_error' "
        "WHERE id = :i",
        event_id,
    )


def _trigger_count(engine: Engine) -> tuple[int, int]:
    with engine.connect() as c:
        trig = int(
            c.execute(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
                    "AND tgrelid = 'platform.tenant_audit_outbox'::regclass "
                    "AND tgname = 'tenant_audit_outbox_set_once'"
                )
            ).scalar_one()
        )
        fn = int(
            c.execute(
                text(
                    "SELECT count(*) FROM pg_proc WHERE proname = 'tg_tenant_audit_outbox_set_once'"
                )
            ).scalar_one()
        )
    return trig, fn


@pytest.mark.db
def test_D68_migration_0079_round_trip(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> None:
    url = test_database.create_fresh(DB)
    cfg = make_alembic_config(url)
    command.upgrade(cfg, REVISION)
    engine = create_engine(make_url(test_database.admin_url).set(database=DB))
    try:
        assert _trigger_count(engine) == (1, 1)
        command.downgrade(cfg, PREVIOUS)
        assert _trigger_count(engine) == (0, 0)
        command.upgrade(cfg, "head")
        assert _trigger_count(engine) == (1, 1)
    finally:
        engine.dispose()
