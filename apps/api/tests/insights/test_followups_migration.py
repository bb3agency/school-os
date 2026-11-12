"""0102_ew_followups on a populated database (FT-13, FT-14; lead decision D86; FR-EW-025;
CLAUDE.md §6.12).

Two synthetic schools are seeded (``seed-synthetic``), a flag with a planned review and an action
are added, and the walk head -> 0096 -> head succeeds with that data present; the downgrade drops
the two columns (and the permission keys no role holds), the upgrade brings them back.
"""

from __future__ import annotations

import datetime as dt
import io
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.devtools import seed_synthetic as cli
from app.devtools import seeder

pytestmark = pytest.mark.db
DB = "schoolos_ew_followups_migration"
REV = "0102_ew_followups"
BELOW = "0096_compliance_register"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)
NEW_KEYS = ("insights.counts_read", "completeness.read", "completeness.remind")


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
            ["--tenants", "2", "--code-prefix", "ewfollow"],
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


def _columns(admin: Engine) -> set[tuple[str, str]]:
    with admin.connect() as c:
        return {
            (r.table_name, r.column_name)
            for r in c.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'sis' AND column_name = 'review_on'"
                )
            )
        }


def _keys(admin: Engine) -> set[str]:
    with admin.connect() as c:
        return set(
            c.execute(
                text("SELECT key FROM core.permissions WHERE key = ANY(:k)"), {"k": list(NEW_KEYS)}
            ).scalars()
        )


def _flag(admin: Engine) -> uuid.UUID:
    """A synthetic manual flag with a planned review and one action (rows written as admin)."""
    with admin.begin() as c:
        row = c.execute(
            text(
                "SELECT s.tenant_id, s.id AS section_id, m.id AS membership_id, m.user_id "
                "FROM core.sections s JOIN core.memberships m ON m.tenant_id = s.tenant_id "
                "ORDER BY s.id LIMIT 1"
            )
        ).one()
        student_id = uuid.uuid4()
        c.execute(
            text("INSERT INTO sis.students (id, tenant_id, status) VALUES (:i, :t, 'active')"),
            {"i": student_id, "t": row.tenant_id},
        )
        flag_id = uuid.uuid4()
        today = dt.datetime.now(dt.UTC).date()
        c.execute(
            text(
                "INSERT INTO sis.insight_flags (id, tenant_id, student_id, section_id, indicator, "
                "rule, rules_version, basis, status, raised_on, due_on, raised_by, "
                "first_action_at, review_on) VALUES (:i, :t, :s, :sec, 'behaviour', 'manual', 1, "
                ":b, 'in_progress', :d, :due, :u, now(), :r)"
            ),
            {
                "i": flag_id,
                "t": row.tenant_id,
                "s": student_id,
                "sec": row.section_id,
                "b": f"manual:{flag_id}",
                "d": today,
                "due": today + dt.timedelta(days=7),
                "u": row.user_id,
                "r": today + dt.timedelta(days=5),
            },
        )
        c.execute(
            text(
                "INSERT INTO sis.flag_actions (id, tenant_id, flag_id, kind, acted_on, created_by, "
                "created_by_membership, review_on) VALUES (:i, :t, :f, 'called_parent', :d, :u, "
                ":m, :r)"
            ),
            {
                "i": uuid.uuid4(),
                "t": row.tenant_id,
                "f": flag_id,
                "d": today,
                "u": row.user_id,
                "m": row.membership_id,
                "r": today + dt.timedelta(days=5),
            },
        )
    return flag_id


def test_FR_EW_025_columns_checks_and_round_trip(populated: tuple[Config, Engine]) -> None:
    cfg, admin = populated
    assert _columns(admin) == {("insight_flags", "review_on"), ("flag_actions", "review_on")}
    assert _keys(admin) == set(NEW_KEYS)
    flag_id = _flag(admin)
    # A closed flag cannot keep a planned review (FR-EW-022).
    with pytest.raises(DBAPIError, match="insight_flags_review_open"), admin.begin() as c:
        c.execute(
            text(
                "UPDATE sis.insight_flags SET status = 'closed', closed_at = now(), "
                "closed_by = raised_by, close_reason = 'improved' WHERE id = :f"
            ),
            {"f": flag_id},
        )
    command.downgrade(cfg, BELOW)
    assert _columns(admin) == set()
    # Keys a seeded school's roles already hold stay (removed only when no role holds them).
    with admin.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM sis.insight_flags WHERE id = :f"), {"f": flag_id}
            ).scalar_one()
            == 1
        )
    command.upgrade(cfg, REV)
    assert _columns(admin) == {("insight_flags", "review_on"), ("flag_actions", "review_on")}
    assert _keys(admin) == set(NEW_KEYS)
