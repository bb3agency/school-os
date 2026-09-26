"""0008_sis_students on a populated database (CLAUDE.md §6.12, §8; docs/05 §14).

A fresh database is migrated to head and seeded with a synthetic school (``seed-synthetic``),
then students with encrypted values, guardians and enrolments are added through the service.
The walk 0008 -> 0007 -> 0008 -> 0007 -> head must succeed with that data present, and the
global attribute catalog must be re-seeded (deterministic ids, read-only for the app).
"""

from __future__ import annotations

import io
import sys
import uuid
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
from app.students import service as students
from app.students.schemas import GuardianCreate, StudentCreate, ValueIn

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
DB = "schoolos_students_migration"
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
            ["--tenants", "1", "--code-prefix", "stum"],
            settings=SETTINGS,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK
        _add_students(engines["admin"])
        yield cfg, engines["admin"]
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def _add_students(admin: Engine) -> None:
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
        for i in range(3):
            out = students.create_student(
                db,
                ctx,
                StudentCreate(
                    values=[
                        ValueIn(
                            attribute_key="full_name",
                            source="admission_register",
                            value=f"Synthetica Walk {i}",
                        ),
                        ValueIn(
                            attribute_key="health_notes",
                            source="parent_form",
                            value="Synthetic note",
                        ),
                    ],
                    section_id=row.section_id,
                ),
            )
            students.record_value(db, ctx, out.id, "mother_tongue", "parent_form", "Telugu")
            students.record_value(db, ctx, out.id, "mother_tongue", "parent_form", "Hindi")
            students.add_guardian(
                db,
                ctx,
                out.id,
                GuardianCreate(
                    relationship="mother", full_name="Synthetica Mother", phone="9876500000"
                ),
            )


def _scalar(admin: Engine, sql: str) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql)).scalar_one()


def test_CLAUDE_6_12_sis_migration_reversible_with_data(populated: tuple[Config, Engine]) -> None:
    cfg, admin = populated
    assert _scalar(admin, "SELECT count(*) FROM sis.attribute_values") >= 12
    ids = _scalar(admin, "SELECT array_agg(id ORDER BY key) FROM sis.attribute_definitions")
    command.downgrade(cfg, "0007_accept_invitations")
    assert _scalar(admin, "SELECT to_regclass('sis.students') IS NULL") is True
    assert _scalar(admin, "SELECT count(*) FROM core.memberships") > 0, "earlier data untouched"
    command.upgrade(cfg, "0008_sis_students")
    assert _scalar(admin, "SELECT array_agg(id ORDER BY key) FROM sis.attribute_definitions") == ids
    command.downgrade(cfg, "0007_accept_invitations")
    command.upgrade(cfg, "head")
    assert _scalar(
        admin, "SELECT count(*) FROM sis.attribute_definitions WHERE tenant_id IS NULL"
    ) == len(ids)
    assert all(isinstance(i, uuid.UUID) for i in ids)
