"""Every migration downgrades and re-upgrades on a POPULATED synthetic database
(CLAUDE.md §6.12 and §8: "Migrations upgrade and downgrade cleanly on a populated synthetic DB").

A fresh database is migrated to head, seeded with one synthetic school (staff, roles, structure,
audit events) through the real services, then walked down one revision at a time and back up.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.devtools import seed_synthetic as cli
from app.devtools import seeder

pytestmark = pytest.mark.db

DB = "schoolos_migrations_populated"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


@pytest.fixture
def populated(
    test_database: object, make_alembic_config: Callable[[str], Config]
) -> Iterator[tuple[Config, str]]:
    url = test_database.create_fresh(DB)  # type: ignore[attr-defined]
    cfg = make_alembic_config(url)
    command.upgrade(cfg, "head")
    app_url = test_database.url_for("sos_app", "test-app-pw", DB)  # type: ignore[attr-defined]
    platform_url = test_database.url_for("sos_platform", "test-platform-pw", DB)  # type: ignore[attr-defined]
    admin_url = (
        make_url(test_database.admin_url)  # type: ignore[attr-defined]
        .set(database=DB)
        .render_as_string(hide_password=False)
    )
    engines: dict[str, Engine] = {
        "app": create_engine(app_url),
        "platform": create_engine(platform_url),
        "admin": create_engine(admin_url),
    }
    saved = {k: core_db.get_engine(k) for k in ("app", "platform")}
    core_db.set_engine("app", engines["app"])
    core_db.set_engine("platform", engines["platform"])
    try:
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(
            ["--tenants", "1", "--code-prefix", "migr"],
            settings=SETTINGS,
            stdout=out,
            stderr=err,
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK, err.getvalue()
        yield cfg, admin_url
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def _count(admin_url: str, sql: str) -> int:
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            return int(conn.execute(text(sql)).scalar_one())
    finally:
        engine.dispose()


def test_CLAUDE_6_12_every_step_reversible_with_data(populated: tuple[Config, str]) -> None:
    cfg, admin_url = populated
    assert _count(admin_url, "SELECT count(*) FROM core.memberships") > 0
    assert _count(admin_url, "SELECT count(*) FROM audit.events") > 0
    revisions = [
        r.revision for r in ScriptDirectory.from_config(cfg).walk_revisions()
    ]  # head first
    for i, rev in enumerate(revisions[:-1]):
        below = revisions[i + 1]
        command.downgrade(cfg, below)
        command.upgrade(cfg, rev)
        command.downgrade(cfg, below)
    command.upgrade(cfg, "head")
