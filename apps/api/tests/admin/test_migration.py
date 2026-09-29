"""0031_admin: schema facts, database checks and a populated round trip (CLAUDE.md §6.1, §6.12,
§8; docs/05 §7.4; FR-ADM-001, FR-ADM-002).

A fresh database is migrated to head, seeded with a synthetic school (``seed-synthetic``), and
given a full export and retention settings; the walk head -> 0030 -> head must succeed with that
data present and leave every other table untouched.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import Engine, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.admin import models
from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.model_base import Base
from app.devtools import seed_synthetic as cli
from app.devtools import seeder

pytestmark = pytest.mark.db
DB = "schoolos_admin_migration"
BEFORE = "0030_import_cell_edits"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


@pytest.mark.parametrize(
    "model", [models.TenantExport, models.RetentionSetting], ids=lambda m: m.__tablename__
)
def test_FR_ADM_001_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    insp = inspect(admin_engine)
    db_cols = {c["name"]: c for c in insp.get_columns(table.name, schema=table.schema)}
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def test_SEC_001_admin_tables_force_rls_and_the_app_cannot_delete(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'ops' "
                "AND c.relname IN ('tenant_exports', 'retention_settings')"
            )
        ).all()
        policies = c.execute(
            text(
                "SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'ops' "
                "AND tablename IN ('tenant_exports', 'retention_settings')"
            )
        ).all()
    assert {r.relname for r in rows} == {"tenant_exports", "retention_settings"}
    assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows)
    # ADR-0029 (0032_offboarding): plus the restrictive offboarding purge policy for sos_purger
    # only (its shape is pinned by tests/tenancy/test_offboarding_purge.py).
    assert {(p.tablename, p.policyname) for p in policies} == {
        ("tenant_exports", "tenant_isolation"),
        ("retention_settings", "tenant_isolation"),
        ("tenant_exports", "offboarding_purge"),
        ("retention_settings", "offboarding_purge"),
    }
    for table in ("tenant_exports", "retention_settings"):
        grants = _scalar(
            admin_engine,
            "SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) "
            "FROM information_schema.role_table_grants WHERE grantee = 'sos_app' "
            "AND table_schema = 'ops' AND table_name = :t",
            t=table,
        )
        assert "DELETE" not in grants
        assert "TRUNCATE" not in grants
        assert "UPDATE" not in grants  # column grants only (workflow / rules)
        assert "INSERT" in grants
        assert "SELECT" in grants
    frozen = {
        r[0]
        for r in admin_engine.connect().execute(
            text(
                "SELECT column_name FROM information_schema.column_privileges "
                "WHERE grantee = 'sos_app' AND table_schema = 'ops' "
                "AND table_name = 'tenant_exports' AND privilege_type = 'UPDATE'"
            )
        )
    }
    # What was requested, by whom, never changes (only the workflow columns).
    assert not frozen & {"include_sensitive", "requested_by", "requested_by_membership", "id"}
    assert {"status", "object_key", "expires_at", "files_deleted_at"} <= frozen


def _school(
    admin_engine: Engine, tenant_id: uuid.UUID | None = None
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(tenant, user, membership) of an active member: of ``tenant_id``, else the first one."""
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id FROM core.memberships m "
                "WHERE m.status = 'active' AND (CAST(:t AS uuid) IS NULL OR m.tenant_id = :t) "
                "ORDER BY m.created_at LIMIT 1"
            ),
            {"t": tenant_id},
        ).one()
    return row[0], row[1], row[2]


def _insert_export(
    admin_engine: Engine, tenant: uuid.UUID | None = None, **overrides: Any
) -> uuid.UUID:
    tenant_id, user_id, membership_id = _school(admin_engine, tenant)
    export_id: uuid.UUID = overrides.pop("id", uuid.uuid4())
    values = {
        "i": export_id,
        "t": tenant_id,
        "u": user_id,
        "m": membership_id,
        "s": overrides.pop("status", "failed"),
        "e": overrides.pop("error_code", "worker_error"),
        "k": overrides.pop("object_key", None),
    }
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.tenant_exports (id, tenant_id, status, error_code, requested_by, "
                "requested_by_membership, object_key) VALUES (:i, :t, :s, :e, :u, :m, :k)"
            ),
            values,
        )
    return export_id


def test_FR_ADM_001_archive_key_must_be_the_rows_own_school_and_id(
    school: Any, admin_engine: Engine
) -> None:
    tenant_id = school.tenant_id
    export_id = uuid.uuid4()
    with pytest.raises(DBAPIError, match="tenant_exports_key_in_tenant"):
        _insert_export(
            admin_engine,
            tenant_id,
            id=export_id,
            object_key=f"t/{uuid.uuid4()}/tenant-export/{export_id}.zip",
        )
    with pytest.raises(DBAPIError, match="tenant_exports_key_in_tenant"):
        _insert_export(
            admin_engine,
            tenant_id,
            id=export_id,
            object_key=f"t/{tenant_id}/exports/{export_id}/x.zip",
        )
    _insert_export(
        admin_engine,
        tenant_id,
        id=export_id,
        object_key=f"t/{tenant_id}/tenant-export/{export_id}.zip",
    )


