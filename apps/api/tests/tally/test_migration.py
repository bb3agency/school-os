"""0036_tally: schema facts, database checks and a populated round trip (CLAUDE.md §6.1, §6.12,
§8; docs/05 §7.4; ADR-0032; FR-TALLY-001, FR-TALLY-002, FR-TALLY-005, FR-TALLY-006).

A fresh database is migrated to head, seeded with a synthetic school (``seed-synthetic``) and
given a code, a device, a group, a sync, a party and a link; the walk head -> previous revision ->
head must succeed with that data present and leave every other table untouched.
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
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.model_base import Base
from app.devtools import seed_synthetic as cli
from app.devtools import seeder
from app.tally import models

pytestmark = pytest.mark.db
DB = "schoolos_tally_migration"
REVISION = "0036_tally"
TABLES = {
    ("ops", "tally_enrolment_codes"),
    ("ops", "tally_devices"),
    ("ops", "tally_groups"),
    ("ops", "tally_syncs"),
    ("ops", "tally_parties"),
    ("ops", "tally_party_links"),
}
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


@pytest.mark.parametrize(
    "model",
    [
        models.EnrolmentCode,
        models.Device,
        models.Group,
        models.Sync,
        models.Party,
        models.PartyLink,
    ],
    ids=lambda m: m.__tablename__,
)
def test_FR_TALLY_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    insp = inspect(admin_engine)
    db_cols = {c["name"]: c for c in insp.get_columns(table.name, schema=table.schema)}
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


def _update_columns(engine: Engine, table: str) -> set[str]:
    with engine.connect() as c:
        return {
            r[0]
            for r in c.execute(
                text(
                    "SELECT column_name FROM information_schema.column_privileges "
                    "WHERE grantee = 'sos_app' AND table_schema = 'ops' AND table_name = :t "
                    "AND privilege_type = 'UPDATE'"
                ),
                {"t": table},
            )
        }


def test_SEC_001_tables_force_rls_with_narrow_grants(admin_engine: Engine) -> None:
    names = sorted(t for _, t in TABLES)
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'ops' AND c.relname = ANY(:names)"
            ),
            {"names": names},
        ).all()
        policies = {
            (r.schemaname, r.tablename, r.policyname)
            for r in c.execute(
                text(
                    "SELECT schemaname, tablename, policyname FROM pg_policies "
                    "WHERE tablename = ANY(:names)"
                ),
                {"names": names},
            )
        }
        privileges = {
            (r.table_name, r.privilege_type)
            for r in c.execute(
                text(
                    "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = 'sos_app' AND table_schema = 'ops' "
                    "AND table_name = ANY(:names)"
                ),
                {"names": names},
            )
        }
    assert {(r.nspname, r.relname) for r in rows} == TABLES
    assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows)
    for schema, table in TABLES:
        assert (schema, table, "tenant_isolation") in policies
        assert (schema, table, "offboarding_purge") in policies  # ADR-0029
        assert (table, "TRUNCATE") not in privileges
        assert (table, "UPDATE") not in privileges  # column grants only
        assert (table, "SELECT") in privileges
    # Codes, devices, groups and parties are never deleted by the app (used, revoked, marked).
    for table in ("tally_enrolment_codes", "tally_devices", "tally_groups", "tally_parties"):
        assert (table, "DELETE") not in privileges, table
    # Who enrolled a device, with which code and the code's hash never change.
    assert _update_columns(admin_engine, "tally_enrolment_codes") == {"used_at"}
    device_cols = _update_columns(admin_engine, "tally_devices")
    assert not device_cols & {"enrolled_by", "enrolment_code_id", "name", "tenant_id"}
    assert {"status", "key_id", "key_ciphertext", "revoked_at"} <= device_cols
    assert _update_columns(admin_engine, "tally_syncs") == set()
    assert _update_columns(admin_engine, "tally_party_links") == set()


def _owner(world: Any) -> tuple[uuid.UUID, uuid.UUID]:
    owner = world.a.people["owner"]
    return world.a.tenant_id, owner.user_id


def _code(c: Connection, tenant_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    code_id = uuid.uuid4()
    c.execute(
        text(
            "INSERT INTO ops.tally_enrolment_codes (id, tenant_id, code_hash, device_name, "
            "created_by, expires_at) VALUES (:i, :t, :h, 'Office PC', :u, now() + interval "
            "'30 minutes')"
        ),
        {"i": code_id, "t": tenant_id, "h": uuid.uuid4().bytes * 2, "u": user_id},
    )
    return code_id


def test_FR_TALLY_002_a_revoked_device_keeps_no_key(world: Any, admin_engine: Engine) -> None:
    tenant_id, user_id = _owner(world)
    with admin_engine.begin() as c:
        code_id = _code(c, tenant_id, user_id)
    insert = text(
        "INSERT INTO ops.tally_devices (id, tenant_id, name, status, enrolment_code_id, "
        "key_id, key_ciphertext, enrolled_by, revoked_at) VALUES (:i, :t, 'PC', "
        "'revoked', :c, 'tdk-abcdefghijklmnopqrst', '\\x01', :u, now())"
    )
    params = {"i": uuid.uuid4(), "t": tenant_id, "c": code_id, "u": user_id}
    with pytest.raises(DBAPIError, match="tally_devices_keys_by_status"), admin_engine.begin() as c:
        c.execute(insert, params)


def test_FR_TALLY_001_codes_are_stored_as_a_32_byte_hash(world: Any, admin_engine: Engine) -> None:
    tenant_id, user_id = _owner(world)
    with pytest.raises(DBAPIError, match="code_hash"), admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.tally_enrolment_codes (id, tenant_id, code_hash, device_name, "
                "created_by, expires_at) VALUES (:i, :t, :h, 'PC', :u, now() + interval '1 hour')"
            ),
            {"i": uuid.uuid4(), "t": tenant_id, "h": b"ABCD-EFGH-JKLM", "u": user_id},
        )


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
            ["--tenants", "1", "--code-prefix", "tally"],
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


def _synthetic_student(c: Connection, tenant_id: uuid.UUID) -> uuid.UUID:
    student_id = uuid.uuid4()
    c.execute(
        text("INSERT INTO sis.students (id, tenant_id, status) VALUES (:i, :t, 'active')"),
        {"i": student_id, "t": tenant_id},
    )
    return student_id


def test_CLAUDE_6_12_tally_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    before = ScriptDirectory.from_config(populated.cfg).get_revision(REVISION).down_revision
    assert isinstance(before, str)
    with admin.connect() as c:
        tenant_id, user_id = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id FROM core.memberships m WHERE m.status = 'active' "
                "ORDER BY m.created_at LIMIT 1"
            )
        ).one()
    device, sync, party = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with admin.begin() as c:
        code_id = _code(c, tenant_id, user_id)
        c.execute(
            text(
                "INSERT INTO ops.tally_devices (id, tenant_id, name, enrolment_code_id, key_id, "
                "key_ciphertext, enrolled_by) VALUES (:i, :t, 'Office PC', :c, "
                "'tdk-abcdefghijklmnopqrst', '\\x01', :u)"
            ),
            {"i": device, "t": tenant_id, "c": code_id, "u": user_id},
        )
        c.execute(
            text(
                "INSERT INTO ops.tally_groups (id, tenant_id, company, name, selected, "
                "selected_by, selected_at) VALUES (:i, :t, 'Synthetic School', "
                "'Sundry Debtors', true, :u, now())"
            ),
            {"i": uuid.uuid4(), "t": tenant_id, "u": user_id},
        )
        c.execute(
            text(
                "INSERT INTO ops.tally_syncs (id, tenant_id, device_id, batch_id, company, as_of, "
                "groups, parties, created, updated, missing, total_due) VALUES (:i, :t, :d, :b, "
                "'Synthetic School', '2026-09-29', 1, 1, 1, 0, 0, 1500.00)"
            ),
            {"i": sync, "t": tenant_id, "d": device, "b": uuid.uuid4()},
        )
        c.execute(
            text(
                "INSERT INTO ops.tally_parties (id, tenant_id, company, ledger_name, group_name, "
                "closing_balance, as_of, last_sync_id) VALUES (:i, :t, 'Synthetic School', "
                "'Synthetic Party 9A', 'Sundry Debtors', 1500.00, '2026-09-29', :s)"
            ),
            {"i": party, "t": tenant_id, "s": sync},
        )
        c.execute(
            text(
                "INSERT INTO ops.tally_party_links (id, tenant_id, party_id, student_id, "
                "linked_by) VALUES (:i, :t, :p, :s, :u)"
            ),
            {
                "i": uuid.uuid4(),
                "t": tenant_id,
                "p": party,
                "s": _synthetic_student(c, tenant_id),
                "u": user_id,
            },
        )
    students = _scalar(admin, "SELECT count(*) FROM sis.students")
    events = _scalar(admin, "SELECT count(*) FROM audit.events")

    command.downgrade(populated.cfg, before)
    for schema, table in TABLES:
        assert _scalar(admin, f"SELECT to_regclass('{schema}.{table}') IS NULL") is True
    assert _scalar(admin, "SELECT version_num FROM ops.alembic_version") == before
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students
    assert _scalar(admin, "SELECT count(*) FROM audit.events") == events
    # The seeded school's system roles hold the new keys (roles.yaml), so the downgrade keeps
    # them rather than break those grants (only keys no role holds are removed).
    held = _scalar(
        admin,
        "SELECT count(DISTINCT permission_key) FROM core.role_permissions "
        "WHERE permission_key LIKE 'tally.%'",
    )
    assert _scalar(admin, "SELECT count(*) FROM core.permissions WHERE key LIKE 'tally.%'") == held

    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM ops.tally_parties") == 0
    assert _scalar(admin, "SELECT count(*) FROM core.permissions WHERE key LIKE 'tally.%'") == 2
    command.downgrade(populated.cfg, before)
    command.upgrade(populated.cfg, "head")
