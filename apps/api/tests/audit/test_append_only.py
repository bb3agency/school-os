"""FR-AUD-002: audit tables are append-only (grants AND triggers), SEC-007, threat T4."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.core.db import tenant_session
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

pytestmark = pytest.mark.db


def _partition() -> str:
    now = datetime.now(UTC)
    return f"audit.events_y{now:%Y}m{now:%m}"


@pytest.fixture
def seeded(tenant: uuid.UUID, record_events: object) -> uuid.UUID:
    record_events(tenant, 2)  # type: ignore[operator]
    return tenant


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit.events SET action = 'x.y' WHERE tenant_id = :t",
        "DELETE FROM audit.events WHERE tenant_id = :t",
        "TRUNCATE audit.events",
        "DELETE FROM audit.chain_heads WHERE tenant_id = :t",
        "TRUNCATE audit.chain_heads",
    ],
    ids=["update", "delete", "truncate", "head-delete", "head-truncate"],
)
def test_FR_AUD_002_app_role_cannot_mutate_audit(
    seeded: uuid.UUID, app_engine: Engine, statement: str
) -> None:
    """The app may advance chain heads (record() does) but never delete or truncate them."""
    with pytest.raises(ProgrammingError, match="permission denied"), tenant_session(seeded) as s:
        s.execute(text(statement), {"t": seeded})


@pytest.mark.parametrize("verb", ["SELECT *", "UPDATE", "DELETE", "TRUNCATE", "INSERT"])
def test_FR_AUD_002_app_role_cannot_touch_partitions_directly(
    seeded: uuid.UUID, app_engine: Engine, verb: str
) -> None:
    table = _partition()
    sql = {
        "SELECT *": f"SELECT * FROM {table}",
        "UPDATE": f"UPDATE {table} SET action = 'x.y'",
        "DELETE": f"DELETE FROM {table}",
        "TRUNCATE": f"TRUNCATE {table}",
        "INSERT": f"INSERT INTO {table} (tenant_id) VALUES (:t)",
    }[verb]
    with pytest.raises(ProgrammingError, match="permission denied"), tenant_session(seeded) as s:
        s.execute(text(sql), {"t": seeded})


def test_FR_AUD_002_readonly_role_cannot_mutate(seeded: uuid.UUID, readonly_engine: Engine) -> None:
    for sql in (
        "UPDATE audit.events SET action = 'x.y'",
        "DELETE FROM audit.events",
        "TRUNCATE audit.events",
        "INSERT INTO audit.chain_heads (tenant_id, last_seq, last_hash) VALUES (:t, 0, '')",
    ):
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            readonly_engine.begin() as c,
        ):
            c.execute(text(sql), {"t": seeded})


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit.events SET action = 'x.y' WHERE tenant_id = :t",
        "DELETE FROM audit.events WHERE tenant_id = :t",
        "TRUNCATE audit.events",
        "UPDATE {partition} SET action = 'x.y' WHERE tenant_id = :t",
        "DELETE FROM {partition} WHERE tenant_id = :t",
        "TRUNCATE {partition}",
    ],
    ids=["update", "delete", "truncate", "part-update", "part-delete", "part-truncate"],
)
def test_FR_AUD_002_trigger_blocks_even_the_owner(
    seeded: uuid.UUID, owner_conn: Any, statement: str
) -> None:
    """Grants are not enough: the owner (via migrator) is stopped by the triggers too."""
    sql = statement.format(partition=_partition())
    with owner_conn(seeded) as conn, pytest.raises(DBAPIError, match="append-only"):
        conn.execute(text(sql), {"t": seeded})


def test_FR_AUD_002_rows_survive_attempts(
    seeded: uuid.UUID, app_engine: Engine, owner_conn: Any
) -> None:
    with owner_conn(seeded) as conn, pytest.raises(DBAPIError):
        conn.execute(text("DELETE FROM audit.events WHERE tenant_id = :t"), {"t": seeded})
    with tenant_session(seeded) as s:
        assert s.execute(text("SELECT count(*) FROM audit.events")).scalar_one() == 2


def test_FR_AUD_002_privilege_catalog(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        for role in ("sos_app", "sos_readonly", "sos_platform", "sos_definer"):
            for table in ("audit.events", "audit.chain_heads", _partition()):
                for priv in ("UPDATE", "DELETE", "TRUNCATE"):
                    if role == "sos_app" and table == "audit.chain_heads" and priv == "UPDATE":
                        continue
                    has: object = conn.execute(
                        text("SELECT has_table_privilege(:r, :t, :p)"),
                        {"r": role, "t": table, "p": priv},
                    ).scalar_one()
                    assert has is False, f"{role} has {priv} on {table}"
        assert conn.execute(
            text("SELECT has_table_privilege('sos_app', 'audit.events', 'INSERT')")
        ).scalar_one()
