"""0017_exports: schema facts, grants and a populated round trip (CLAUDE.md §6.1, §6.12, §8;
docs/05 §7.3, §14).

A fresh database is migrated to head, seeded with a synthetic school (``seed-synthetic``) and
exports are made through the service (one ready, one failed). Walking 0017 down and up again must
succeed with that data present (the downgrade drops the export records: documented as lossy).
"""

from __future__ import annotations

import io
import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import sqlalchemy.exc
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import Engine, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.db import tenant_session
from app.core.model_base import Base
from app.devtools import seed_synthetic as cli
from app.devtools import seeder
from app.exports import models

pytestmark = pytest.mark.db
EX = sys.modules["sos_test_exports_objects"]
REVISION = "0017_exports"
PREVIOUS = "0014_change_requests"
DB = "schoolos_exports_migration"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


@pytest.mark.parametrize("model", [models.Export, models.ExportFile], ids=lambda m: m.__tablename__)
def test_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    insp = inspect(admin_engine)
    db_cols = {c["name"]: c for c in insp.get_columns(table.name, schema=table.schema)}
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


def test_SEC_001_export_tables_rls_and_composite_fks(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid "
                " AND p.polname = 'tenant_isolation') AS policies "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'ops' AND c.relname IN ('exports', 'export_files')"
            )
        ).all()
        fks: dict[str, str] = dict(
            c.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid IN ('ops.exports'::regclass, 'ops.export_files'::regclass) "
                    "AND contype = 'f'"
                )
            ).all()
        )
    assert {r.relname for r in rows} == {"exports", "export_files"}
    assert all(r.relrowsecurity and r.relforcerowsecurity and r.policies == 1 for r in rows)
    assert "(tenant_id, requested_by_membership)" in fks["exports_membership_fk"]
    assert "(tenant_id, job_id)" in fks["exports_job_fk"]
    assert "(tenant_id, export_id)" in fks["export_files_export_fk"]


def test_FR_EXP_003_export_records_cannot_be_rewritten_or_deleted(
    school: Any, admin_engine: Engine
) -> None:
    EX.student(school)
    export_id = EX.queued_export(school, "principal")
    statements = [
        "DELETE FROM ops.exports WHERE id = :i",
        "UPDATE ops.exports SET student_count = 1 WHERE id = :i",
        "UPDATE ops.exports SET kind = 'student_list' WHERE id = :i",
        "DELETE FROM ops.export_files WHERE export_id = :i",
        "UPDATE ops.export_files SET object_key = 'x' WHERE export_id = :i",
        "TRUNCATE ops.exports",
    ]
    for sql in statements:
        with (
            pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"),
            tenant_session(school.tenant_id) as db,
        ):
            db.execute(text(sql), {"i": export_id})
    # Workflow columns may change; what was requested may not (trigger exports_frozen).
    with tenant_session(school.tenant_id) as db:
        db.execute(
            text("UPDATE ops.exports SET version = version + 1 WHERE id = :i"), {"i": export_id}
        )
    # Even the table owner cannot rewrite the request (trigger exports_frozen).
    for column, value in (("student_count", "7"), ("include_sensitive", "true")):
        with (
            pytest.raises(sqlalchemy.exc.IntegrityError, match="exports_frozen"),
            admin_engine.begin() as c,
        ):
            c.execute(
                text(f"UPDATE ops.exports SET {column} = {value} WHERE id = :i"),
                {"i": export_id},
            )


def test_file_keys_must_sit_under_their_own_export(school: Any, admin_engine: Engine) -> None:
    EX.student(school)
    export_id = EX.queued_export(school, "principal")
    with tenant_session(school.tenant_id) as db, pytest.raises(sqlalchemy.exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO ops.export_files (id, tenant_id, export_id, format, object_key, "
                "content_type, size_bytes, sha256) VALUES (gen_random_uuid(), :t, :e, 'csv', "
                ":k, 'text/csv', 1, decode(repeat('00', 32), 'hex'))"
            ),
            {
                "t": school.tenant_id,
                "e": export_id,
                "k": f"t/{school.tenant_id}/exports/{school.tenant_id}/students.csv",
            },
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
            ["--tenants", "1", "--code-prefix", "expm"],
            settings=SETTINGS,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK
        _add_exports(engines["admin"])
        yield cfg, engines["admin"]
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def _add_exports(admin: Engine) -> None:
    EX.install()
    with admin.connect() as c:
        row = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id AS membership_id, s.id AS section_id "
                "FROM core.memberships m JOIN core.sections s ON s.tenant_id = m.tenant_id "
                "JOIN core.academic_years y ON y.id = s.academic_year_id AND y.is_current "
                "JOIN core.membership_roles mr ON mr.membership_id = m.id "
                "JOIN core.roles r ON r.id = mr.role_id AND r.key = 'principal' "
                "WHERE m.status = 'active' ORDER BY m.created_at, s.id LIMIT 1"
            )
        ).one()
    person = EX.W.Person("principal", row.user_id, row.membership_id, "sub", "Synthetic")
    school = EX.W.School(
        row.tenant_id,
        people={"principal": person, "owner": person},
        ids={"section_9a": row.section_id},
    )
    EX.student(school)
    EX.ready_export(school, "principal")
    failed = EX.queued_export(school, "principal")
    from app.exports import service

    service.abandon(school.tenant_id, failed)


def _scalar(admin: Engine, sql: str) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql)).scalar_one()


def test_CLAUDE_6_12_exports_migration_reversible_with_data(
    populated: tuple[Config, Engine],
) -> None:
    cfg, admin = populated
    assert _scalar(admin, "SELECT count(*) FROM ops.exports") == 2
    assert _scalar(admin, "SELECT count(*) FROM ops.export_files") == 1
    command.downgrade(cfg, PREVIOUS)
    assert _scalar(admin, "SELECT to_regclass('ops.exports') IS NULL") is True
    assert _scalar(admin, "SELECT count(*) FROM pg_proc WHERE proname = 'tg_exports_frozen'") == 0
    command.upgrade(cfg, REVISION)
    assert _scalar(admin, "SELECT count(*) FROM ops.exports") == 0
    command.downgrade(cfg, PREVIOUS)
    command.upgrade(cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM pg_trigger WHERE tgname = 'exports_frozen'") == 1
