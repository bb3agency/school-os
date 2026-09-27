"""0013_dq on a populated database (CLAUDE.md §6.12, §8; docs/05 §14).

A fresh database is migrated to head and seeded with a synthetic school (``seed-synthetic``);
students with conflicting values are checked by the real engine, so runs and findings exist.
The walk 0013 -> 0011 -> 0013 -> 0011 -> head must succeed with that data present, and the
tables must come back with RLS forced and no DELETE for the app.
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
from app.dq import service as dq
from app.students import service as students
from app.students.schemas import StudentCreate, ValueIn

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
DB = "schoolos_dq_migration"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


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
            ["--tenants", "1", "--code-prefix", "dqm"],
            settings=SETTINGS,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK
        _add_findings(engines["admin"])
        yield cfg, engines["admin"]
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def _add_findings(admin: Engine) -> None:
    SW.configure_keyring()
    with admin.connect() as c:
        row = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id AS membership_id, s.id AS section_id "
                "FROM core.memberships m JOIN core.sections s ON s.tenant_id = m.tenant_id "
                "JOIN core.academic_years y ON y.id = s.academic_year_id AND y.is_current "
                "WHERE m.status = 'active' ORDER BY m.created_at LIMIT 1"
            )
        ).one()
    person = SW.W.Person("office_admin", row.user_id, row.membership_id, "sub", "Synthetic")
    ctx = SW.ctx_for(row.tenant_id, person, "office_admin")
    with tenant_session(row.tenant_id, row.user_id) as db:
        ids = []
        for i, gender in enumerate(("female", "male", "female")):
            out = students.create_student(
                db,
                ctx,
                StudentCreate(
                    values=[
                        ValueIn(
                            attribute_key="full_name",
                            source="admission_register",
                            value=f"Synthetica Walk {'ABC'[i]}",
                        ),
                        ValueIn(attribute_key="gender", source="admission_register", value="male"),
                        ValueIn(
                            attribute_key="aadhaar_gender_as_printed",
                            source="aadhaar_as_printed",
                            value=gender,
                        ),
                    ],
                    section_id=row.section_id,
                ),
            )
            ids.append(out.id)
        dq.run_checks(db, ctx, student_ids=ids, profile_key="cisce-registration-2026")


def _scalar(admin: Engine, sql: str) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql)).scalar_one()


def test_CLAUDE_6_12_dq_migration_reversible_with_data(populated: tuple[Config, Engine]) -> None:
    cfg, admin = populated
    assert _scalar(admin, "SELECT count(*) FROM sis.dq_findings WHERE rule_id = 'DQ-003'") == 2
    assert _scalar(admin, "SELECT count(*) FROM sis.dq_runs") == 1
    command.downgrade(cfg, "0011_breakglass")
    assert _scalar(admin, "SELECT to_regclass('sis.dq_findings') IS NULL") is True
    assert _scalar(admin, "SELECT count(*) FROM sis.students") >= 3, "earlier data untouched"
    command.upgrade(cfg, "0013_dq")
    command.downgrade(cfg, "0011_breakglass")
    command.upgrade(cfg, "head")
    for table in ("dq_runs", "dq_findings"):
        forced = _scalar(
            admin,
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
            f"WHERE oid = 'sis.{table}'::regclass",
        )
        assert forced is True
        assert (
            _scalar(admin, f"SELECT has_table_privilege('sos_app', 'sis.{table}', 'DELETE')")
            is False
        )
