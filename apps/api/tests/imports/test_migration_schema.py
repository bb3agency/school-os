"""0012_imports: schema facts and a populated round trip (CLAUDE.md §6.12, §8; docs/05 §5.2).

A fresh database is migrated to head, seeded with a synthetic school (``seed-synthetic``), then
imports are run through the service (committed, reverted, failed). The walk 0012 -> 0011 ->
0012 -> 0011 -> head must succeed with that data present.
"""

from __future__ import annotations

import io
import sys
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import Engine, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.model_base import Base
from app.devtools import seed_synthetic as cli
from app.devtools import seeder
from app.imports import models

pytestmark = pytest.mark.db
S = sys.modules["sos_test_imports_support"]
DB = "schoolos_imports_migration"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


@pytest.mark.parametrize(
    "model",
    [models.ImportBatch, models.ImportRow, models.ImportMappingTemplate],
    ids=lambda m: m.__tablename__,
)
def test_FR_IMP_004_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    insp = inspect(admin_engine)
    db_cols = {c["name"]: c for c in insp.get_columns(table.name, schema=table.schema)}
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


def test_SEC_001_import_tables_are_isolated(world: Any, admin_engine: Engine) -> None:
    from app.core.db import tenant_session

    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(S.class_list(1)[0]))
    with tenant_session(world.b.tenant_id) as s:
        for table in ("sis.import_batches", "sis.import_rows"):
            column = "id" if table.endswith("batches") else "batch_id"
            n: int = s.execute(
                text(f"SELECT count(*) FROM {table} WHERE {column} = :b"), {"b": batch_id}
            ).scalar_one()
            assert n == 0
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'sis' "
                "AND c.relkind = 'r' AND c.relname LIKE 'import%'"
            )
        ).all()
        fk: str = c.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'attribute_values_import_batch_fk'"
            )
        ).scalar_one()
    assert {r.relname for r in rows} == {
        "import_batches",
        "import_rows",
        "import_mapping_templates",
        "import_cell_edits",  # 0030_import_cell_edits (FR-IMP-008)
    }
    assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows)
    assert "(tenant_id, import_batch_id)" in fk


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
            ["--tenants", "1", "--code-prefix", "impm"],
            settings=SETTINGS,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK
        _add_imports(engines["admin"])
        yield _Walk(cfg, engines["admin"])
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def _add_imports(admin: Engine) -> None:
    S.SW.configure_keyring()
    with admin.connect() as c:
        row = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id AS membership_id, k.code AS class_code, "
                "s.name AS section "
                "FROM core.memberships m JOIN core.sections s ON s.tenant_id = m.tenant_id "
                "JOIN core.classes k ON k.id = s.class_id "
                "JOIN core.academic_years y ON y.id = s.academic_year_id AND y.is_current "
                "JOIN core.membership_roles mr ON mr.membership_id = m.id "
                "JOIN core.roles r ON r.id = mr.role_id AND r.key = 'office_admin' "
                "WHERE m.status = 'active' ORDER BY m.created_at LIMIT 1"
            )
        ).one()
    person = S.W.Person("office_admin", row.user_id, row.membership_id, "sub", "Synthetic")
    school = S.W.School(row.tenant_id, people={"office_admin": person})
    for n in range(3):
        rows, _ = S.class_list(2, klass=row.class_code, section=row.section)
        rows[0].append("Caste")
        for r in rows[1:]:
            r.append("Synthetic Caste")
        batch_id = S.imported(admin, school, S.xlsx_bytes(rows))
        if n == 1:
            from app.core.db import tenant_session
            from app.imports import service

            with tenant_session(school.tenant_id, person.user_id) as s:
                service.revert(s, S.ctx(school), batch_id)
    S.start(admin, school, b"not a spreadsheet", kind="csv")  # a failed batch


def _scalar(admin: Engine, sql: str) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql)).scalar_one()


def test_CLAUDE_6_12_imports_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    assert _scalar(admin, "SELECT count(*) FROM sis.import_batches") == 4
    assert (
        _scalar(
            admin, "SELECT count(*) FROM sis.attribute_values WHERE import_batch_id IS NOT NULL"
        )
        > 0
    )
    students_before = _scalar(admin, "SELECT count(*) FROM sis.students")
    command.downgrade(populated.cfg, "0011_breakglass")
    assert _scalar(admin, "SELECT to_regclass('sis.import_batches') IS NULL") is True
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students_before
    fk = _scalar(
        admin,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'attribute_values_student_fk'",
    )
    assert "CASCADE" not in fk  # 0008's definition restored
    command.upgrade(populated.cfg, "0012_imports")
    assert _scalar(admin, "SELECT count(*) FROM sis.import_batches") == 0
    command.downgrade(populated.cfg, "0011_breakglass")
    command.upgrade(populated.cfg, "head")
    assert (
        _scalar(
            admin,
            "SELECT count(*) FROM pg_trigger "
            "WHERE tgname = 'students_delete_only_by_import_revert'",
        )
        == 1
    )


def test_CLAUDE_6_12_cell_edits_migration_reversible_with_data(populated: _Walk) -> None:
    """0030_import_cell_edits (FR-IMP-008) walks down and up on a populated database: the edit
    history goes with the table, batches and their rows stay."""
    admin = populated.admin
    with admin.begin() as c:
        batch = c.execute(
            text("SELECT id, tenant_id, created_by FROM sis.import_batches ORDER BY id LIMIT 1")
        ).one()
        c.execute(
            text(
                "INSERT INTO sis.import_cell_edits (id, tenant_id, batch_id, batch_version, "
                "row_no, column_index, new_value_ciphertext, key_version, edited_by) VALUES "
                "(:i, :t, :b, 2, 2, 1, :blob, 1, :u)"
            ),
            {
                "i": uuid.uuid4(),
                "t": batch.tenant_id,
                "b": batch.id,
                "blob": b"\x01\x00\x01synthetic",
                "u": batch.created_by,
            },
        )
    batches = _scalar(admin, "SELECT count(*) FROM sis.import_batches")
    rows = _scalar(admin, "SELECT count(*) FROM sis.import_rows")
    command.downgrade(populated.cfg, "0029_kb_v2")
    assert _scalar(admin, "SELECT to_regclass('sis.import_cell_edits') IS NULL") is True
    assert _scalar(admin, "SELECT count(*) FROM sis.import_batches") == batches
    assert _scalar(admin, "SELECT count(*) FROM sis.import_rows") == rows
    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM sis.import_cell_edits") == 0
    grants = _scalar(
        admin,
        "SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) "
        "FROM information_schema.role_table_grants WHERE grantee = 'sos_app' "
        "AND table_schema = 'sis' AND table_name = 'import_cell_edits'",
    )
    assert "DELETE" not in grants  # the edit history is append-only for the app
    assert "UPDATE" not in grants  # only the ciphertext columns (column grants, re-encryption)
