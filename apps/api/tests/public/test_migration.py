"""0055_public_handles: model/database agreement and a populated round trip (CLAUDE.md §6.1,
§6.12; docs/05 §15.5; ADR-0047, ADR-0044; FR-PUB-002, FR-LANG-003, FR-LANG-006).

A fresh database is migrated to head, seeded with a synthetic school (``seed-synthetic``), given a
public handle, a guardian language, a Telugu school parent language and a consent-form setting
that follows the school; head -> 0054 -> head must succeed with that data present: the handle
table and resolver go, the form language is filled from the school's parent language, the
student records are untouched, and the upgrade brings everything back.
"""

from __future__ import annotations

import datetime as dt
import hashlib
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
from sqlalchemy import Engine, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.apaar.models import ApaarConsentSettings
from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.model_base import Base
from app.devtools import seed_synthetic as cli
from app.devtools import seeder
from app.public.models import PublicHandle
from app.students.models import Guardian

pytestmark = pytest.mark.db
DB = "schoolos_public_migration"
REVISION = "0055_public_handles"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


@pytest.mark.parametrize(
    "model", [PublicHandle, Guardian, ApaarConsentSettings], ids=lambda m: m.__tablename__
)
def test_FR_PUB_002_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    db_cols = {
        c["name"]: c for c in inspect(admin_engine).get_columns(table.name, schema=table.schema)
    }
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


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
            ["--tenants", "1", "--code-prefix", "pubhdl"],
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


def _seed_r2(admin: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    """A handle, a guardian language, a Telugu school and a following consent form."""
    with admin.begin() as c:
        tenant_id = c.execute(
            text("SELECT id FROM core.tenants WHERE status = 'active' ORDER BY created_at LIMIT 1")
        ).scalar_one()
        guardian_id = uuid.uuid4()
        c.execute(
            text(
                "INSERT INTO sis.guardians (id, tenant_id, full_name, preferred_language) "
                "VALUES (:g, :t, 'Synthetic Guardian', 'te')"
            ),
            {"g": guardian_id, "t": tenant_id},
        )
        c.execute(
            text(
                "INSERT INTO core.public_handles (id, tenant_id, kind, key_hash, target_type, "
                "target_id, guardian_id, expires_at) VALUES (:i, :t, 'parent_notice', :h, "
                "'notice', :ti, :g, :e)"
            ),
            {
                "i": uuid.uuid4(),
                "t": tenant_id,
                "h": hashlib.sha256(uuid.uuid4().bytes).digest(),
                "ti": uuid.uuid4(),
                "g": guardian_id,
                "e": dt.datetime.now(dt.UTC) + dt.timedelta(days=30),
            },
        )
        c.execute(
            text(
                "UPDATE core.tenants SET settings = jsonb_set(settings, '{parent_language}', "
                "'\"te\"') WHERE id = :t"
            ),
            {"t": tenant_id},
        )
        c.execute(
            text(
                "INSERT INTO sis.apaar_consent_settings (id, tenant_id) VALUES (:i, :t) "
                "ON CONFLICT (tenant_id) DO UPDATE SET form_language = NULL"
            ),
            {"i": uuid.uuid4(), "t": tenant_id},
        )
    return tenant_id, guardian_id


def test_CLAUDE_6_12_public_handles_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    before = ScriptDirectory.from_config(populated.cfg).get_revision(REVISION).down_revision
    assert before == "0054_r1_finish"
    tenant_id, guardian_id = _seed_r2(admin)
    students = _scalar(admin, "SELECT count(*) FROM sis.students")
    guardians = _scalar(admin, "SELECT count(*) FROM sis.guardians")
    events = _scalar(admin, "SELECT count(*) FROM audit.events")

    command.downgrade(populated.cfg, before)
    assert _scalar(admin, "SELECT to_regclass('core.public_handles') IS NULL") is True
    assert (
        _scalar(admin, "SELECT to_regprocedure('core.resolve_public_handle(text,bytea)') IS NULL")
        is True
    )
    assert (
        _scalar(
            admin,
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'sis' "
            "AND table_name = 'guardians' AND column_name = 'preferred_language'",
        )
        == 0
    )
    # NULL ("follow the school") becomes the school's parent language, then NOT NULL again.
    assert (
        _scalar(
            admin,
            "SELECT form_language FROM sis.apaar_consent_settings WHERE tenant_id = :t",
            t=tenant_id,
        )
        == "te"
    )
    assert (
        _scalar(
            admin,
            "SELECT is_nullable FROM information_schema.columns WHERE table_schema = 'sis' "
            "AND table_name = 'apaar_consent_settings' AND column_name = 'form_language'",
        )
        == "NO"
    )
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students
    assert _scalar(admin, "SELECT count(*) FROM sis.guardians") == guardians
    assert _scalar(admin, "SELECT count(*) FROM audit.events") == events
    # The schemas belong to bootstrap.sql and stay.
    assert _scalar(admin, "SELECT to_regnamespace('fin') IS NOT NULL") is True

    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM core.public_handles") == 0
    assert (
        _scalar(admin, "SELECT preferred_language FROM sis.guardians WHERE id = :g", g=guardian_id)
        is None
    )
    command.downgrade(populated.cfg, before)
    command.upgrade(populated.cfg, "head")
