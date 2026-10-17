"""0057_messaging_core: model/database agreement, grants, triggers and a populated round trip
(CLAUDE.md §6.1, §6.7, §6.12; docs/05 §15.3; ADR-0043; FR-MSG-008, FR-MSG-016, FR-MSG-023).

A fresh database is migrated to head, seeded with a synthetic school (``seed-synthetic``) and
given messaging rows (account, settings, an opt-in, a message, an inbox event); head -> 0056 ->
head must succeed with that data present: the msg tables and catalog keys go, the student records
are untouched, and the upgrade brings the empty tables back.
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
from sqlalchemy import Engine, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.model_base import Base
from app.devtools import seed_synthetic as cli
from app.devtools import seeder
from app.messaging import models

pytestmark = pytest.mark.db
DB = "schoolos_messaging_migration"
REVISION = "0057_messaging_core"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)
MODELS = [
    models.Account,
    models.MessageTemplate,
    models.MessagingSettings,
    models.Optin,
    models.JoinCode,
    models.Message,
    models.InboundEvent,
    models.ProviderEvent,
]
TABLES = [f"msg.{m.__tablename__}" for m in MODELS]


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__tablename__)
def test_CLAUDE_6_1_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    db_cols = {
        c["name"]: c for c in inspect(admin_engine).get_columns(table.name, schema=table.schema)
    }
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


def _privileges(engine: Engine, role: str, table: str) -> set[str]:
    with engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = :r AND table_schema || '.' || table_name = :t"
            ),
            {"r": role, "t": table},
        )
        return {r[0] for r in rows}


def test_FR_MSG_008_optins_are_append_only_for_the_app(admin_engine: Engine) -> None:
    assert _privileges(admin_engine, "sos_app", "msg.optins") == {"SELECT", "INSERT"}
    for table in TABLES:
        assert "TRUNCATE" not in _privileges(admin_engine, "sos_app", table)
        assert _privileges(admin_engine, "sos_purger", table) == {"SELECT", "DELETE"}
        assert _privileges(admin_engine, "sos_platform", table) == set()


def test_docs_05_15_secrets_and_payloads_never_reach_readonly(admin_engine: Engine) -> None:
    for table in ("msg.accounts", "msg.provider_events", "msg.join_codes"):
        assert _privileges(admin_engine, "sos_readonly", table) == set()


def test_FR_MSG_008_app_role_cannot_rewrite_an_optin(app_engine: Engine) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), app_engine.begin() as c:
        c.execute(text("UPDATE msg.optins SET status = 'opted_in'"))


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
            ["--tenants", "1", "--code-prefix", "msgmig"],
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


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def _seed(admin: Engine) -> uuid.UUID:
    ids = {k: uuid.uuid4() for k in ("account", "settings", "guardian", "optin", "msg", "event")}
    with admin.begin() as c:
        tenant: uuid.UUID = c.execute(
            text("SELECT id FROM core.tenants WHERE status = 'active' ORDER BY created_at LIMIT 1")
        ).scalar_one()
        p = {**ids, "t": tenant}
        c.execute(
            text(
                "INSERT INTO sis.guardians (id, tenant_id, full_name) "
                "VALUES (:guardian, :t, 'Synthetic Guardian')"
            ),
            p,
        )
        c.execute(
            text(
                "INSERT INTO msg.accounts (id, tenant_id, mode, waba_id, phone_number_id, status, "
                "token_ciphertext) VALUES (:account, :t, 'manual_token', '123', '456', 'active', "
                "'\\x00')"
            ),
            p,
        )
        c.execute(text("INSERT INTO msg.settings (id, tenant_id) VALUES (:settings, :t)"), p)
        c.execute(
            text(
                "INSERT INTO msg.optins (id, tenant_id, guardian_id, category, seq, status, "
                "method, wording_version, decided_on) VALUES (:optin, :t, :guardian, 'notices', 1, "
                "'opted_out', 'whatsapp_stop', 'optin-2026-10', current_date)"
            ),
            p,
        )
        c.execute(
            text(
                "INSERT INTO msg.messages (id, tenant_id, purpose, kind, language, guardian_id, "
                "source_type, source_id, dedupe_key, status) VALUES (:msg, :t, 'optin_stopped', "
                "'text', 'en', :guardian, 'inbound', :event, 'stop', 'sent')"
            ),
            p,
        )
        c.execute(
            text(
                "INSERT INTO msg.provider_events (id, tenant_id, account_id, event_key, "
                "event_type) VALUES (:event, :t, :account, 'in:wamid.X', 'inbound_message')"
            ),
            p,
        )
    return ids["msg"]


def test_FR_MSG_016_status_trigger_and_account_secret_checks(populated: _Walk) -> None:
    admin = populated.admin
    message = _seed(admin)
    with pytest.raises(DBAPIError, match="only move forward"), admin.begin() as c:
        c.execute(text("UPDATE msg.messages SET status = 'queued' WHERE id = :i"), {"i": message})
    with admin.begin() as c:
        c.execute(
            text("UPDATE msg.messages SET status = 'delivered' WHERE id = :i"), {"i": message}
        )
    with pytest.raises(DBAPIError, match="cannot fail"), admin.begin() as c:
        c.execute(text("UPDATE msg.messages SET status = 'failed' WHERE id = :i"), {"i": message})
    with pytest.raises(DBAPIError, match="accounts_secrets"), admin.begin() as c:
        c.execute(
            text(
                "UPDATE msg.accounts SET status = 'disconnected', disconnected_at = now() "
                "WHERE token_ciphertext IS NOT NULL"
            )
        )


def test_CLAUDE_6_12_messaging_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    before = ScriptDirectory.from_config(populated.cfg).get_revision(REVISION).down_revision
    assert before == "0056_certificate_verifications"
    _seed(admin)
    students = _scalar(admin, "SELECT count(*) FROM sis.students")
    guardians = _scalar(admin, "SELECT count(*) FROM sis.guardians")
    command.downgrade(populated.cfg, before)
    for table in TABLES:
        assert _scalar(admin, "SELECT to_regclass(:t) IS NULL", t=table) is True
    # Catalog keys still granted to the seeded school's roles stay (like 0051); the rest go.
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students
    assert _scalar(admin, "SELECT count(*) FROM sis.guardians") == guardians
    assert _scalar(admin, "SELECT to_regnamespace('msg') IS NOT NULL") is True
    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM msg.optins") == 0
    assert _scalar(admin, "SELECT count(*) FROM core.permissions WHERE key = 'message.read'") == 1
