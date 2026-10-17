"""0056_certificate_verifications: model/database agreement, database checks and a populated
round trip (CLAUDE.md §6.1, §6.12; docs/05 §15.4; ADR-0045; FR-CERT-015, FR-CERT-019,
FR-CERT-023).

A fresh database is migrated to head and seeded with a synthetic school (``seed-synthetic``), then
given a transfer certificate prepared in the AP format, a verification code and its public
handle; head -> 0055 -> head must succeed with that data present: the code table goes, the
handle is revoked (nothing resolves to a missing row), the certificate stays without its format
columns, and the upgrade brings the structure back.
"""

from __future__ import annotations

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
from sqlalchemy.exc import DBAPIError

from app.certificates.models import Certificate, CertificateVerification
from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.model_base import Base
from app.devtools import seed_synthetic as cli
from app.devtools import seeder

pytestmark = pytest.mark.db
DB = "schoolos_certificate_verification_migration"
REVISION = "0056_certificate_verifications"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)
CODE = "7K4MQ2XP9HD3RT6W"


@pytest.mark.parametrize(
    "model", [Certificate, CertificateVerification], ids=lambda m: m.__tablename__
)
def test_FR_CERT_019_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    db_cols = {
        c["name"]: c for c in inspect(admin_engine).get_columns(table.name, schema=table.schema)
    }
    assert set(db_cols) == {c.name for c in table.columns}


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def test_SEC_037_app_role_updates_only_withdrawal_and_counters(admin_engine: Engine) -> None:
    allowed = {
        name
        for name in (
            c["name"]
            for c in inspect(admin_engine).get_columns("certificate_verifications", schema="sis")
        )
        if _scalar(
            admin_engine,
            "SELECT has_column_privilege('sos_app', 'sis.certificate_verifications', :c, 'UPDATE')",
            c=name,
        )
    }
    assert allowed == {
        "withdrawn_at",
        "withdrawn_by",
        "withdraw_reason",
        "check_count",
        "last_checked_at",
        "updated_at",
        "version",
    }
    assert (
        _scalar(
            admin_engine,
            "SELECT has_table_privilege('sos_app', 'sis.certificate_verifications', 'DELETE')",
        )
        is False
    )
    # sos_readonly never reads the printed code (it opens the content publicly).
    assert (
        _scalar(
            admin_engine,
            "SELECT has_column_privilege('sos_readonly', 'sis.certificate_verifications', "
            "'code', 'SELECT')",
        )
        is False
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
            ["--tenants", "1", "--code-prefix", "certvf"],
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


def _seed(admin: Engine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A pending AP-format TC, a code and its handle (test setup only)."""
    with admin.begin() as c:
        tenant_id: uuid.UUID = c.execute(
            text("SELECT id FROM core.tenants WHERE status = 'active' ORDER BY created_at LIMIT 1")
        ).scalar_one()
        student_id = uuid.uuid4()
        c.execute(
            text(
                "INSERT INTO sis.students (id, tenant_id, admission_no, status) "
                "VALUES (:i, :t, 'CV/0001', 'active')"
            ),
            {"i": student_id, "t": tenant_id},
        )
        membership_id: uuid.UUID = c.execute(
            text("SELECT id FROM core.memberships WHERE tenant_id = :t LIMIT 1"),
            {"t": tenant_id},
        ).scalar_one()
        certificate_id, handle_id = uuid.uuid4(), uuid.uuid4()
        c.execute(
            text(
                "INSERT INTO sis.certificates (id, tenant_id, student_id, certificate_type, "
                "requested_by, format_key, format_version) VALUES (:i, :t, :s, 'transfer', :m, "
                "'ap-tc-2027', 1)"
            ),
            {"i": certificate_id, "t": tenant_id, "s": student_id, "m": membership_id},
        )
        c.execute(
            text(
                "INSERT INTO core.public_handles (id, tenant_id, kind, key_hash, target_type, "
                "target_id) VALUES (:i, :t, 'cert_verify', :h, 'certificate', :c)"
            ),
            {
                "i": handle_id,
                "t": tenant_id,
                "h": hashlib.sha256(CODE.encode()).digest(),
                "c": certificate_id,
            },
        )
        c.execute(
            text(
                "INSERT INTO sis.certificate_verifications (id, tenant_id, certificate_id, code, "
                "handle_id, created_reason, created_by) VALUES (:i, :t, :c, :code, :h, 'issued', "
                ":m)"
            ),
            {
                "i": uuid.uuid4(),
                "t": tenant_id,
                "c": certificate_id,
                "code": CODE,
                "h": handle_id,
                "m": membership_id,
            },
        )
    return tenant_id, certificate_id, handle_id


def test_FR_CERT_019_database_refuses_bad_rows_and_rewrites(populated: _Walk) -> None:
    admin = populated.admin
    _tenant_id, certificate_id, _handle_id = _seed(admin)
    for sql in (
        "UPDATE sis.certificate_verifications SET code = 'ABCDEFGHJKMNPQRS'",
        "UPDATE sis.certificate_verifications SET check_count = -1",
        "DELETE FROM sis.certificate_verifications",
        "UPDATE sis.certificates SET format_key = 'generic-v1' WHERE id = :c",
    ):
        with pytest.raises(DBAPIError), admin.begin() as c:
            c.execute(text(sql), {"c": certificate_id})
    with pytest.raises(DBAPIError), admin.begin() as c:  # U is never in a code
        c.execute(text("UPDATE sis.certificate_verifications SET code = '7K4MQ2XP9HD3RT6U'"))
    with admin.begin() as c:
        c.execute(
            text(
                "UPDATE sis.certificate_verifications SET withdrawn_at = now(), withdrawn_by = "
                "created_by, withdraw_reason = 'Synthetic withdrawal reason'"
            )
        )
    with pytest.raises(DBAPIError), admin.begin() as c:  # a withdrawal is final
        c.execute(
            text(
                "UPDATE sis.certificate_verifications SET withdrawn_at = NULL, withdrawn_by = "
                "NULL, withdraw_reason = NULL"
            )
        )


def test_CLAUDE_6_12_certificate_verification_migration_reversible_with_data(
    populated: _Walk,
) -> None:
    admin = populated.admin
    before = ScriptDirectory.from_config(populated.cfg).get_revision(REVISION).down_revision
    assert before == "0055_public_handles"
    _tenant_id, certificate_id, handle_id = _seed(admin)
    students = _scalar(admin, "SELECT count(*) FROM sis.students")
    certificates = _scalar(admin, "SELECT count(*) FROM sis.certificates")

    command.downgrade(populated.cfg, before)
    assert _scalar(admin, "SELECT to_regclass('sis.certificate_verifications') IS NULL") is True
    assert (
        _scalar(
            admin,
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'sis' "
            "AND table_name = 'certificates' AND column_name IN ('format_key', 'format_version')",
        )
        == 0
    )
    assert (
        _scalar(admin, "SELECT revoked_reason FROM core.public_handles WHERE id = :h", h=handle_id)
        == "downgrade"
    )
    assert _scalar(admin, "SELECT count(*) FROM sis.certificates") == certificates
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students
    # The 0033 trigger body is back: the request is still frozen.
    with pytest.raises(DBAPIError), admin.begin() as c:
        c.execute(
            text("UPDATE sis.certificates SET inputs = '{\"x\": 1}' WHERE id = :c"),
            {"c": certificate_id},
        )

    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM sis.certificate_verifications") == 0
    assert (
        _scalar(admin, "SELECT format_key FROM sis.certificates WHERE id = :c", c=certificate_id)
        is None
    )
    command.downgrade(populated.cfg, before)
    command.upgrade(populated.cfg, "head")
