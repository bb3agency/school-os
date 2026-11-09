"""0088_udise_followup: schema facts and database checks (CLAUDE.md §6.1, §6.7, §6.12;
FR-UDI-021, FR-UDI-025, FR-UDI-035). The round trips with data run in tests/migrations."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, Table, inspect, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.dq import followup_models as models

pytestmark = pytest.mark.db
DS = sys.modules["sos_test_dq_support"]
TABLES = ("dq_followups", "udise_movement_marks")


def _grants(engine: Engine, role: str, table: str) -> set[str]:
    with engine.connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = :r AND table_schema = 'sis' AND table_name = :t"
                ),
                {"r": role, "t": table},
            ).scalars()
        )


@pytest.mark.parametrize(
    "table", [models.dq_followups, models.udise_movement_marks], ids=lambda t: t.name
)
def test_FR_UDI_021_models_match_database(table: Table, admin_engine: Engine) -> None:
    db_cols = {
        c["name"]: c for c in inspect(admin_engine).get_columns(table.name, schema=table.schema)
    }
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


def test_SEC_001_tables_force_rls_with_purge_policy(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'sis' AND c.relname = ANY(:names)"
            ),
            {"names": list(TABLES)},
        ).all()
        policies = {
            (r.tablename, r.policyname)
            for r in c.execute(
                text(
                    "SELECT tablename, policyname FROM pg_policies "
                    "WHERE schemaname = 'sis' AND tablename = ANY(:names)"
                ),
                {"names": list(TABLES)},
            )
        }
    assert {r.relname for r in rows} == set(TABLES)
    assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows)
    for table in TABLES:
        assert (table, "tenant_isolation") in policies
        assert (table, "offboarding_purge") in policies  # ADR-0029
        assert {"SELECT", "DELETE"} <= _grants(admin_engine, "sos_purger", table)
        assert not _grants(admin_engine, "sos_platform", table)
    # The follow-up log is append-only for the app; a tick may be changed or undone.
    assert _grants(admin_engine, "sos_app", "dq_followups") == {"SELECT", "INSERT"}
    marks = _grants(admin_engine, "sos_app", "udise_movement_marks")
    assert {"SELECT", "INSERT", "DELETE"} <= marks
    assert not {"UPDATE", "TRUNCATE"} & marks


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE sis.dq_followups SET event = 'slip_given' WHERE false",
        "DELETE FROM sis.dq_followups WHERE false",
        "UPDATE sis.udise_movement_marks SET item_key = 'x' WHERE false",
        "UPDATE sis.udise_movement_marks SET student_id = student_id WHERE false",
    ],
)
def test_FR_UDI_025_app_role_cannot_rewrite_the_log_or_keys(
    app_engine: Engine, statement: str
) -> None:
    with app_engine.connect() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(uuid.uuid4())})
        with pytest.raises(ProgrammingError, match="permission denied"):
            c.execute(text(statement))


def test_FR_UDI_025_log_refuses_update_and_delete_even_for_the_owner(
    admin_engine: Engine, world: Any, keyring: None
) -> None:
    sid = DS.student(world.a)
    who = world.a.people["owner"]
    row = uuid.uuid4()
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO sis.dq_followups (id, tenant_id, student_id, profile_key, fix_owner, "
                "event, occurred_on, recorded_by, recorded_by_membership) VALUES (:i, :t, :s, "
                "'bseap-ssc-2027', 'parent_aadhaar', 'slip_given', DATE '2026-11-02', :u, :m)"
            ),
            {"i": row, "t": world.a.tenant_id, "s": sid, "u": who.user_id, "m": who.membership_id},
        )
    for statement in (
        "UPDATE sis.dq_followups SET channel = 'phone' WHERE id = :i",
        "DELETE FROM sis.dq_followups WHERE id = :i",
    ):
        with pytest.raises(DBAPIError, match="append-only"), admin_engine.begin() as c:
            c.execute(text(statement), {"i": row})
    # A promise must carry its date (and only a promise does).
    with pytest.raises(DBAPIError, match="dq_followups_promise"), admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO sis.dq_followups (id, tenant_id, student_id, profile_key, fix_owner, "
                "event, occurred_on, recorded_by, recorded_by_membership) VALUES (:i, :t, :s, "
                "'bseap-ssc-2027', 'parent_aadhaar', 'promised_by', DATE '2026-11-02', :u, :m)"
            ),
            {
                "i": uuid.uuid4(),
                "t": world.a.tenant_id,
                "s": sid,
                "u": who.user_id,
                "m": who.membership_id,
            },
        )