def test_FR_ADM_001_one_live_export_per_school(school: Any, admin_engine: Engine) -> None:
    tenant_id = school.tenant_id
    with admin_engine.begin() as c:  # free the school first (earlier tests may have left one)
        c.execute(
            text(
                "UPDATE ops.tenant_exports SET status = 'failed', error_code = 'worker_error' "
                "WHERE tenant_id = :t AND status IN ('queued','running')"
            ),
            {"t": tenant_id},
        )
    _insert_export(admin_engine, tenant_id, status="queued", error_code=None)
    with pytest.raises(DBAPIError, match="tenant_exports_one_live"):
        _insert_export(admin_engine, tenant_id, status="running", error_code=None)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.tenant_exports SET status = 'failed', error_code = 'worker_error' "
                "WHERE tenant_id = :t AND status IN ('queued','running')"
            ),
            {"t": tenant_id},
        )


@pytest.mark.parametrize(
    ("rules", "ok"),
    [
        ('{"import_raw_files": 30}', True),
        ('{"import_raw_files": 30, "exports": 1}', True),
        ("{}", True),
        ('{"import_raw_files": 0}', False),
        ('{"import_raw_files": 3651}', False),
        ('{"import_raw_files": 1.5}', False),
        ('{"import_raw_files": "30"}', False),
        ('{"Bad Key": 30}', False),
        ("[]", False),
    ],
)
def test_FR_ADM_002_retention_rules_shape_is_checked_by_the_database(
    school: Any, admin_engine: Engine, rules: str, ok: bool
) -> None:
    tenant_id = school.tenant_id
    stmt = text(
        "INSERT INTO ops.retention_settings (id, tenant_id, rules) "
        "VALUES (:i, :t, CAST(:r AS jsonb)) ON CONFLICT (tenant_id) DO NOTHING"
    )
    conn = admin_engine.connect()
    trans = conn.begin()
    try:
        # A fresh savepoint per attempt; nothing is kept.
        conn.execute(
            text("DELETE FROM ops.retention_settings WHERE tenant_id = :t"), {"t": tenant_id}
        )
        if ok:
            conn.execute(stmt, {"i": uuid.uuid4(), "t": tenant_id, "r": rules})
        else:
            with pytest.raises(DBAPIError, match="retention_settings_rules"):
                conn.execute(stmt, {"i": uuid.uuid4(), "t": tenant_id, "r": rules})
    finally:
        trans.rollback()
        conn.close()


@dataclass
class _Walk:
    cfg: Config
    admin: Engine


@pytest.fixture
def populated(test_database: Any, make_alembic_config: Callable[[str], Config]) -> Iterator[_Walk]:
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
            ["--tenants", "1", "--code-prefix", "admm"],
            settings=SETTINGS,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK
        yield _Walk(cfg, engines["admin"])
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def test_CLAUDE_6_12_admin_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    tenant_id, _, _ = _school(admin)
    export_id = _insert_export(admin)
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.retention_settings (id, tenant_id, rules) "
                "VALUES (:i, :t, '{\"import_raw_files\": 30}'::jsonb)"
            ),
            {"i": uuid.uuid4(), "t": tenant_id},
        )
    students = _scalar(admin, "SELECT count(*) FROM sis.students")
    events = _scalar(admin, "SELECT count(*) FROM audit.events")
    memberships = _scalar(admin, "SELECT count(*) FROM core.memberships")
    assert _scalar(admin, "SELECT count(*) FROM ops.tenant_exports WHERE id = :i", i=export_id) == 1

    command.downgrade(populated.cfg, BEFORE)
    assert _scalar(admin, "SELECT to_regclass('ops.tenant_exports') IS NULL") is True
    assert _scalar(admin, "SELECT to_regclass('ops.retention_settings') IS NULL") is True
    assert _scalar(admin, "SELECT version_num FROM ops.alembic_version") == BEFORE
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students
    assert _scalar(admin, "SELECT count(*) FROM audit.events") == events
    assert _scalar(admin, "SELECT count(*) FROM core.memberships") == memberships

    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM ops.tenant_exports") == 0
    assert _scalar(admin, "SELECT count(*) FROM ops.retention_settings") == 0
    command.downgrade(populated.cfg, BEFORE)
    command.upgrade(populated.cfg, "head")
    assert (
        _scalar(
            admin,
            "SELECT count(*) FROM pg_indexes WHERE schemaname = 'ops' "
            "AND indexname = 'tenant_exports_one_live'",
        )
        == 1
    )
