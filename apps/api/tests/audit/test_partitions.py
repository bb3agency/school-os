"""Monthly partitions of audit.events (docs/05 §12, SEC-001, FR-AUD-002)."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest
from app.audit.partitions import ensure_partitions, partition_upper_bound
from app.core.db import context_free_session
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0002_audit.py"


def _migration_module() -> object:
    spec = importlib.util.spec_from_file_location("m0002_audit", MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_creates_at_least_24_months_and_12_ahead() -> None:
    months = _migration_module().initial_partition_months  # type: ignore[attr-defined]
    early = months(date(2026, 9, 26))
    assert early[0] == date(2026, 9, 1)
    assert len(early) == 24
    late = months(date(2029, 1, 15))
    assert late[-1] == date(2030, 1, 1)


def test_partition_upper_bound_from_names() -> None:
    assert partition_upper_bound(["events_y2026m09", "events_y2026m12", "other"]) == date(
        2027, 1, 1
    )
    assert partition_upper_bound([]) is None


@pytest.mark.db
def test_ensure_partitions_creates_secured_partitions(
    migrator_engine: Engine, admin_engine: Engine
) -> None:
    with migrator_engine.begin() as conn:
        ensure_partitions(conn, 40)
    with migrator_engine.begin() as conn:
        assert ensure_partitions(conn, 40) == [], "idempotent"
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, r.rolname AS owner,
                       ARRAY(SELECT polname::text FROM pg_policy WHERE polrelid = c.oid) AS pols,
                       ARRAY(SELECT tgname::text FROM pg_trigger WHERE tgrelid = c.oid) AS trg,
                       has_table_privilege('sos_app', c.oid, 'SELECT') AS app_sel
                FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
                JOIN pg_roles r ON r.oid = c.relowner
                WHERE i.inhparent = 'audit.events'::regclass
                """
            )
        ).all()
    assert len(rows) >= 41
    for r in rows:
        assert r.relrowsecurity, r.relname
        assert r.relforcerowsecurity, r.relname
        assert r.owner == "sos_owner", r.relname
        assert set(r.pols) == {"tenant_isolation"}, r.relname
        assert {"events_append_only", "events_no_truncate"} <= set(r.trg), r.relname
        assert r.app_sel is False, r.relname


@pytest.mark.db
def test_app_role_cannot_create_partitions(app_engine: Engine) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), context_free_session() as s:
        s.execute(text("SELECT audit.ensure_partitions(1)"))
