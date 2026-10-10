"""0051_apaar_consent_pen: schema facts, database checks and a populated round trip (CLAUDE.md
§6.1, §6.12; docs/05 §5; ADR-0039; FR-APC-001..003, FR-STU-017, PRV-021).

A fresh database is migrated to head, seeded with a synthetic school (``seed-synthetic``), given
consent decisions and a form-language setting; head -> 0050 -> head must succeed with that data
present and leave the student records untouched.
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import Engine, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.apaar import models
from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.model_base import Base
from app.devtools import seed_synthetic as cli
from app.devtools import seeder

pytestmark = pytest.mark.db
DB = "schoolos_apaar_migration"
REVISION = "0051_apaar_consent_pen"
TABLES = {("sis", "apaar_consents"), ("sis", "apaar_consent_settings")}
NEW_PERMISSIONS = ("apaar.consent.read", "apaar.consent.record")
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def _grants(engine: Engine, role: str, table: str) -> set[str]:
    with engine.connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = :r AND table_schema = 'sis' AND table_name = :t"
                ),
                {"r": role, "t": table},
            ).scalars()
        )


@pytest.mark.parametrize(
    "model", [models.ApaarConsent, models.ApaarConsentSettings], ids=lambda m: m.__tablename__
)
def test_ADR_0039_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    db_cols = {
        c["name"]: c for c in inspect(admin_engine).get_columns(table.name, schema=table.schema)
    }
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


def test_SEC_001_tables_force_rls_and_the_register_is_append_only(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'sis' AND c.relname = ANY(:names)"
            ),
            {"names": [t for _, t in TABLES]},
        ).all()
        policies = {
            (r.tablename, r.policyname)
            for r in c.execute(
                text(
                    "SELECT tablename, policyname FROM pg_policies "
                    "WHERE schemaname = 'sis' AND tablename = ANY(:names)"
                ),
                {"names": [t for _, t in TABLES]},
            )
        }
    assert {r.relname for r in rows} == {t for _, t in TABLES}
    assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows)
    for _, table in TABLES:
        assert (table, "tenant_isolation") in policies
        assert (table, "offboarding_purge") in policies  # ADR-0029
        assert {"SELECT", "DELETE"} <= _grants(admin_engine, "sos_purger", table)
        assert not {"UPDATE", "DELETE", "TRUNCATE"} & _grants(admin_engine, "sos_app", table)
    # History kept (FR-APC-002): the app may only read and append decisions.
    assert {"SELECT", "INSERT"} <= _grants(admin_engine, "sos_app", "apaar_consents")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE sis.apaar_consents SET status = 'refused' WHERE false",
        "DELETE FROM sis.apaar_consents WHERE false",
        "UPDATE sis.apaar_consent_settings SET tenant_id = tenant_id WHERE false",
        "DELETE FROM sis.apaar_consent_settings WHERE false",
    ],
)
def test_FR_APC_002_app_role_cannot_rewrite_the_register(
    app_engine: Engine, statement: str
) -> None:
    with app_engine.connect() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(uuid.uuid4())})
        with pytest.raises(ProgrammingError, match="permission denied"):
            c.execute(text(statement))
        c.rollback()


def test_ADR_0039_new_permissions_are_in_the_catalog(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = {
            r.key: r
            for r in c.execute(
                text("SELECT key, step_up, is_platform FROM core.permissions WHERE key = ANY(:k)"),
                {"k": list(NEW_PERMISSIONS)},
            )
        }
    assert set(rows) == set(NEW_PERMISSIONS)
    assert not any(r.step_up or r.is_platform for r in rows.values())


def test_FR_STU_017_pen_definition_is_eleven_digits(admin_engine: Engine) -> None:
    raw = _scalar(
        admin_engine,
        "SELECT validation::text FROM sis.attribute_definitions "
        "WHERE key = 'udise_pen' AND tenant_id IS NULL",
    )
    validation = json.loads(raw)
    assert validation["pattern"] == "^[0-9]{11}$"
    assert validation["compact"] is True
    assert validation["sources"] == ["udise_plus", "tc_incoming", "manual_entry"]


# --- database checks ------------------------------------------------------------------------


@dataclass
class _Ids:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    membership_id: uuid.UUID
    student_id: uuid.UUID


def _school(admin: Engine) -> _Ids:
    with admin.begin() as c:
        tenant_id, user_id, membership_id = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id FROM core.memberships m "
                "WHERE m.status = 'active' ORDER BY m.created_at LIMIT 1"
            )
        ).one()
        student = uuid.uuid4()
        c.execute(
            text("INSERT INTO sis.students (id, tenant_id, status) VALUES (:i, :t, 'active')"),
            {"i": student, "t": tenant_id},
        )
    return _Ids(tenant_id, user_id, membership_id, student)


INSERT = (
    "INSERT INTO sis.apaar_consents (id, tenant_id, student_id, seq, status, relationship, "
    "decided_on, evidence_document_id, recorded_by, recorded_by_membership) VALUES (:i, :t, :s, "
    ":seq, :st, :rel, :d, NULL, :u, :m)"
)


def _params(ids: _Ids, **over: Any) -> dict[str, Any]:
    base = {
        "i": uuid.uuid4(),
        "t": ids.tenant_id,
        "s": ids.student_id,
        "seq": 1,
        "st": "refused",
        "rel": "mother",
        "d": "2026-07-01",
        "u": ids.user_id,
        "m": ids.membership_id,
    }
    return {**base, **over}


def test_PRV_021_consent_needs_the_signed_form_and_a_decision_needs_who_and_when(
    world: Any, admin_engine: Engine
) -> None:
    ids = _school(admin_engine)
    for over, constraint in (
        ({"st": "given"}, "apaar_consents_given_has_form"),
        ({"rel": None}, "apaar_consents_decided"),
        ({"d": None}, "apaar_consents_decided"),
        ({"st": "maybe"}, "apaar_consents_status_check"),
        ({"rel": "uncle"}, "apaar_consents_relationship_check"),
    ):
        with pytest.raises(DBAPIError, match=constraint), admin_engine.begin() as c:
            c.execute(text(INSERT), _params(ids, **over))
    with admin_engine.begin() as c:
        c.execute(text(INSERT), _params(ids, st="pending", rel=None, d=None))


def test_FR_APC_002_one_decision_per_sequence_number(world: Any, admin_engine: Engine) -> None:
    ids = _school(admin_engine)
    with admin_engine.begin() as c:
        c.execute(text(INSERT), _params(ids))
    with pytest.raises(DBAPIError, match="apaar_consents_seq_key"), admin_engine.begin() as c:
        c.execute(text(INSERT), _params(ids))


# --- populated round trip -------------------------------------------------------------------


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
            ["--tenants", "1", "--code-prefix", "apaar"],
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


def test_CLAUDE_6_12_apaar_consent_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    before = ScriptDirectory.from_config(populated.cfg).get_revision(REVISION).down_revision
    assert before == "0050_verified_answer_drafter"
    ids = _school(admin)
    with admin.begin() as c:
        c.execute(text(INSERT), _params(ids))
        c.execute(text(INSERT), _params(ids, seq=2, st="pending", rel=None, d=None))
        c.execute(
            text(
                "INSERT INTO sis.apaar_consent_settings (id, tenant_id, form_language) "
                "VALUES (:i, :t, 'te')"
            ),
            {"i": uuid.uuid4(), "t": ids.tenant_id},
        )
    students = _scalar(admin, "SELECT count(*) FROM sis.students")
    values = _scalar(admin, "SELECT count(*) FROM sis.attribute_values")
    events = _scalar(admin, "SELECT count(*) FROM audit.events")

    command.downgrade(populated.cfg, before)
    for schema, table in TABLES:
        assert _scalar(admin, f"SELECT to_regclass('{schema}.{table}') IS NULL") is True
    assert _scalar(admin, "SELECT version_num FROM ops.alembic_version") == before
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students
    assert _scalar(admin, "SELECT count(*) FROM sis.attribute_values") == values
    assert _scalar(admin, "SELECT count(*) FROM audit.events") == events
    pen = json.loads(
        _scalar(
            admin,
            "SELECT validation::text FROM sis.attribute_definitions "
            "WHERE key = 'udise_pen' AND tenant_id IS NULL",
        )
    )
    assert pen["pattern"] == "^[A-Za-z0-9]{1,20}$"
    # Catalog keys a school's (synced) role still holds stay; the rest are removed.

    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM sis.apaar_consents") == 0
    assert (
        _scalar(admin, "SELECT count(*) FROM core.permissions WHERE key LIKE 'apaar.consent.%'")
        == 2
    )
    command.downgrade(populated.cfg, before)
    command.upgrade(populated.cfg, "head")
