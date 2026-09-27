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


# --- 0018_extraction_redaction (PRV-016) ----------------------------------------------------

REDACTION = "0018_extraction_redaction"


def _page_flags(admin: Engine) -> tuple[Any, ...]:
    with admin.connect() as c:
        return tuple(
            c.execute(
                text(
                    "SELECT p.aadhaar_detected, p.image_withheld, b.pages_withheld "
                    "FROM sis.extraction_pages p JOIN sis.extraction_batches b "
                    "ON b.tenant_id = p.tenant_id AND b.id = p.batch_id"
                )
            ).one()
        )


def test_PRV_016_downgrade_refuses_while_redacted_pages_exist_and_round_trips_otherwise(
    populated: tuple[Config, Engine],
) -> None:
    from sqlalchemy.exc import DBAPIError

    cfg, admin = populated
    with admin.begin() as c:
        c.execute(
            text("UPDATE sis.extraction_pages SET aadhaar_detected = true, image_redacted = true")
        )
    assert _page_flags(admin) == (True, False, 0)
    before = _scalar(admin, "SELECT version_num FROM ops.alembic_version")
    # head (each later revision is added here)
    assert before in {REDACTION, "0019_export_access", "0020_provisioning_runs", "0021_kb_tables"}
    with pytest.raises(DBAPIError, match="irreversible: redacted register pages exist"):
        command.downgrade(cfg, "0017_exports")
    # The refused walk changes nothing: the database stays at the revision it was at.
    assert _scalar(admin, "SELECT version_num FROM ops.alembic_version") == before
    assert _scalar(admin, "SELECT image_redacted FROM sis.extraction_pages") is True

    # Without redacted pages (a withheld one here) the walk is clean.
    with admin.begin() as c:
        c.execute(
            text("UPDATE sis.extraction_pages SET image_redacted = false, image_withheld = true")
        )
    command.downgrade(cfg, "0017_exports")
    assert _page_flags(admin) == (True, True, 0)
    assert (
        _scalar(
            admin,
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'sis' "
            "AND table_name = 'extraction_pages' AND column_name = 'image_redacted'",
        )
        == 0
    )
    command.upgrade(cfg, "head")
    assert _scalar(admin, "SELECT image_redacted FROM sis.extraction_pages") is False
    assert _page_flags(admin) == (True, True, 0)


@pytest.mark.parametrize(
    ("detected", "withheld", "redacted", "ok"),
    [
        (False, False, False, True),
        (True, True, False, True),
        (True, False, True, True),
        (True, False, False, False),  # a page that showed a number is withheld or redacted
        (True, True, True, False),  # ... not both
        (False, True, False, False),
        (False, False, True, False),
    ],
)
def test_PRV_016_page_image_state_follows_detection(  # noqa: PLR0917 - parametrized
    world: Any,
    admin_engine: Engine,
    detected: bool,
    withheld: bool,
    redacted: bool,
    ok: bool,
) -> None:
    from sqlalchemy.exc import IntegrityError

    item = X.pending_item(admin_engine, world.a)
    page_id = X.row_of(admin_engine, "sis.extraction_items", item)["page_id"]
    stmt = text(
        "UPDATE sis.extraction_pages SET aadhaar_detected = :d, image_withheld = :w, "
        "image_redacted = :r WHERE id = :p"
    )
    params = {"d": detected, "w": withheld, "r": redacted, "p": page_id}
    if ok:
        with admin_engine.begin() as c:
            c.execute(stmt, params)
    else:
        with pytest.raises(IntegrityError), admin_engine.begin() as c:
            c.execute(stmt, params)
