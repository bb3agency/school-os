"""tenant_session() behaviour (Invariant 1, FR-TEN-001, FR-TEN-002, SEC-001, docs/12 §4.4)."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator

import pytest
from app.core.db import context_free_session, tenant_session
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

pytestmark = pytest.mark.db

SCHEMA = "sos_test_isolation"


@pytest.fixture(scope="module")
def notes_table(admin_engine: Engine, app_engine: Engine) -> Iterator[str]:
    """A throwaway tenant table with the standard policy (docs/05 §3)."""
    with admin_engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA {SCHEMA} TO sos_app"))
        conn.execute(
            text(
                f"CREATE TABLE {SCHEMA}.notes "
                "(id uuid PRIMARY KEY, tenant_id uuid NOT NULL, body text)"
            )
        )
        conn.execute(text(f"ALTER TABLE {SCHEMA}.notes ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {SCHEMA}.notes FORCE ROW LEVEL SECURITY"))
        conn.execute(
            text(
                f"CREATE POLICY tenant_isolation ON {SCHEMA}.notes "
                "USING (tenant_id = core.current_tenant()) "
                "WITH CHECK (tenant_id = core.current_tenant())"
            )
        )
        conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {SCHEMA}.notes TO sos_app"))
    yield f"{SCHEMA}.notes"
    with admin_engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))


def _insert(tenant: uuid.UUID, table: str, body: str) -> None:
    with tenant_session(tenant) as s:
        s.execute(
            text(f"INSERT INTO {table} (id, tenant_id, body) VALUES (:i, :t, :b)"),
            {"i": uuid.uuid4(), "t": tenant, "b": body},
        )


def _count(tenant: uuid.UUID, table: str) -> int:
    with tenant_session(tenant) as s:
        return int(s.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def test_FR_TEN_002_tenant_sees_only_own_rows(
    notes_table: str, tenant_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = tenant_ids
    _insert(a, notes_table, "a1")
    _insert(a, notes_table, "a2")
    _insert(b, notes_table, "b1")
    assert _count(a, notes_table) == 2
    assert _count(b, notes_table) == 1


def test_FR_TEN_002_no_context_returns_zero_rows(
    notes_table: str, tenant_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    _insert(tenant_ids[0], notes_table, "x")
    with context_free_session() as s:
        assert s.execute(text(f"SELECT count(*) FROM {notes_table}")).scalar_one() == 0


def test_FR_TEN_002_insert_for_other_tenant_violates_with_check(
    notes_table: str, tenant_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = tenant_ids
    with pytest.raises(ProgrammingError, match="row-level security"), tenant_session(a) as s:
        s.execute(
            text(f"INSERT INTO {notes_table} (id, tenant_id, body) VALUES (:i, :t, 'x')"),
            {"i": uuid.uuid4(), "t": b},
        )


def test_FR_TEN_002_update_cannot_move_row_to_other_tenant(
    notes_table: str, tenant_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = tenant_ids
    _insert(a, notes_table, "move-me")
    with pytest.raises(ProgrammingError, match="row-level security"), tenant_session(a) as s:
        s.execute(text(f"UPDATE {notes_table} SET tenant_id = :b WHERE body = 'move-me'"), {"b": b})


def test_context_is_cleared_after_commit_on_reused_connection(
    app_engine: Engine, tenant_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    with tenant_session(tenant_ids[0]) as s:
        assert s.execute(text("SELECT core.current_tenant()")).scalar_one() == tenant_ids[0]
    # The pool hands back the same connection; the transaction-local setting must be gone.
    with app_engine.connect() as conn:
        assert conn.execute(text("SELECT core.current_tenant()")).scalar_one() is None


def test_context_is_cleared_after_rollback(
    app_engine: Engine, tenant_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    with pytest.raises(RuntimeError), tenant_session(tenant_ids[0]):
        raise RuntimeError("boom")
    with app_engine.connect() as conn:
        assert conn.execute(text("SELECT core.current_tenant()")).scalar_one() is None


def test_exception_rolls_back_writes(
    notes_table: str, tenant_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a = tenant_ids[0]

    def write_then_fail() -> None:
        with tenant_session(a) as s:
            s.execute(
                text(f"INSERT INTO {notes_table} (id, tenant_id, body) VALUES (:i, :t, 'lost')"),
                {"i": uuid.uuid4(), "t": a},
            )
            raise RuntimeError("abort")

    with pytest.raises(RuntimeError):
        write_then_fail()
    assert _count(a, notes_table) == 0


@pytest.mark.parametrize("bad", ["not-a-uuid", "", "1; DROP TABLE x"])
def test_invalid_tenant_id_is_rejected(bad: str, app_engine: Engine) -> None:
    with pytest.raises(ValueError, match="UUID"), tenant_session(bad):
        pass


def test_user_context_is_set(app_engine: Engine, tenant_ids: tuple[uuid.UUID, uuid.UUID]) -> None:
    user = uuid.uuid4()
    with tenant_session(tenant_ids[0], user) as s:
        assert s.execute(text("SELECT core.current_user_id()")).scalar_one() == user


def test_statement_timeout_is_applied(
    app_engine: Engine, tenant_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    with (
        pytest.raises(DBAPIError, match="statement timeout"),
        tenant_session(tenant_ids[0], statement_timeout_ms=200) as s,
    ):
        s.execute(text("SELECT pg_sleep(2)"))


def test_concurrent_sessions_do_not_leak_context(
    notes_table: str, tenant_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = tenant_ids
    _insert(a, notes_table, "a")
    errors: list[str] = []

    def worker(tenant: uuid.UUID) -> None:
        for _ in range(25):
            with tenant_session(tenant) as s:
                seen: list[uuid.UUID] = list(
                    s.execute(text(f"SELECT DISTINCT tenant_id FROM {notes_table}")).scalars().all()
                )
                if any(t != tenant for t in seen):
                    errors.append(f"{tenant} saw {seen}")

    threads = [threading.Thread(target=worker, args=(t,)) for t in (a, b, a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_current_tenant_is_null_for_empty_setting(app_engine: Engine) -> None:
    with app_engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        assert conn.execute(text("SELECT core.current_tenant()")).scalar_one() is None
