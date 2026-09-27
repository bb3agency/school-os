"""0023_api_gaps: archive columns, guard trigger and a populated round trip (US-202, FR-TEN-010;
CLAUDE.md §6.12, §8).

A fresh database is migrated to head and seeded with a synthetic school; one class and one
section are archived. Walking 0023 down and up again must succeed with that data present: the
downgrade drops the ``archived_at`` columns (archived rows become ordinary rows; documented) and
keeps every row.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.devtools import seed_synthetic as cli
from app.devtools import seeder

pytestmark = pytest.mark.db
REVISION = "0023_api_gaps"
PREVIOUS = "0020_provisioning_runs"
DB = "schoolos_api_gaps_migration"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)
TABLES = ("academic_years", "classes", "sections")


def test_FR_TEN_010_archive_columns_and_guard_exist(admin_engine: Engine) -> None:
    insp = inspect(admin_engine)
    for table in TABLES:
        cols = {c["name"]: c for c in insp.get_columns(table, schema="core")}
        assert cols["archived_at"]["nullable"] is True, table
    with admin_engine.connect() as c:
        triggers = set(
            c.execute(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE "
                    "'%archive_guard'"
                )
            ).scalars()
        )
        definer: bool = c.execute(
            text(
                "SELECT prosecdef FROM pg_proc WHERE oid = "
                "'core.tg_structure_archive_guard()'::regprocedure"
            )
        ).scalar_one()
    assert triggers == {f"{t}_archive_guard" for t in TABLES}
    assert definer is False, "the guard runs as the caller (RLS applies)"


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
            ["--tenants", "1", "--code-prefix", "gaps"],
            settings=SETTINGS,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK
        yield cfg, engines["admin"]
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def _counts(admin: Engine) -> dict[str, int]:
    with admin.connect() as c:
        return {
            t: int(c.execute(text(f"SELECT count(*) FROM core.{t}")).scalar_one()) for t in TABLES
        }


def test_CLAUDE_6_12_0023_round_trip_with_archived_rows(populated: tuple[Config, Engine]) -> None:
    cfg, admin = populated
    with admin.begin() as c:
        c.execute(
            text(
                "UPDATE core.sections SET archived_at = now() WHERE id = (SELECT s.id FROM "
                "core.sections s WHERE NOT EXISTS (SELECT 1 FROM sis.enrollments e WHERE "
                "e.section_id = s.id AND e.status = 'active') ORDER BY s.id LIMIT 1)"
            )
        )
        c.execute(
            text(
                "INSERT INTO core.classes (id, tenant_id, code, display_en, display_te, "
                "sort_order, archived_at) SELECT gen_random_uuid(), id, 'ARCH', 'Archived', "
                "'పాతది', 999, now() FROM core.tenants LIMIT 1"
            )
        )
        archived = int(
            c.execute(
                text(
                    "SELECT (SELECT count(*) FROM core.sections WHERE archived_at IS NOT NULL) + "
                    "(SELECT count(*) FROM core.classes WHERE archived_at IS NOT NULL)"
                )
            ).scalar_one()
        )
    assert archived == 2
    before = _counts(admin)
    assert before["sections"] > 0

    command.downgrade(cfg, PREVIOUS)
    insp = inspect(admin)
    for table in TABLES:
        assert "archived_at" not in {c["name"] for c in insp.get_columns(table, schema="core")}
    assert _counts(admin) == before, "no rows lost"

    command.upgrade(cfg, REVISION)
    assert _counts(admin) == before
    with admin.connect() as c:
        still: int = c.execute(
            text("SELECT count(*) FROM core.classes WHERE archived_at IS NOT NULL")
        ).scalar_one()
    assert still == 0, "archived state is not restored by a re-upgrade (documented)"
    command.upgrade(cfg, "head")
