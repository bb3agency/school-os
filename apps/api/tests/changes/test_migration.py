"""0014_change_requests on a populated database (CLAUDE.md §6.12, §8; docs/05 §14).

A fresh database is migrated to head and seeded with a synthetic school (``seed-synthetic``);
students, evidence documents and change requests (one approved, one pending) are added through
the services. Walking 0014 down and up again must succeed with that data present. The downgrade
drops the requests (documented as lossy); values they produced keep their ``change_request_id``
and the re-created FK then stays ``NOT VALID`` instead of failing the upgrade.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import io
import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.changes import service as changes
from app.changes.schemas import ApproveIn, ChangeRequestCreate
from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.db import tenant_session
from app.devtools import seed_synthetic as cli
from app.devtools import seeder

pytestmark = pytest.mark.db
CR = sys.modules["sos_test_changes_objects"]
REVISION = "0014_change_requests"
FORCED = (
    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
    "WHERE oid = 'sis.attribute_values'::regclass"
)
DB = "schoolos_changes_migration"
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
            ["--tenants", "1", "--code-prefix", "crmig"],
            settings=SETTINGS,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK
        _add_requests(engines["admin"])
        yield cfg, engines["admin"]
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def _add_requests(admin: Engine) -> None:
    CR.SW.configure_keyring()
    CR.D.memory_store()
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id AS membership_id, s.id AS section_id "
                "FROM core.memberships m JOIN core.sections s ON s.tenant_id = m.tenant_id "
                "JOIN core.academic_years y ON y.id = s.academic_year_id AND y.is_current "
                "WHERE m.status = 'active' ORDER BY m.created_at, s.id LIMIT 40"
            )
        ).all()
    first = rows[0]
    tenant_id = first.tenant_id
    school = CR.W.School(tenant_id, ids={"section_9a": first.section_id})
    maker = CR.W.Person("office_admin", first.user_id, first.membership_id, "sub", "Maker")
    checker = CR.W.add_member(admin, tenant_id, ["principal"])
    school.people["owner"] = maker
    maker_ctx = CR.SW.ctx_for(tenant_id, maker, "office_admin")
    checker_ctx = dataclasses.replace(
        CR.SW.ctx_for(tenant_id, checker, "principal"), auth_time=dt.datetime.now(dt.UTC)
    )
    requests = []
    for _ in range(2):
        sid = CR.student(school)
        doc = CR.evidence(admin, school, maker)
        with tenant_session(tenant_id, maker.user_id) as s:
            requests.append(
                changes.submit(
                    s,
                    maker_ctx,
                    ChangeRequestCreate(
                        student_id=sid,
                        attribute_key="dob",
                        new_value="2012-03-15",
                        reason=CR.REASON,
                        evidence_document_id=doc,
                    ),
                )
            )
    with tenant_session(tenant_id, checker.user_id) as s:
        changes.approve(s, checker_ctx, requests[0].id, ApproveIn(), expected_version=1)


def _scalar(admin: Engine, sql: str) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql)).scalar_one()


def test_CLAUDE_6_12_change_requests_migration_reversible_with_data(
    populated: tuple[Config, Engine],
) -> None:
    cfg, admin = populated
    previous = ScriptDirectory.from_config(cfg).get_revision(REVISION).down_revision
    assert isinstance(previous, str)
    assert _scalar(admin, "SELECT count(*) FROM sis.change_requests") == 2
    linked = "SELECT count(*) FROM sis.attribute_values WHERE change_request_id IS NOT NULL"
    assert _scalar(admin, linked) == 1
    fk = (
        "SELECT convalidated FROM pg_constraint "
        "WHERE conname = 'attribute_values_change_request_fk'"
    )
    assert _scalar(admin, fk) is True

    command.downgrade(cfg, previous)
    assert _scalar(admin, "SELECT to_regclass('sis.change_requests') IS NULL") is True
    assert _scalar(admin, linked) == 1, "student history is untouched by the downgrade"
    assert _scalar(admin, "SELECT count(*) FROM sis.students") >= 2

    command.upgrade(cfg, REVISION)
    assert _scalar(admin, "SELECT count(*) FROM sis.change_requests") == 0
    assert _scalar(admin, fk) is False, "stale ids from the lossy downgrade: FK kept NOT VALID"
    assert _scalar(admin, FORCED) is True, "FORCE RLS restored after the failed validation"

    command.downgrade(cfg, previous)
    command.upgrade(cfg, "head")
    assert _scalar(admin, "SELECT to_regclass('sis.change_requests') IS NOT NULL") is True
    assert _scalar(admin, FORCED) is True
