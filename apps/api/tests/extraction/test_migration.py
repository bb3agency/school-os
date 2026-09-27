"""0016_extraction on a populated database (CLAUDE.md §6.12, §8; docs/05 §5).

A fresh database is migrated to head and seeded with a synthetic school (``seed-synthetic``);
a register page is extracted and one row confirmed through the services, so all three
extraction tables, a student and its evidence-linked values hold data. The walk
head -> 0011 -> head -> 0011 -> head must succeed with that data present, and the tables must be
tenant-isolated (RLS ENABLE + FORCE, tenant_isolation) with DELETE revoked from the app role.
"""

from __future__ import annotations

import io
import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.db import tenant_session
from app.devtools import seed_synthetic as cli
from app.devtools import seeder
from app.extraction import service
from app.extraction.schemas import BatchCreate, ItemConfirm

pytestmark = pytest.mark.db
X = sys.modules["sos_test_extraction_support"]
DB = "schoolos_extraction_migration"
TABLES = ("sis.extraction_batches", "sis.extraction_pages", "sis.extraction_items")
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


def _below(cfg: Config) -> str:
    """The revision under 0016_extraction (0011 while developing; the lead relinks wave 2)."""
    from alembic.script import ScriptDirectory

    rev = ScriptDirectory.from_config(cfg).get_revision("0016_extraction")
    assert rev is not None
    down = rev.down_revision
    assert isinstance(down, str)
    return down


@pytest.fixture
def populated(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> Iterator[tuple[Config, Engine]]:
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
            ["--tenants", "1", "--code-prefix", "extm"],
            settings=SETTINGS,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK
        _extract_and_confirm(engines["admin"])
        yield cfg, engines["admin"]
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def _extract_and_confirm(admin: Engine) -> None:
    X.SW.configure_keyring()
    X.D.memory_store()
    with admin.connect() as c:
        row = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id AS membership_id FROM core.memberships m "
                "WHERE m.status = 'active' ORDER BY m.created_at LIMIT 1"
            )
        ).one()
    person = X.W.Person("office_admin", row.user_id, row.membership_id, "sub", "Synthetic")
    ctx = X.SW.ctx_for(row.tenant_id, person, "office_admin")
    rows = [X.register_row(f"Synthetica Walk {i}") for i in range(3)]
    doc = X.register_scan(admin, row.tenant_id, row.user_id, X.page_png(rows))
    with tenant_session(row.tenant_id, row.user_id) as s:
        batch = service.create_batch(s, ctx, BatchCreate(document_ids=[doc]))
    service.process_batch(row.tenant_id, batch.id)
    items = X.item_ids(admin, batch.id)
    with tenant_session(row.tenant_id, row.user_id) as s:
        service.confirm_item(
            s, ctx, items[0], ItemConfirm(fields={"full_name": "Synthetica Walk 0"})
        )


def _scalar(admin: Engine, sql: str) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql)).scalar_one()


def test_CLAUDE_6_12_extraction_migration_reversible_with_data(
    populated: tuple[Config, Engine],
) -> None:
    cfg, admin = populated
    below = _below(cfg)
    assert _scalar(admin, "SELECT count(*) FROM sis.extraction_items") == 3
    assert (
        _scalar(admin, "SELECT count(*) FROM sis.extraction_items WHERE status = 'confirmed'") == 1
    )
    students = _scalar(admin, "SELECT count(*) FROM sis.students")
    command.downgrade(cfg, below)
    for table in TABLES:
        assert _scalar(admin, f"SELECT to_regclass('{table}') IS NULL") is True
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students, "records untouched"
    command.upgrade(cfg, "0016_extraction")
    command.downgrade(cfg, below)
    command.upgrade(cfg, "head")
    for table in TABLES:
        assert _scalar(admin, f"SELECT count(*) FROM {table}") == 0


def test_SEC_001_extraction_tables_are_tenant_isolated(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        for table in TABLES:
            schema, name = table.split(".")
            flags = c.execute(
                text(
                    "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = :n"
                ),
                {"s": schema, "n": name},
            ).one()
            assert tuple(flags) == (True, True), table
            policies: set[str] = set(
                c.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = :n"
                    ),
                    {"s": schema, "n": name},
                ).scalars()
            )
            assert policies == {"tenant_isolation"}, table
            can_delete: object = c.execute(
                text("SELECT has_table_privilege('sos_app', :t, 'DELETE')"), {"t": table}
            ).scalar_one()
            assert can_delete is False, table


def test_SEC_001_other_school_rows_are_invisible(world: Any, admin_engine: Engine) -> None:
    item = X.pending_item(admin_engine, world.b)
    with tenant_session(world.a.tenant_id) as s:
        seen: object = s.execute(
            text("SELECT count(*) FROM sis.extraction_items WHERE id = :i"), {"i": item}
        ).scalar_one()
    assert seen == 0
    with tenant_session(world.b.tenant_id) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM sis.extraction_items WHERE id = :i"), {"i": item}
            ).scalar_one()
            == 1
        )
