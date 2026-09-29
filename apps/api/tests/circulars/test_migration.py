"""0034_circulars: schema facts, database checks and a populated round trip (CLAUDE.md §6.1,
§6.12, §8; docs/05 §6.3; FR-CIR-*, FR-TASK-*, FR-NOTICE-*).

A fresh database is migrated to head, seeded with a synthetic school (``seed-synthetic``), and
given a reading, a suggestion, a task and a notice; the walk head -> previous revision -> head
must succeed with that data present and leave every other table untouched.
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
from sqlalchemy.exc import DBAPIError

from app.circulars import models
from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.model_base import Base
from app.devtools import seed_synthetic as cli
from app.devtools import seeder

pytestmark = pytest.mark.db
DB = "schoolos_circulars_migration"
REVISION = "0034_circulars"
TABLES = {
    ("kb", "circular_readings"),
    ("kb", "circular_suggestions"),
    ("ops", "tasks"),
    ("ops", "parent_notices"),
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
    [models.CircularReading, models.CircularSuggestion, models.Task, models.ParentNotice],
    ids=lambda m: m.__tablename__,
)
def test_M4_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    insp = inspect(admin_engine)
    db_cols = {c["name"]: c for c in insp.get_columns(table.name, schema=table.schema)}
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


def test_SEC_001_tables_force_rls_and_the_app_cannot_delete_or_rewrite(
    admin_engine: Engine,
) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE (n.nspname, c.relname) IN (('kb','circular_readings'), "
                "('kb','circular_suggestions'), ('ops','tasks'), ('ops','parent_notices'))"
            )
        ).all()
        policies = {
            (r.schemaname, r.tablename, r.policyname)
            for r in c.execute(
                text(
                    "SELECT schemaname, tablename, policyname FROM pg_policies "
                    "WHERE tablename IN ('circular_readings','circular_suggestions','tasks',"
                    "'parent_notices')"
                )
            )
        }
    assert {(r.nspname, r.relname) for r in rows} == TABLES
    assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows)
    for schema, table in TABLES:
        assert (schema, table, "tenant_isolation") in policies
        assert (schema, table, "offboarding_purge") in policies  # ADR-0029
        grants = _scalar(
            admin_engine,
            "SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) "
            "FROM information_schema.role_table_grants WHERE grantee = 'sos_app' "
            "AND table_schema = :s AND table_name = :t",
            s=schema,
            t=table,
        )
        assert "TRUNCATE" not in grants
        assert "UPDATE" not in grants  # column grants only
        if schema == "ops":
            assert "DELETE" not in grants  # tasks are cancelled, notices kept
    frozen = {
        r[0]
        for r in admin_engine.connect().execute(
            text(
                "SELECT column_name FROM information_schema.column_privileges "
                "WHERE grantee = 'sos_app' AND table_schema = 'kb' "
                "AND table_name = 'circular_suggestions' AND privilege_type = 'UPDATE'"
            )
        )
    }
    # What the AI suggested and where it came from never changes; only the decision.
    assert not frozen & {"title", "due_on", "citation", "reading_id"}
    assert {"status", "task_id", "decided_by", "decided_at"} <= frozen


def _member(admin: Engine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    with admin.connect() as c:
        row = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id FROM core.memberships m "
                "WHERE m.status = 'active' ORDER BY m.created_at LIMIT 1"
            )
        ).one()
    return row[0], row[1], row[2]


def _document(
    admin: Engine, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    doc, ver = uuid.uuid4(), uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, sensitivity, "
                "created_by) VALUES (:d, :t, 'circular', 'circular', 'Synthetic circular', "
                "'C1', :u)"
            ),
            {"d": doc, "t": tenant_id, "u": user_id},
        )
        c.execute(
            text(
                "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES (:v, :t, "
                ":d, 1, :k, :h, 'application/pdf', 10, 'ready', :u)"
            ),
            {
                "v": ver,
                "t": tenant_id,
                "d": doc,
                "k": f"t/{tenant_id}/docs/{doc}/v1/original.pdf",
                "h": b"\x01" * 32,
                "u": user_id,
            },
        )
    return doc, ver


def test_FR_CIR_003_citations_must_name_a_document_page(world: Any, admin_engine: Engine) -> None:
    tenant_id, owner = world.a.tenant_id, world.a.people["owner"]
    user_id, membership_id = owner.user_id, owner.membership_id
    doc, _ver = _document(admin_engine, tenant_id, user_id)
    bad = '{"source": "https://example.org/x", "quote": "x"}'
    with pytest.raises(DBAPIError, match="tasks_citation_object"), admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.tasks (id, tenant_id, title, owner_membership_id, due_on, "
                "source, document_id, citation, created_by, created_by_membership) VALUES "
                "(:i, :t, 'x', :m, '2026-10-15', 'circular', :d, CAST(:c AS jsonb), :u, :m)"
            ),
            {
                "i": uuid.uuid4(),
                "t": tenant_id,
                "m": membership_id,
                "d": doc,
                "c": bad,
                "u": user_id,
            },
        )
    with pytest.raises(DBAPIError, match="tasks_circular_has_citation"), admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.tasks (id, tenant_id, title, owner_membership_id, due_on, "
                "source, created_by, created_by_membership) VALUES "
                "(:i, :t, 'x', :m, '2026-10-15', 'circular', :u, :m)"
            ),
            {"i": uuid.uuid4(), "t": tenant_id, "m": membership_id, "u": user_id},
        )


def test_FR_NOTICE_004_an_approved_notice_has_both_languages(
    world: Any, admin_engine: Engine
) -> None:
    tenant_id, user_id = world.a.tenant_id, world.a.people["owner"].user_id
    complete = "parent_notices_approved_complete"
    with pytest.raises(DBAPIError, match=complete), admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.parent_notices (id, tenant_id, source, status, title_en, "
                "body_en, created_by, approved_by, approved_at) VALUES (:i, :t, 'blank', "
                "'approved', 'Title', 'Body', :u, :u, now())"
            ),
            {"i": uuid.uuid4(), "t": tenant_id, "u": user_id},
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
            ["--tenants", "1", "--code-prefix", "circ"],
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


def test_CLAUDE_6_12_circulars_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    before = ScriptDirectory.from_config(populated.cfg).get_revision(REVISION).down_revision
    assert isinstance(before, str)
    tenant_id, user_id, membership_id = _member(admin)
    doc, ver = _document(admin, tenant_id, user_id)
    reading, task = uuid.uuid4(), uuid.uuid4()
    citation = '{"source": "sos://doc/' + str(doc) + '/v1#p1", "quote": "synthetic"}'
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.circular_readings (id, tenant_id, document_id, version_id, "
                "version_no, status, completed_at) VALUES (:i, :t, :d, :v, 1, 'ready', now())"
            ),
            {"i": reading, "t": tenant_id, "d": doc, "v": ver},
        )
        c.execute(
            text(
                "INSERT INTO ops.tasks (id, tenant_id, title, owner_membership_id, due_on, source, "
                "document_id, citation, created_by, created_by_membership) VALUES (:i, :t, "
                "'Synthetic', :m, '2026-10-15', 'circular', :d, CAST(:c AS jsonb), :u, :m)"
            ),
            {"i": task, "t": tenant_id, "m": membership_id, "d": doc, "c": citation, "u": user_id},
        )
        c.execute(
            text(
                "INSERT INTO kb.circular_suggestions (id, tenant_id, reading_id, position, title, "
                "due_on, citation) VALUES (:i, :t, :r, 1, 'Synthetic', '2026-10-15', "
                "CAST(:c AS jsonb))"
            ),
            {"i": uuid.uuid4(), "t": tenant_id, "r": reading, "c": citation},
        )
        c.execute(
            text(
                "INSERT INTO ops.parent_notices (id, tenant_id, source, created_by) "
                "VALUES (:i, :t, 'blank', :u)"
            ),
            {"i": uuid.uuid4(), "t": tenant_id, "u": user_id},
        )
        c.execute(
            text(
                "INSERT INTO kb.llm_calls (id, tenant_id, feature, role, provider, model, outcome, "
                "attempts, latency_ms, input_tokens, output_tokens, cost_usd) VALUES (:i, :t, "
                "'circulars', 'circular', 'fake', 'claude-haiku-4-5-20251001', 'ok', 1, 1, 1, 1, 0)"
            ),
            {"i": uuid.uuid4(), "t": tenant_id},
        )
    students = _scalar(admin, "SELECT count(*) FROM sis.students")
    documents = _scalar(admin, "SELECT count(*) FROM kb.documents")
    events = _scalar(admin, "SELECT count(*) FROM audit.events")

    command.downgrade(populated.cfg, before)
    for schema, table in TABLES:
        assert _scalar(admin, f"SELECT to_regclass('{schema}.{table}') IS NULL") is True
    assert _scalar(admin, "SELECT version_num FROM ops.alembic_version") == before
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students
    assert _scalar(admin, "SELECT count(*) FROM kb.documents") == documents
    assert _scalar(admin, "SELECT count(*) FROM audit.events") == events
    # Metered calls of the new features are kept (the checks come back NOT VALID).
    assert _scalar(admin, "SELECT count(*) FROM kb.llm_calls WHERE feature = 'circulars'") == 1

    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM ops.tasks") == 0
    assert _scalar(admin, "SELECT count(*) FROM core.permissions WHERE key = 'task.manage'") == 1
    command.downgrade(populated.cfg, before)
    command.upgrade(populated.cfg, "head")


# --- 0037_notice_drafting (FR-NOTICE-003) ---------------------------------------------------------

DRAFTING = "0037_notice_drafting"


def _notice_row(c: Any, tenant_id: uuid.UUID, user_id: uuid.UUID, **values: Any) -> uuid.UUID:
    notice_id = uuid.uuid4()
    columns = {"id": notice_id, "tenant_id": tenant_id, "created_by": user_id, **values}
    names = ", ".join(columns)
    params = ", ".join(f":{k}" for k in columns)
    c.execute(text(f"INSERT INTO ops.parent_notices ({names}) VALUES ({params})"), columns)
    return notice_id


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"source": "blank", "status": "draft_failed"}, "parent_notices_failed_has_error"),
        (
            {"source": "staff_text", "status": "draft", "source_text": "Sports day"},
            "parent_notices_source_text_while_drafting",
        ),
        (
            {"source": "circular", "status": "drafting", "source_text": "Sports day"},
            "parent_notices_source_text_while_drafting",
        ),
        ({"source": "blank", "status": "writing"}, "parent_notices_status_check"),
        (
            {"source": "staff_text", "status": "drafting", "source_text": "x" * 4001},
            "parent_notices_source_text_length",
        ),
    ],
    ids=["failed_without_code", "text_on_draft", "text_on_circular", "state", "long"],
)
def test_FR_NOTICE_003_drafting_states_are_checked_by_the_database(
    world: Any, admin_engine: Engine, values: dict[str, Any], constraint: str
) -> None:
    tenant_id, user_id = world.a.tenant_id, world.a.people["owner"].user_id
    with pytest.raises(DBAPIError, match=constraint), admin_engine.begin() as c:
        _notice_row(c, tenant_id, user_id, **values)


def test_FR_NOTICE_003_the_app_may_record_the_draft_outcome(admin_engine: Engine) -> None:
    columns = {
        r[0]
        for r in admin_engine.connect().execute(
            text(
                "SELECT column_name FROM information_schema.column_privileges "
                "WHERE grantee = 'sos_app' AND table_schema = 'ops' "
                "AND table_name = 'parent_notices' AND privilege_type = 'UPDATE'"
            )
        )
    }
    assert {"ai_drafted", "draft_error", "source_text", "status"} <= columns
    assert not columns & {"source", "document_id", "created_by", "tenant_id"}


def test_CLAUDE_6_12_notice_drafting_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    before = ScriptDirectory.from_config(populated.cfg).get_revision(DRAFTING).down_revision
    assert before == "0034_circulars"
    tenant_id, user_id, _membership_id = _member(admin)
    with admin.begin() as c:
        drafting = _notice_row(
            c, tenant_id, user_id, source="staff_text", status="drafting", source_text="Sports"
        )
        failed = _notice_row(
            c,
            tenant_id,
            user_id,
            source="circular",
            status="draft_failed",
            draft_error="ai_unavailable",
        )
        _notice_row(c, tenant_id, user_id, source="blank", status="draft")
    events = _scalar(admin, "SELECT count(*) FROM audit.events")

    command.downgrade(populated.cfg, before)
    assert _scalar(admin, "SELECT version_num FROM ops.alembic_version") == before
    assert (
        _scalar(
            admin,
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'ops' "
            "AND table_name = 'parent_notices' AND column_name = 'source_text'",
        )
        == 0
    )
    # Rows already drafting or failed are kept (NOT VALID checks); new rows obey 0034 again.
    assert _scalar(admin, "SELECT count(*) FROM ops.parent_notices") == 3
    assert _scalar(admin, "SELECT status FROM ops.parent_notices WHERE id = :i", i=drafting) == (
        "drafting"
    )
    assert _scalar(admin, "SELECT status FROM ops.parent_notices WHERE id = :i", i=failed) == (
        "draft_failed"
    )
    assert _scalar(admin, "SELECT count(*) FROM audit.events") == events
    new_rows = "parent_notices_(status_check|approved_complete)"
    with pytest.raises(DBAPIError, match=new_rows), admin.begin() as c:
        _notice_row(c, tenant_id, user_id, source="blank", status="drafting")

    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM ops.parent_notices") == 3
    with admin.begin() as c:
        _notice_row(
            c, tenant_id, user_id, source="staff_text", status="drafting", source_text="Again"
        )
    with admin.begin() as c:
        c.execute(
            text("DELETE FROM ops.parent_notices WHERE status IN ('drafting','draft_failed')")
        )
    command.downgrade(populated.cfg, before)
    command.upgrade(populated.cfg, "head")
