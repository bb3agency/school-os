"""Schema of kb documents (migration 0009_kb_documents; docs/05 §6, FR-DOC-003, SEC-001)."""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, Table, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.db import tenant_session
from app.documents import models

pytestmark = pytest.mark.db
S = sys.modules["sos_test_documents_support"]

MODELS = [models.Document, models.DocumentVersion, models.DocumentAcl, models.UploadIntent]


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__tablename__)
def test_FR_DOC_005_models_match_database(model: Any, admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    insp = inspect(admin_engine)
    db_cols = {c["name"]: c for c in insp.get_columns(table.name, schema=table.schema)}
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"
    pk = insp.get_pk_constraint(table.name, schema=table.schema)["constrained_columns"]
    assert sorted(pk) == sorted(c.name for c in table.primary_key.columns)


def _intent_sql() -> str:
    return (
        "INSERT INTO kb.upload_intents (id, tenant_id, purpose, document_id, version_no, "
        "object_key, declared_content_type, declared_size, max_bytes, created_by, expires_at) "
        "VALUES (:i, :t, 'circular', :d, 1, :k, 'application/pdf', 10, 100, :u, "
        "now() + interval '10 minutes')"
    )


def test_FR_DOC_003_object_keys_must_be_inside_the_tenant_prefix(
    world: Any, app_engine: Engine
) -> None:
    a, b = world.a.tenant_id, world.b.tenant_id
    owner = world.a.people["owner"]
    doc = uuid.uuid4()
    for key in (
        f"t/{b}/uploads/{doc}/original.pdf",  # another school's prefix
        f"t/{a}/../{b}/uploads/{doc}/original.pdf",  # traversal
        f"uploads/{doc}/original.pdf",  # no tenant prefix at all
        f"t/{a}/uploads/{doc}/original.exe",  # not an allowlisted extension
        f"t/{a}/docs/{doc}/v1/original.pdf",  # presigned POSTs never target final keys
    ):
        with (
            pytest.raises(IntegrityError),
            tenant_session(a, owner.user_id, engine=app_engine) as s,
        ):
            s.execute(
                text(_intent_sql()),
                {"i": uuid.uuid4(), "t": a, "d": doc, "k": key, "u": owner.user_id},
            )


def test_SEC_016_intents_are_short_lived_and_size_bounded(world: Any, app_engine: Engine) -> None:
    a = world.a.tenant_id
    owner = world.a.people["owner"]
    doc = uuid.uuid4()
    key = f"t/{a}/uploads/{uuid.uuid4()}/original.pdf"
    with (
        pytest.raises(IntegrityError, match="upload_intents_short_lived"),
        tenant_session(a, owner.user_id, engine=app_engine) as s,
    ):
        s.execute(
            text(_intent_sql().replace("interval '10 minutes'", "interval '2 hours'")),
            {"i": uuid.uuid4(), "t": a, "d": doc, "k": key, "u": owner.user_id},
        )
    with (
        pytest.raises(IntegrityError, match="upload_intents_size_within_max"),
        tenant_session(a, owner.user_id, engine=app_engine) as s,
    ):
        s.execute(
            text(_intent_sql().replace("10, 100", "1000, 100")),
            {"i": uuid.uuid4(), "t": a, "d": doc, "k": key, "u": owner.user_id},
        )


def test_SEC_001_acl_cannot_reference_another_schools_document(
    world: Any, admin_engine: Engine, app_engine: Engine
) -> None:
    b_doc = S.make_document(admin_engine, world.b.tenant_id, world.b.people["owner"].user_id)
    a = world.a.tenant_id
    owner = world.a.people["owner"]
    with (
        pytest.raises(IntegrityError),
        tenant_session(a, owner.user_id, engine=app_engine) as s,
    ):
        s.execute(
            text(
                "INSERT INTO kb.document_acl (tenant_id, document_id, principal_type, "
                "principal_ref) VALUES (:t, :d, 'role', 'owner')"
            ),
            {"t": a, "d": b_doc},
        )


def test_SEC_001_documents_of_another_school_are_invisible(
    world: Any, admin_engine: Engine, app_engine: Engine
) -> None:
    b_doc = S.make_document(admin_engine, world.b.tenant_id, world.b.people["owner"].user_id)
    with tenant_session(world.a.tenant_id, engine=app_engine) as s:
        queries = {
            "kb.documents": "SELECT count(*) FROM kb.documents WHERE id = :d",
            "kb.document_versions": "SELECT count(*) FROM kb.document_versions "
            "WHERE document_id = :d",
        }
        for table, sql in queries.items():
            assert s.execute(text(sql), {"d": b_doc}).scalar_one() == 0, table


def test_FR_DOC_008_status_values_are_constrained(world: Any, admin_engine: Engine) -> None:
    doc = S.make_document(admin_engine, world.a.tenant_id, world.a.people["owner"].user_id)
    with pytest.raises(IntegrityError), admin_engine.begin() as c:
        c.execute(
            text("UPDATE kb.document_versions SET status = 'infected' WHERE document_id = :d"),
            {"d": doc},
        )
    with pytest.raises(IntegrityError), admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE kb.document_versions SET error = 'Free text with a name' "
                "WHERE document_id = :d"
            ),
            {"d": doc},
        )


def test_deleting_a_document_cascades_versions_and_acl(world: Any, admin_engine: Engine) -> None:
    doc = S.make_document(
        admin_engine,
        world.a.tenant_id,
        world.a.people["owner"].user_id,
        acl=[("role", "principal")],
    )
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE kb.documents SET current_version_id = NULL WHERE id = :d"), {"d": doc}
        )
        c.execute(text("DELETE FROM kb.documents WHERE id = :d"), {"d": doc})
    with admin_engine.connect() as c:
        for table in ("kb.document_versions", "kb.document_acl"):
            n: Any = c.execute(
                text(f"SELECT count(*) FROM {table} WHERE document_id = :d"), {"d": doc}
            ).scalar_one()
            assert n == 0


def test_intent_expiry_column_is_timezone_aware(world: Any, admin_engine: Engine) -> None:
    intent = S.make_intent(
        admin_engine, world.a.tenant_id, world.a.people["owner"].user_id, S.pdf()
    )
    with admin_engine.connect() as c:
        value: Any = c.execute(
            text("SELECT expires_at FROM kb.upload_intents WHERE id = :i"), {"i": intent}
        ).scalar_one()
    assert value.tzinfo is not None
    assert value > dt.datetime.now(dt.UTC)


def test_FR_DOC_003_version_keys_must_be_inside_the_tenant_prefix(
    world: Any, admin_engine: Engine, app_engine: Engine
) -> None:
    owner = world.a.people["owner"]
    doc = S.make_document(admin_engine, world.a.tenant_id, owner.user_id)
    foreign = f"t/{world.b.tenant_id}/docs/{doc}/v2/original.pdf"
    with (
        pytest.raises(IntegrityError, match="document_versions_key_in_tenant_prefix"),
        tenant_session(world.a.tenant_id, owner.user_id, engine=app_engine) as s,
    ):
        s.execute(
            text(
                "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
                "(:v, :t, :d, 2, :k, :h, 'application/pdf', 10, 'queued', :u)"
            ),
            {
                "v": uuid.uuid4(),
                "t": world.a.tenant_id,
                "d": doc,
                "k": foreign,
                "h": b"\x00" * 32,
                "u": owner.user_id,
            },
        )
