"""0058_fees_core: model/database agreement and a populated round trip (CLAUDE.md §6.1, §6.12;
docs/05 §15.1; ADR-0042; FR-FEE-006).

A fresh database is migrated to head, seeded with a synthetic school (``seed-synthetic``) and
given a fee head, plan, account, charge and a receipt with its allocation and payment; head ->
0055 -> head must succeed with that data present: the ``fin`` tables, their triggers and the
``fee.*`` permissions go (the fee ledger is the lossy part, as documented), the student records
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
from app.fees.models import ALL_MODELS

pytestmark = pytest.mark.db
DB = "schoolos_fees_migration"
REVISION = "0058_fees_core"
PREVIOUS = "0057_messaging_core"  # re-chained at merge (docs/14 R2)
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__tablename__)
def test_FR_FEE_006_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    db_cols = {
        c["name"]: c for c in inspect(admin_engine).get_columns(table.name, schema=table.schema)
    }
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


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
            ["--tenants", "1", "--code-prefix", "feemig"],
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


def _seed_fees(admin: Engine) -> uuid.UUID:
    """A head, plan, account, charge and a cash receipt (as the test superuser)."""
    ids = {
        k: uuid.uuid4() for k in ("head", "plan", "account", "charge", "receipt", "alloc", "pay")
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
        c.execute(
            text(
                "INSERT INTO sis.students (id, tenant_id, admission_no) VALUES (:s, :t, 'FEEMIG1')"
            ),
            p,
        )
        c.execute(
            text(
                "INSERT INTO fin.fee_heads (id, tenant_id, code, name, kind, created_by) "
                "VALUES (:head, :t, 'TUI', 'Tuition', 'tuition', :m)"
            ),
            p,
        )
        c.execute(
            text(
                "INSERT INTO fin.fee_plans (id, tenant_id, academic_year_id, class_id, name, "
                "status, created_by, activated_by, activated_at) VALUES (:plan, :t, :y, :k, "
                "'Plan', 'active', :m, :m, now())"
            ),
            p,
        )
        c.execute(
            text(
                "INSERT INTO fin.student_fee_accounts (id, tenant_id, student_id, "
                "academic_year_id, plan_id, opened_by) VALUES (:account, :t, :s, :y, :plan, :m)"
            ),
            p,
        )
        c.execute(
            text(
                "INSERT INTO fin.ledger_entries (id, tenant_id, account_id, entry_kind, amount, "
                "instalment_no, head_id, entry_date) VALUES (:charge, :t, :account, 'charge', "
                "1000, 1, :head, current_date)"
            ),
            p,
        )
        c.execute(
            text(
                "INSERT INTO fin.receipts (id, tenant_id, account_id, financial_year, receipt_no, "
                "receipt_number, receipt_date, amount, mode, collected_by, content, "
                "content_sha256) VALUES (:receipt, :t, :account, '2026-27', 1, "
                "'RCT/2026-27/000001', current_date, 400, 'cash', :m, '{}'::jsonb, "
                "decode(repeat('00', 32), 'hex'))"
            ),
            p,
        )
        c.execute(
            text(
                "INSERT INTO fin.receipt_allocations (id, tenant_id, receipt_id, instalment_no, "
                "head_id, amount) VALUES (:alloc, :t, :receipt, 1, :head, 400)"
            ),
            p,
        )
        c.execute(
            text(
                "INSERT INTO fin.ledger_entries (id, tenant_id, account_id, entry_kind, amount, "
                "instalment_no, head_id, entry_date, receipt_id) VALUES (:pay, :t, :account, "
                "'payment', -400, 1, :head, current_date, :receipt)"
            ),
            p,
        )
    return uuid.UUID(str(row["t"]))


def test_CLAUDE_6_12_fees_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    before = ScriptDirectory.from_config(populated.cfg).get_revision(REVISION).down_revision
    assert before == PREVIOUS
    _seed_fees(admin)
    assert _scalar(admin, "SELECT count(*) FROM fin.receipts") == 1
    students = _scalar(admin, "SELECT count(*) FROM sis.students")
    events = _scalar(admin, "SELECT count(*) FROM audit.events")

    command.downgrade(populated.cfg, before)
    assert _scalar(admin, "SELECT to_regclass('fin.receipts') IS NULL") is True
    assert _scalar(admin, "SELECT to_regclass('fin.ledger_entries') IS NULL") is True
    # Keys still granted to the seeded school's roles stay (FK; as 0033), the others go.
    assert (
        _scalar(
            admin,
            "SELECT count(*) FROM core.permissions p WHERE key LIKE 'fee.%' AND NOT EXISTS "
            "(SELECT 1 FROM core.role_permissions r WHERE r.permission_key = p.key)",
        )
        == 0
    )
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students
    assert _scalar(admin, "SELECT count(*) FROM audit.events") == events
    assert _scalar(admin, "SELECT to_regnamespace('fin') IS NOT NULL") is True

    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM fin.receipts") == 0
    assert _scalar(admin, "SELECT count(*) FROM core.permissions WHERE key LIKE 'fee.%'") == 8
    command.downgrade(populated.cfg, before)
    command.upgrade(populated.cfg, "head")
