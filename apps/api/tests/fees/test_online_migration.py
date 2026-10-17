"""0059_payments_online: model/database agreement and a populated round trip (CLAUDE.md §6.1,
§6.12; docs/05 §15.2; ADR-0042).

A fresh database is migrated to head, seeded with a synthetic school, given a connection, a
payment intent with its receipt, an inbox event, a provider payment and a reconciliation item;
head -> 0058 -> head must succeed with that data present: the online tables, their triggers, the
receipt FK and ``payments.configure`` go (lossy as documented), receipts and the ledger stay, and
the upgrade brings the tables back empty.
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
from app.fees.online_models import ALL_MODELS

pytestmark = pytest.mark.db
DB = "schoolos_payments_migration"
REVISION = "0059_payments_online"
PREVIOUS = "0058_fees_core"  # re-chained at merge (docs/14 R2)
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)
ONLINE_TABLES = (
    "fin.payment_connections",
    "fin.payment_intents",
    "fin.provider_events",
    "fin.provider_payments",
    "fin.reconciliation_runs",
    "fin.reconciliation_items",
)


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__tablename__)
def test_FR_PAY_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    db_cols = {
        c["name"]: c for c in inspect(admin_engine).get_columns(table.name, schema=table.schema)
    }
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


def test_SEC_035_readonly_role_never_reads_secrets_or_payloads(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        for table in ("fin.payment_connections", "fin.provider_events"):
            assert (
                c.execute(
                    text("SELECT has_table_privilege('sos_readonly', :t, 'SELECT')"), {"t": table}
                ).scalar_one()
                is False
            ), table
        for table in ONLINE_TABLES:
            assert c.execute(
                text("SELECT has_table_privilege('sos_app', :t, 'DELETE')"), {"t": table}
            ).scalar_one() is (table == "fin.provider_events"), table


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
            ["--tenants", "1", "--code-prefix", "paymig"],
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
    """A connection, intent, receipt, event, provider payment and item (as the superuser)."""
    ids = {
        k: uuid.uuid4()
        for k in (
            "head",
            "plan",
            "account",
            "charge",
            "receipt",
            "alloc",
            "pay",
            "conn",
            "handle",
            "intent",
            "event",
            "pp",
            "run",
            "item",
        )
    }
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
        p = {**ids, **row, "s": uuid.uuid4()}
        statements = (
            "INSERT INTO sis.students (id, tenant_id, admission_no) VALUES (:s, :t, 'PAYMIG1')",
            "INSERT INTO fin.fee_heads (id, tenant_id, code, name, kind, created_by) "
            "VALUES (:head, :t, 'TUI', 'Tuition', 'tuition', :m)",
            "INSERT INTO fin.fee_plans (id, tenant_id, academic_year_id, class_id, name, status, "
            "created_by, activated_by, activated_at) VALUES (:plan, :t, :y, :k, 'Plan', 'active', "
            ":m, :m, now())",
            "INSERT INTO fin.student_fee_accounts (id, tenant_id, student_id, academic_year_id, "
            "plan_id, opened_by) VALUES (:account, :t, :s, :y, :plan, :m)",
            "INSERT INTO fin.ledger_entries (id, tenant_id, account_id, entry_kind, amount, "
            "instalment_no, head_id, entry_date) VALUES (:charge, :t, :account, 'charge', 1000, "
            "1, :head, current_date)",
            "INSERT INTO core.public_handles (id, tenant_id, kind, key_hash, target_type, "
            "target_id) VALUES (:handle, :t, 'razorpay_route', decode(repeat('ab', 32), 'hex'), "
            "'payment_connection', :conn)",
            "INSERT INTO fin.payment_connections (id, tenant_id, mode, environment, status, "
            "key_id, "
            "key_secret_ciphertext, webhook_secret_ciphertext, route_ciphertext, route_handle_id, "
            "connected_by) VALUES (:conn, :t, 'manual_keys', 'test', 'active', "
            "'rzp_test_SYNTHABC', '\\x01', '\\x02', '\\x03', :handle, :m)",
            "INSERT INTO fin.payment_intents (id, tenant_id, account_id, connection_id, "
            "intent_code, amount, instalments, status, provider_link_id, short_url, expires_at, "
            "created_via, created_by, provider_payment_id, paid_at, closed_at) VALUES (:intent, "
            ":t, :account, :conn, 'ABCD1234', 400, '{1}', 'paid', 'plink_SYNTH', "
            "'https://rzp.example.test/x', now() + interval '7 days', 'staff', :m, 'pay_SYNTH', "
            "now(), now())",
            "INSERT INTO fin.receipts (id, tenant_id, account_id, financial_year, receipt_no, "
            "receipt_number, receipt_date, amount, mode, provider_payment_id, payment_intent_id, "
            "content, content_sha256) VALUES (:receipt, :t, :account, '2026-27', 1, "
            "'RCT/2026-27/000001', current_date, 400, 'razorpay', 'pay_SYNTH', :intent, "
            "'{}'::jsonb, decode(repeat('00', 32), 'hex'))",
            "INSERT INTO fin.receipt_allocations (id, tenant_id, receipt_id, instalment_no, "
            "head_id, amount) VALUES (:alloc, :t, :receipt, 1, :head, 400)",
            "INSERT INTO fin.ledger_entries (id, tenant_id, account_id, entry_kind, amount, "
            "instalment_no, head_id, entry_date, receipt_id) VALUES (:pay, :t, :account, "
            "'payment', -400, 1, :head, current_date, :receipt)",
            "INSERT INTO fin.provider_events (id, tenant_id, connection_id, event_id, event_type, "
            "payload_ciphertext, status, processed_at) VALUES (:event, :t, :conn, 'evt_SYNTH', "
            "'payment_link.paid', '\\x00', 'processed', now())",
            "INSERT INTO fin.provider_payments (id, tenant_id, connection_id, provider_payment_id, "
            "intent_id, amount, currency, method, status, receipt_id) VALUES (:pp, :t, :conn, "
            "'pay_SYNTH', :intent, 400, 'INR', 'upi', 'captured', :receipt)",
            "INSERT INTO fin.reconciliation_runs (id, tenant_id, run_date) VALUES (:run, :t, "
            "current_date)",
            "INSERT INTO fin.reconciliation_items (id, tenant_id, run_id, kind, reason_code, "
            "dedupe_key, provider_payment_id, receipt_id) VALUES (:item, :t, :run, "
            "'settlement_missing', 'not_settled', 'settlement_missing:pay_SYNTH', 'pay_SYNTH', "
            ":receipt)",
        )
        for sql in statements:
            c.execute(text(sql), p)


def test_CLAUDE_6_12_payments_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    before = ScriptDirectory.from_config(populated.cfg).get_revision(REVISION).down_revision
    assert before == PREVIOUS
    _seed(admin)
    receipts = _scalar(admin, "SELECT count(*) FROM fin.receipts")
    entries = _scalar(admin, "SELECT count(*) FROM fin.ledger_entries")
    assert receipts == 1
    command.downgrade(populated.cfg, before)
    for table in ONLINE_TABLES:
        assert _scalar(admin, f"SELECT to_regclass('{table}') IS NULL") is True, table
    assert _scalar(admin, "SELECT count(*) FROM fin.receipts") == receipts
    assert _scalar(admin, "SELECT count(*) FROM fin.ledger_entries") == entries
    assert (
        _scalar(
            admin,
            "SELECT count(*) FROM pg_constraint WHERE conname = 'receipts_payment_intent_fk'",
        )
        == 0
    )
    # The receipt still names its (removed) intent: the FK comes back NOT VALID.
    command.upgrade(populated.cfg, "head")
    for table in ONLINE_TABLES:
        assert _scalar(admin, f"SELECT count(*) FROM {table}") == 0, table
    assert (
        _scalar(admin, "SELECT count(*) FROM core.permissions WHERE key = 'payments.configure'")
        == 1
    )
    command.downgrade(populated.cfg, before)
    command.upgrade(populated.cfg, "head")
