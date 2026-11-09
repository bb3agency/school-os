"""0088_tally_vouchers: schema facts and a populated round trip (CLAUDE.md §6.1, §6.12, §8;
FM-03; FR-TALLY-011..030; SEC-001). Synthetic data only.
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
DB = "schoolos_tally_vouchers_migration"
REVISION = "0088_tally_vouchers"
TABLES = {
    "tally_export_settings": models.ExportSettings,
    "tally_voucher_exports": models.VoucherExport,
    "tally_exported_vouchers": models.ExportedVoucher,
}


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


@pytest.mark.parametrize("name", sorted(TABLES))
def test_FR_TALLY_011_models_match_database(admin_engine: Engine, name: str) -> None:
    table = TABLES[name].__table__
    assert isinstance(table, Table)
    db_cols = {c["name"]: c for c in inspect(admin_engine).get_columns(name, "ops")}
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, col.name


@pytest.mark.parametrize("name", sorted(TABLES))
def test_SEC_001_voucher_tables_force_rls_with_narrow_grants(
    admin_engine: Engine, name: str
) -> None:
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'ops' AND c.relname = :n"
            ),
            {"n": name},
        ).one()
        policies: set[str] = set(
            c.execute(
                text("SELECT policyname FROM pg_policies WHERE tablename = :n"), {"n": name}
            ).scalars()
        )
        privileges: set[str] = set(
            c.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = 'sos_app' AND table_schema = 'ops' AND table_name = :n"
                ),
                {"n": name},
            ).scalars()
        )
    assert row == (True, True)
    assert {"tenant_isolation", "offboarding_purge"} <= policies
    assert {"SELECT", "INSERT"} <= privileges
    # Table-wide UPDATE, DELETE and TRUNCATE never; the mapping has column UPDATE only.
    assert not privileges & {"UPDATE", "DELETE", "TRUNCATE"}


def test_FR_TALLY_022_a_voucher_is_recorded_once(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        unique: set[str] = set(
            c.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'ops.tally_exported_vouchers'::regclass AND contype = 'u'"
                )
            ).scalars()
        )
    assert "tally_exported_vouchers_once" in unique


def test_CLAUDE_6_12_vouchers_migration_reversible_with_data(
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
        t, user, export = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        with admin.begin() as c:
            c.execute(
                text(
                    "INSERT INTO core.tenants (id, code, name, status) "
                    "VALUES (:t, 'vouchers-test', 'Synthetic School', 'active')"
                ),
                {"t": t},
            )
            c.execute(
                text(
                    "INSERT INTO core.users (id, email, idp_subject, display_name) "
                    "VALUES (:u, :e, CAST(:u AS text), 'Synthetic Accountant')"
                ),
                {"u": user, "e": f"vouchers-{user}@example.test"},
            )
            c.execute(
                text(
                    "INSERT INTO ops.tally_export_settings (id, tenant_id, party_mode, "
                    "receivable_ledger, mode_ledgers, updated_by) VALUES (:i, :t, 'receivable', "
                    "'Fee receivable', '{\"cash\": \"Cash\"}', :u)"
                ),
                {"i": uuid.uuid4(), "t": t, "u": user},
            )
            c.execute(
                text(
                    "INSERT INTO ops.tally_voucher_exports (id, tenant_id, period_from, "
                    "period_to, include_exported, include_demand, party_mode, vouchers, "
                    "new_vouchers, cash_total, file_sha256, created_by) VALUES (:i, :t, "
                    "'2026-11-01', '2026-11-02', false, false, 'receivable', 2, 2, 20.00, :h, :u)"
                ),
                {"i": export, "t": t, "h": bytes(32), "u": user},
            )
            c.execute(
                text(
                    "INSERT INTO ops.tally_exported_vouchers (id, tenant_id, voucher_key, kind, "
                    "export_id) VALUES (:i, :t, :k, 'receipt', :e)"
                ),
                {"i": uuid.uuid4(), "t": t, "k": f"receipt:{uuid.uuid4()}", "e": export},
            )

        command.downgrade(cfg, before)
        for name in TABLES:
            assert _scalar(admin, f"SELECT to_regclass('ops.{name}') IS NULL") is True
        command.upgrade(cfg, "head")
        assert _scalar(admin, "SELECT count(*) FROM ops.tally_voucher_exports") == 0
    finally:
        admin.dispose()
