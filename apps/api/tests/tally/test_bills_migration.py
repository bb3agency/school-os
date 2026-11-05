"""0077_tally_bills: schema facts and a populated round trip (CLAUDE.md §6.1, §6.12, §8; D63;
ADR-0032; FR-TALLY-005; SEC-001). Synthetic data only.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.tally import models

pytestmark = pytest.mark.db
DB = "schoolos_tally_bills_migration"
REVISION = "0077_tally_bills"


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def test_D63_model_matches_database(admin_engine: Engine) -> None:
    table = models.Bill.__table__
    assert isinstance(table, Table)
    db_cols = {c["name"]: c for c in inspect(admin_engine).get_columns("tally_bills", "ops")}
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, col.name


def test_SEC_001_bills_force_rls_with_narrow_grants(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'ops' AND c.relname = 'tally_bills'"
            )
        ).one()
        policies: set[str] = set(
            c.execute(
                text("SELECT policyname FROM pg_policies WHERE tablename = 'tally_bills'")
            ).scalars()
        )
        privileges: set[str] = set(
            c.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = 'sos_app' AND table_schema = 'ops' "
                    "AND table_name = 'tally_bills'"
                )
            ).scalars()
        )
        fks = {
            r[0]: r[1]
            for r in c.execute(
                text(
                    "SELECT conname, confdeltype FROM pg_constraint "
                    "WHERE conrelid = 'ops.tally_bills'::regclass AND contype = 'f'"
                )
            )
        }
    assert row == (True, True)
    assert {"tenant_isolation", "offboarding_purge"} <= policies
    assert {"SELECT", "INSERT", "DELETE"} <= privileges
    assert not privileges & {"UPDATE", "TRUNCATE"}
    # Composite FKs to the ledger and the sync record; both cascade (retention, D63).
    assert fks["tally_bills_party_fk"] == "c"
    assert fks["tally_bills_sync_fk"] == "c"


def test_CLAUDE_6_12_bills_migration_reversible_with_data(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> None:
    url = test_database.create_fresh(DB)
    cfg = make_alembic_config(url)
    command.upgrade(cfg, "head")
    admin = create_engine(
        make_url(test_database.admin_url).set(database=DB).render_as_string(hide_password=False)
    )
    try:
        before = ScriptDirectory.from_config(cfg).get_revision(REVISION).down_revision
        assert isinstance(before, str)
        t, user, code, device, sync, party = (uuid.uuid4() for _ in range(6))
        with admin.begin() as c:
            c.execute(
                text(
                    "INSERT INTO core.tenants (id, code, name, status) "
                    "VALUES (:t, 'bills-test', 'Synthetic School', 'active')"
                ),
                {"t": t},
            )
            c.execute(
                text(
                    "INSERT INTO core.users (id, email, idp_subject, display_name) "
                    "VALUES (:u, :e, CAST(:u AS text), 'Synthetic Owner')"
                ),
                {"u": user, "e": f"bills-{user}@example.test"},
            )
            c.execute(
                text(
                    "INSERT INTO ops.tally_enrolment_codes (id, tenant_id, code_hash, "
                    "device_name, created_by, expires_at) VALUES (:i, :t, :h, 'PC', :u, "
                    "now() + interval '30 minutes')"
                ),
                {"i": code, "t": t, "h": uuid.uuid4().bytes * 2, "u": user},
            )
            c.execute(
                text(
                    "INSERT INTO ops.tally_devices (id, tenant_id, name, enrolment_code_id, "
                    "key_id, key_ciphertext, enrolled_by) VALUES (:i, :t, 'PC', :c, "
                    "'tdk-abcdefghijklmnopqrst', '\\x01', :u)"
                ),
                {"i": device, "t": t, "c": code, "u": user},
            )
            c.execute(
                text(
                    "INSERT INTO ops.tally_syncs (id, tenant_id, device_id, batch_id, company, "
                    "as_of, groups, parties, created, updated, missing, total_due) VALUES "
                    "(:i, :t, :d, :b, 'Synthetic School', '2026-09-29', 1, 1, 1, 0, 0, 10.00)"
                ),
                {"i": sync, "t": t, "d": device, "b": uuid.uuid4()},
            )
            c.execute(
                text(
                    "INSERT INTO ops.tally_parties (id, tenant_id, company, ledger_name, "
                    "group_name, closing_balance, as_of, last_sync_id) VALUES (:i, :t, "
                    "'Synthetic School', 'Synthetic Party', 'Sundry Debtors', 10.00, "
                    "'2026-09-29', :s)"
                ),
                {"i": party, "t": t, "s": sync},
            )
            c.execute(
                text(
                    "INSERT INTO ops.tally_bills (id, tenant_id, party_id, sync_id, reference, "
                    "bill_date, due_date, pending) VALUES (:i, :t, :p, :s, 'Term 1', "
                    "'2026-04-01', '2026-06-15', 10.00)"
                ),
                {"i": uuid.uuid4(), "t": t, "p": party, "s": sync},
            )
        parties = _scalar(admin, "SELECT count(*) FROM ops.tally_parties")

        command.downgrade(cfg, before)
        assert _scalar(admin, "SELECT to_regclass('ops.tally_bills') IS NULL") is True
        assert _scalar(admin, "SELECT count(*) FROM ops.tally_parties") == parties
        command.upgrade(cfg, "head")
        assert _scalar(admin, "SELECT count(*) FROM ops.tally_bills") == 0
        assert (
            _scalar(
                admin,
                "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'ops.tally_bills'::regclass",
            )
            is True
        )
    finally:
        admin.dispose()
