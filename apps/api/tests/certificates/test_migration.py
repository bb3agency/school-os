"""0033_certificates: schema facts, database checks and a populated round trip (CLAUDE.md §6.1,
§6.12, §8; docs/05 §5.7; FR-CERT-004, FR-CERT-006, FR-REG-005).

A fresh database is migrated to head, seeded with a synthetic school (``seed-synthetic``), and
given certificates (issued, pending) and a certificate document; the walk head -> 0032 -> head
must succeed with that data present and leave every other table untouched.
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
from pydantic import SecretStr
from sqlalchemy import Engine, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.certificates import models
from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.model_base import Base
from app.devtools import seed_synthetic as cli
from app.devtools import seeder

pytestmark = pytest.mark.db
DB = "schoolos_certificates_migration"
BEFORE = "0032_offboarding"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)
TABLES = ("certificates", "certificate_counters")


@pytest.mark.parametrize(
    "model", [models.Certificate, models.CertificateCounter], ids=lambda m: m.__tablename__
)
def test_FR_CERT_006_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    insp = inspect(admin_engine)
    db_cols = {c["name"]: c for c in insp.get_columns(table.name, schema=table.schema)}
    assert set(db_cols) == {c.name for c in table.columns}


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def test_SEC_001_certificate_tables_force_rls_and_are_append_only(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'sis' "
                "AND c.relname = ANY(:t)"
            ),
            {"t": list(TABLES)},
        ).all()
        policies = c.execute(
            text(
                "SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'sis' "
                "AND tablename = ANY(:t)"
            ),
            {"t": list(TABLES)},
        ).all()
        updatable = {
            (r[0], r[1])
            for r in c.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.column_privileges "
                    "WHERE grantee = 'sos_app' AND table_schema = 'sis' "
                    "AND table_name = ANY(:t) AND privilege_type = 'UPDATE'"
                ),
                {"t": list(TABLES)},
            )
        }
    assert {r.relname for r in rows} == set(TABLES)
    assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows)
    assert {(p.tablename, p.policyname) for p in policies} == {
        (t, name) for t in TABLES for name in ("tenant_isolation", "offboarding_purge")
    }
    for table in TABLES:
        grants = _scalar(
            admin_engine,
            "SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) "
            "FROM information_schema.role_table_grants WHERE grantee = 'sos_app' "
            "AND table_schema = 'sis' AND table_name = :t",
            t=table,
        )
        assert "DELETE" not in grants
        assert "TRUNCATE" not in grants
        assert "UPDATE" not in grants  # column grants only
        assert "INSERT" in grants
        assert "SELECT" in grants
    cert_cols = {col for t, col in updatable if t == "certificates"}
    # What was requested, for whom and by whom never changes (FR-REG-005).
    assert not cert_cols & {
        "id",
        "tenant_id",
        "student_id",
        "certificate_type",
        "inputs",
        "requested_by",
        "requested_at",
        "original_certificate_id",
        "duplicate_reason",
        "created_at",
    }
    assert {"status", "serial", "content", "cancel_reason", "document_id"} <= cert_cols
    assert {col for t, col in updatable if t == "certificate_counters"} == {
        "last_no",
        "updated_at",
    }


def test_FR_CERT_010_documents_accept_the_certificate_purpose(admin_engine: Engine) -> None:
    check = _scalar(
        admin_engine,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'documents_purpose_check'",
    )
    assert "'certificate'" in check
    upload = _scalar(
        admin_engine,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'upload_intents_purpose_check'",
    )
    assert "'certificate'" not in upload, "certificates are generated, never uploaded"


def test_FR_CERT_004_permissions_are_in_the_catalog(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = {
            r[0]: (r[1], r[2])
            for r in c.execute(
                text(
                    "SELECT key, step_up, is_platform FROM core.permissions "
                    "WHERE key IN ('certificate.read', 'certificate.issue', "
                    "'certificate.approve', 'register.read')"
                )
            )
        }
    assert rows == {
        "certificate.read": (False, False),
        "certificate.issue": (False, False),
        "certificate.approve": (True, False),
        "register.read": (True, False),
    }


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
            ["--tenants", "1", "--code-prefix", "certm"],
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


def _seed_certificates(admin: Engine) -> None:
    """An issued certificate with its PDF document and a pending TC (synthetic rows)."""
    with admin.begin() as c:
        tenant_id, user_id, membership_id = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id FROM core.memberships m "
                "WHERE m.status = 'active' ORDER BY m.created_at LIMIT 1"
            )
        ).one()
        year = c.execute(
            text("SELECT id FROM core.academic_years WHERE tenant_id = :t LIMIT 1"),
            {"t": tenant_id},
        ).scalar_one()
        student_ids = [uuid.uuid4(), uuid.uuid4()]
        for n, sid in enumerate(student_ids):
            c.execute(
                text(
                    "INSERT INTO sis.students (id, tenant_id, admission_no, status) "
                    "VALUES (:s, :t, :a, 'active')"
                ),
                {"s": sid, "t": tenant_id, "a": f"CM-{n}"},
            )
        doc, version = uuid.uuid4(), uuid.uuid4()
        c.execute(
            text(
                "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, sensitivity, "
                "created_by, current_version_id) VALUES (:d, :t, 'certificate', 'certificate', "
                "'Bonafide certificate BC/2026-27/0001', 'C2', :u, :v)"
            ),
            {"d": doc, "t": tenant_id, "u": user_id, "v": version},
        )
        c.execute(
            text(
                "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
                "(:v, :t, :d, 1, :k, :sha, 'application/pdf', 10, 'ready', :u)"
            ),
            {
                "v": version,
                "t": tenant_id,
                "d": doc,
                "k": f"t/{tenant_id}/docs/{doc}/v1/original.pdf",
                "sha": hashlib.sha256(b"certificate-pdf").digest(),
                "u": user_id,
            },
        )
        c.execute(
            text(
                "INSERT INTO sis.certificate_counters (id, tenant_id, certificate_type, "
                "academic_year_id, last_no) VALUES (:i, :t, 'bonafide', :y, 1)"
            ),
            {"i": uuid.uuid4(), "t": tenant_id, "y": year},
        )
        c.execute(
            text(
                "INSERT INTO sis.certificates (id, tenant_id, student_id, certificate_type, "
                "status, inputs, academic_year_id, serial_no, serial, content, content_sha256, "
                "template_version, requested_by, issued_by, issued_at, document_id, pdf_status) "
                "VALUES (:i, :t, :s, 'bonafide', 'issued', CAST(:inputs AS jsonb), :y, 1, "
                "'BC/2026-27/0001', CAST(:content AS jsonb), :sha, 'v1', :m, :m, now(), :d, "
                "'ready')"
            ),
            {
                "i": uuid.uuid4(),
                "t": tenant_id,
                "s": student_ids[0],
                "inputs": '{"purpose": "passport"}',
                "y": year,
                "content": '{"serial": "BC/2026-27/0001"}',
                "sha": hashlib.sha256(b"content").digest(),
                "m": membership_id,
                "d": doc,
            },
        )
        c.execute(
            text(
                "INSERT INTO sis.certificates (id, tenant_id, student_id, certificate_type, "
                "status, inputs, requested_by) VALUES (:i, :t, :s, 'transfer', 'pending', "
                "CAST(:inputs AS jsonb), :m)"
            ),
            {
                "i": uuid.uuid4(),
                "t": tenant_id,
                "s": student_ids[-1],
                "inputs": '{"leaving_date": "2026-09-01"}',
                "m": membership_id,
            },
        )


def test_CLAUDE_6_12_certificates_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    _seed_certificates(admin)
    students = _scalar(admin, "SELECT count(*) FROM sis.students")
    events = _scalar(admin, "SELECT count(*) FROM audit.events")
    documents = _scalar(admin, "SELECT count(*) FROM kb.documents")
    assert _scalar(admin, "SELECT count(*) FROM sis.certificates") == 2

    command.downgrade(populated.cfg, BEFORE)
    assert _scalar(admin, "SELECT to_regclass('sis.certificates') IS NULL") is True
    assert _scalar(admin, "SELECT to_regclass('sis.certificate_counters') IS NULL") is True
    assert _scalar(admin, "SELECT version_num FROM ops.alembic_version") == BEFORE
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students
    assert _scalar(admin, "SELECT count(*) FROM audit.events") == events
    assert _scalar(admin, "SELECT count(*) FROM kb.documents") == documents, "PDFs are kept"

    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM sis.certificates") == 0
    command.downgrade(populated.cfg, BEFORE)
    command.upgrade(populated.cfg, "head")
    assert (
        _scalar(
            admin,
            "SELECT count(*) FROM pg_indexes WHERE schemaname = 'sis' "
            "AND indexname = 'certificates_one_live_tc'",
        )
        == 1
    )


def test_FR_CERT_006_serial_unique_per_type_and_year(school: Any, admin_engine: Engine) -> None:
    """Belt and braces for the counter: the database refuses a reused number."""
    sys_mod = __import__("sys").modules["sos_test_certificates_support"]
    cert = sys_mod.issue(school, sys_mod.student(school), "bonafide")
    row = sys_mod.row(admin_engine, cert.id)
    other = sys_mod.student(school)
    with pytest.raises(DBAPIError, match="certificates_serial"), admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO sis.certificates (id, tenant_id, student_id, certificate_type, "
                "status, academic_year_id, serial_no, serial, content, content_sha256, "
                "template_version, requested_by, issued_by, issued_at) VALUES (:i, :t, :s, "
                "'bonafide', 'issued', :y, :n, :serial, '{}', :sha, 'v1', :m, :m, now())"
            ),
            {
                "i": uuid.uuid4(),
                "t": school.tenant_id,
                "s": other,
                "y": row["academic_year_id"],
                "n": row["serial_no"],
                "serial": row["serial"] + "X",
                "sha": hashlib.sha256(b"x").digest(),
                "m": school.people["office_admin"].membership_id,
            },
        )
