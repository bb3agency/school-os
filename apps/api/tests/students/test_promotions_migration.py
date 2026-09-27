"""0022_promotions on a populated database (CLAUDE.md §6.12, §8; FR-TEN-011).

A fresh database is migrated to head and seeded with a synthetic school (``seed-synthetic``);
a promotion is committed through the service (so promotion runs and items exist), then the walk
0022 -> 0021 -> 0022 -> 0021 -> head must succeed with that data present. Downgrading drops
only the undo bookkeeping: the enrolments the promotion opened and closed stay.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
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
from app.devtools import seed_synthetic as cli
from app.devtools import seeder

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
DB = "schoolos_promotions_migration"
REVISION, BELOW = "0022_promotions", "0021_kb_tables"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


def _support() -> Any:
    name = "sos_test_promotion_support"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("promotion_support.py")
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


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
            ["--tenants", "1", "--code-prefix", "prom"],
            settings=SETTINGS,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK
        _promote(engines["admin"])
        yield cfg, engines["admin"]
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def _promote(admin: Engine) -> None:
    """Commit one promotion (a student in class IX of a fresh year pair) in the seeded school."""
    with admin.connect() as c:
        row = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id AS membership_id FROM core.memberships m "
                "WHERE m.status = 'active' ORDER BY m.created_at LIMIT 1"
            )
        ).one()
    person = SW.W.Person("office_admin", row.user_id, row.membership_id, "sub", "Synthetic")
    school = SW.W.School(row.tenant_id, people={"owner": person})
    _support().committed(school, "IX")


def _scalar(admin: Engine, sql: str) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql)).scalar_one()


def test_CLAUDE_6_12_promotions_migration_reversible_with_data(
    populated: tuple[Config, Engine],
) -> None:
    cfg, admin = populated
    assert _scalar(admin, "SELECT count(*) FROM sis.promotion_runs") == 1
    assert _scalar(admin, "SELECT count(*) FROM sis.promotion_items") == 1
    enrolments = _scalar(admin, "SELECT count(*) FROM sis.enrollments")
    command.downgrade(cfg, BELOW)
    assert _scalar(admin, "SELECT to_regclass('sis.promotion_runs') IS NULL") is True
    assert _scalar(admin, "SELECT to_regclass('sis.promotion_items') IS NULL") is True
    assert _scalar(admin, "SELECT count(*) FROM sis.enrollments") == enrolments
    command.upgrade(cfg, REVISION)
    assert _scalar(admin, "SELECT count(*) FROM sis.promotion_runs") == 0
    assert _scalar(
        admin,
        "SELECT relforcerowsecurity AND relrowsecurity FROM pg_class "
        "WHERE oid = 'sis.promotion_items'::regclass",
    )
    command.downgrade(cfg, BELOW)
    command.upgrade(cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM sis.enrollments") == enrolments
