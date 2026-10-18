"""0060_fees_upi: model/database agreement and a populated round trip (CLAUDE.md §6.1, §6.12;
docs/05 §15.1; ADR-0042 decision 9; FR-FEE-016).

A fresh database is migrated to head, seeded with a synthetic school (``seed-synthetic``) and given
a UPI receipt, UPI settings, a bank statement with one credit and the confirmation of that
receipt; head -> 0059 -> head must succeed with that data present: the four tables, their
triggers and ``fee.reconcile`` go (the lossy part, as documented), receipts and student records
are untouched, and the upgrade brings the tables back empty.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import Engine, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.model_base import Base
from app.devtools import seed_synthetic as cli
from app.devtools import seeder
from app.fees.upi_models import UPI_MODELS

pytestmark = pytest.mark.db
DB = "schoolos_fees_upi_migration"
REVISION = "0060_fees_upi"
PREVIOUS = "0059_payments_online"  # re-chained after R2-2D at merge (docs/14 R2)
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)
TABLES = (
    "fin.upi_settings",
    "fin.bank_statement_imports",
    "fin.bank_statement_lines",
    "fin.upi_confirmations",
)


@pytest.mark.parametrize("model", UPI_MODELS, ids=lambda m: m.__tablename__)
def test_FR_FEE_016_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    db_cols = {
        c["name"]: c for c in inspect(admin_engine).get_columns(table.name, schema=table.schema)
    }
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


def test_INV_1_new_tables_have_forced_rls_and_narrow_grants(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        for name in TABLES:
            schema, table = name.split(".")
            forced: object = c.execute(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class k JOIN "
                    "pg_namespace n ON n.oid = k.relnamespace WHERE n.nspname = :s "
                    "AND k.relname = :t"
                ),
                {"s": schema, "t": table},
            ).scalar_one()
            assert forced is True, name
            deletes: object = c.execute(
                text("SELECT has_table_privilege('sos_app', :n, 'DELETE')"), {"n": name}
            ).scalar_one()
            assert deletes is False, name
            platform: object = c.execute(
                text("SELECT has_table_privilege('sos_platform', :n, 'SELECT')"), {"n": name}
            ).scalar_one()
            assert platform is False, name
        # Statement imports and confirmations are insert-only for the app.
        for name in ("fin.bank_statement_imports", "fin.upi_confirmations"):
            updates: object = c.execute(
                text("SELECT has_any_column_privilege('sos_app', :n, 'UPDATE')"), {"n": name}
            ).scalar_one()
            assert updates is False, name


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


@dataclass
class _Walk:
    cfg: Config
    admin: Engine


@pytest.fixture
def populated(test_database: Any, make_alembic_config: Callable[[str], Config]) -> Iterator[_Walk]:
    url = test_database.create_fresh(DB)
    cfg = make_alembic_config(url)
    command.upgrade(cfg, "head")
    engines = {
        "app": create_engine(test_database.url_for("sos_app", "test-app-pw", DB)),
        "platform": create_engine(test_database.url_for("sos_platform", "test-platform-pw", DB)),
        "admin": create_engine(
            make_url(test_database.admin_url).set(database=DB).render_as_string(hide_password=False)
        ),
    }
    saved = {k: core_db.get_engine(k) for k in ("app", "platform")}
    core_db.set_engine("app", engines["app"])
    core_db.set_engine("platform", engines["platform"])
    try:
        code = cli.main(
            ["--tenants", "1", "--code-prefix", "upimig"],
            settings=SETTINGS,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK
        yield _Walk(cfg, engines["admin"])
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def _seed(admin: Engine) -> None:
    """A UPI receipt, settings, a statement with one credit and its confirmation (superuser)."""
    ids = {k: uuid.uuid4() for k in ("head", "plan", "account", "receipt", "alloc", "pay", "imp",
                                     "line", "conf", "set", "charge", "s")}  # fmt: skip
    with admin.begin() as c:
        row = (
            c.execute(
                text(
                    "SELECT t.id AS t, m.id AS m, y.id AS y, k.id AS k "
                    "FROM core.tenants t JOIN core.memberships m ON m.tenant_id = t.id "
                    "JOIN core.academic_years y ON y.tenant_id = t.id "
                    "JOIN core.classes k ON k.tenant_id = t.id "
                    "WHERE t.status = 'active' LIMIT 1"
                )
            )
            .mappings()
            .one()
        )
        p = {**ids, **row}
        for sql in (
            "INSERT INTO sis.students (id, tenant_id, admission_no) VALUES (:s, :t, 'UPIMIG1')",
            "INSERT INTO fin.fee_heads (id, tenant_id, code, name, kind, created_by) "
            "VALUES (:head, :t, 'TUI', 'Tuition', 'tuition', :m)",
            "INSERT INTO fin.fee_plans (id, tenant_id, academic_year_id, class_id, name, status, "
            "created_by, activated_by, activated_at) VALUES (:plan, :t, :y, :k, 'Plan', 'active', "
            ":m, :m, now())",
            "INSERT INTO fin.student_fee_accounts (id, tenant_id, student_id, academic_year_id, "
            "plan_id, opened_by) VALUES (:account, :t, :s, :y, :plan, :m)",
            "INSERT INTO fin.ledger_entries (id, tenant_id, account_id, entry_kind, amount, "
            "instalment_no, head_id, entry_date) VALUES (:charge, :t, :account, 'charge', 1000, 1, "
            ":head, current_date)",
            "INSERT INTO fin.receipts (id, tenant_id, account_id, financial_year, receipt_no, "
            "receipt_number, receipt_date, amount, mode, utr, collected_by, content, "
            "content_sha256) VALUES (:receipt, :t, :account, '2026-27', 1, 'RCT/2026-27/000001', "
            "current_date, 400, 'upi_qr', '629112345678', :m, '{}'::jsonb, "
            "decode(repeat('00', 32), 'hex'))",
            "INSERT INTO fin.receipt_allocations (id, tenant_id, receipt_id, instalment_no, "
            "head_id, amount) VALUES (:alloc, :t, :receipt, 1, :head, 400)",
            "INSERT INTO fin.ledger_entries (id, tenant_id, account_id, entry_kind, amount, "
            "instalment_no, head_id, entry_date, receipt_id) VALUES (:pay, :t, :account, "
            "'payment', -400, 1, :head, current_date, :receipt)",
            "INSERT INTO fin.upi_settings (id, tenant_id, payee_name, vpa, updated_by) VALUES "
            "(:set, :t, 'Synthetic School', 'synthetic.school@sbi', :m)",
            "INSERT INTO fin.bank_statement_imports (id, tenant_id, preset_key, preset_version, "
            "file_kind, file_sha256, period_from, period_to, line_count, credit_count, "
            "duplicate_count, proposed_count, imported_by, file_deleted_at) VALUES (:imp, :t, "
            "'generic', 1, 'csv', decode(repeat('11', 32), 'hex'), current_date, current_date, 1, "
            "1, 0, 1, :m, now())",
            "INSERT INTO fin.bank_statement_lines (id, tenant_id, import_id, line_no, value_date, "
            "amount, ref_kind, utr_or_ref, match_status, match_kind, receipt_id) VALUES (:line, "
            ":t, :imp, 6, current_date, 400, 'utr', '629112345678', 'proposed', 'utr_exact', "
            ":receipt)",
            "INSERT INTO fin.upi_confirmations (id, tenant_id, receipt_id, statement_line_id, "
            "match_kind, confirmed_by) VALUES (:conf, :t, :receipt, :line, 'utr_exact', :m)",
            "UPDATE fin.bank_statement_lines SET match_status = 'confirmed', decided_by = :m, "
            "decided_at = now() WHERE id = :line",
        ):
            c.execute(text(sql), p)


def test_CLAUDE_6_12_upi_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    before = ScriptDirectory.from_config(populated.cfg).get_revision(REVISION).down_revision
    assert before == PREVIOUS
    _seed(admin)
    assert _scalar(admin, "SELECT count(*) FROM fin.upi_confirmations") == 1
    receipts = _scalar(admin, "SELECT count(*) FROM fin.receipts")
    students = _scalar(admin, "SELECT count(*) FROM sis.students")

    command.downgrade(populated.cfg, before)
    for name in TABLES:
        assert _scalar(admin, "SELECT to_regclass(:n) IS NULL", n=name) is True, name
    assert _scalar(admin, "SELECT count(*) FROM fin.receipts") == receipts
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students
    assert (
        _scalar(
            admin,
            "SELECT count(*) FROM core.permissions p WHERE key = 'fee.reconcile' AND NOT EXISTS "
            "(SELECT 1 FROM core.role_permissions r WHERE r.permission_key = p.key)",
        )
        == 0
    )

    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM fin.bank_statement_lines") == 0
    assert _scalar(admin, "SELECT count(*) FROM core.permissions WHERE key = 'fee.reconcile'") == 1
    command.downgrade(populated.cfg, before)
    command.upgrade(populated.cfg, "head")
