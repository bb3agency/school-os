"""Schema of the M2 knowledge tables (0021_kb_tables; docs/05 §6.2, SEC-001, FR-KB-002, FR-KB-009).

RLS ENABLE + FORCE with the standard policy on every table, composite tenant FKs (a chunk's
version must belong to its document and its school), role grants, the generated tsvector and
the fail-closed ``is_latest`` default. The generic catalog suites (tests/security) cover these
tables automatically as well; these tests pin the specific decisions.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, Table, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.db import tenant_session
from app.knowledge import models
from app.knowledge.config.embeddings import load_embeddings_config
from app.knowledge.models import vector_literal


def _load() -> ModuleType:
    name = "sos_test_kb_retrieval_support"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("retrieval_support.py")
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


R = _load()
pytestmark = pytest.mark.db

MODELS = [models.DocumentChunk, models.EmbeddingCacheEntry, models.Query, models.VerifiedAnswer]
TABLES = ["kb.document_chunks", "kb.embedding_cache", "kb.queries", "kb.verified_answers"]


@pytest.mark.filterwarnings("ignore:Did not recognize type 'halfvec'")
@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__tablename__)
def test_models_match_database(model: Any, admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    insp = inspect(admin_engine)
    db_cols = {c["name"]: c for c in insp.get_columns(table.name, schema=table.schema)}
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"
    pk = insp.get_pk_constraint(table.name, schema=table.schema)["constrained_columns"]
    assert sorted(pk) == sorted(c.name for c in table.primary_key.columns)


def test_SEC_001_kb_tables_force_rls_with_the_standard_policy(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT n.nspname || '.' || c.relname AS name, c.relrowsecurity AS rls, "
                "c.relforcerowsecurity AS force, "
                "ARRAY(SELECT p.polname::text FROM pg_policy p WHERE p.polrelid = c.oid) AS pols, "
                "(SELECT pg_get_expr(p.polqual, p.polrelid) FROM pg_policy p "
                " WHERE p.polrelid = c.oid AND p.polname = 'tenant_isolation') AS qual "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname || '.' || c.relname = ANY(:t)"
            ),
            {"t": TABLES},
        ).all()
    assert sorted(r.name for r in rows) == sorted(TABLES)
    for r in rows:
        assert r.rls, r.name
        assert r.force, r.name
        # No definer_access (ADR-0013); the restrictive offboarding purge policy for sos_purger
        # only (ADR-0029; shape pinned by tests/tenancy/test_offboarding_purge.py).
        assert sorted(r.pols) == ["offboarding_purge", "tenant_isolation"], r.name
        assert "current_tenant()" in r.qual, r.name


def test_ADR_0013_chunk_foreign_keys_are_composite(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        fks: dict[str, str] = dict(
            c.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE contype = 'f' AND conrelid = ANY(ARRAY['kb.document_chunks'::regclass, "
                    "'kb.verified_answers'::regclass])"
                )
            ).all()
        )
    assert "(tenant_id, document_id, version_id)" in fks["document_chunks_version_fk"]
    assert "kb.document_versions(tenant_id, document_id, id)" in fks["document_chunks_version_fk"]
    assert "(tenant_id, document_id)" in fks["document_chunks_document_fk"]
    assert "(tenant_id, academic_year_id)" in fks["document_chunks_academic_year_fk"]
    assert "(tenant_id, document_id)" in fks["verified_answers_document_fk"]


def test_SEC_001_role_grants(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:

        def has(role: str, table: str, priv: str) -> bool:
            return bool(
                c.execute(
                    text("SELECT has_table_privilege(:r, :t, :p)"),
                    {"r": role, "t": table, "p": priv},
                ).scalar_one()
            )

        for table in TABLES:
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert has("sos_app", table, priv), (table, priv)
                assert not has("sos_platform", table, priv), (table, priv)
            assert not has("sos_app", table, "TRUNCATE"), table
        assert not has("sos_readonly", "kb.embedding_cache", "SELECT")
        for table in ("kb.document_chunks", "kb.queries", "kb.verified_answers"):
            assert has("sos_readonly", table, "SELECT"), table
            assert not has("sos_readonly", table, "INSERT"), table


def test_embedding_column_matches_the_embeddings_config(admin_engine: Engine) -> None:
    storage = load_embeddings_config().storage
    assert storage.dimensions == models.EMBEDDING_DIMENSIONS
    with admin_engine.connect() as c:
        types: dict[str, str] = dict(
            c.execute(
                text(
                    "SELECT attrelid::regclass::text, format_type(atttypid, atttypmod) "
                    "FROM pg_attribute WHERE attname = 'embedding' AND attrelid = ANY(ARRAY["
                    "'kb.document_chunks'::regclass, 'kb.embedding_cache'::regclass])"
                )
            ).all()
        )
    expected = f"{storage.precision}({storage.dimensions})"
    assert types == {"kb.document_chunks": expected, "kb.embedding_cache": expected}


@pytest.fixture
def two_docs(admin_engine: Engine, app_engine: Engine) -> Any:
    a, b = R.make_tenant(admin_engine), R.make_tenant(admin_engine)
    docs = {}
    for name, tenant in (("a1", a), ("a2", a), ("b1", b)):
        d = R.make_document(admin_engine, tenant)
        R.add_version(admin_engine, d)
        docs[name] = d
    yield a, b, docs
    R.delete_tenant_data(admin_engine, [a, b])


def _insert_chunk(tenant: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID) -> uuid.UUID:
    cid = uuid.uuid4()
    with tenant_session(tenant) as s:
        s.execute(
            text(
                "INSERT INTO kb.document_chunks (id, tenant_id, document_id, version_id, chunk_no, "
                "context_header, content, token_count, embedding, embedding_model, doc_type, "
                "sensitivity) VALUES (:i, :t, :d, :v, 0, '[Circular] Synthetic DEO', "
                "'zebra timings', 2, CAST(:e AS halfvec(1024)), 'synthetic-embed-1', "
                "'circular', 'C1')"
            ),
            {
                "i": cid,
                "t": tenant,
                "d": document_id,
                "v": version_id,
                "e": vector_literal(R.topic_vector("x")),
            },
        )
    return cid


def test_FR_KB_002_new_chunks_are_hidden_until_promoted(two_docs: Any) -> None:
    a, _, docs = two_docs
    d = docs["a1"]
    cid = _insert_chunk(a, d.document_id, d.versions[0])
    with tenant_session(a) as s:
        row = s.execute(
            text(
                "SELECT is_latest, content_tsv::text AS tsv FROM kb.document_chunks WHERE id = :i"
            ),
            {"i": cid},
        ).one()
    assert row.is_latest is False
    # The contextual header is searchable (docs/06 §4.5) and nothing is stemmed ('simple').
    for lexeme in ("'circular'", "'synthetic'", "'deo'", "'zebra'", "'timings'"):
        assert lexeme in row.tsv


def test_ADR_0013_a_chunk_cannot_point_at_another_documents_version(two_docs: Any) -> None:
    a, _, docs = two_docs
    with pytest.raises(IntegrityError, match="document_chunks_version_fk"):
        _insert_chunk(a, docs["a1"].document_id, docs["a2"].versions[0])


def test_ADR_0013_a_chunk_cannot_point_at_another_schools_version(two_docs: Any) -> None:
    a, _, docs = two_docs
    with pytest.raises(IntegrityError):
        _insert_chunk(a, docs["b1"].document_id, docs["b1"].versions[0])


def test_SEC_001_rls_refuses_a_chunk_for_another_tenant(two_docs: Any) -> None:
    a, b, docs = two_docs
    cid = uuid.uuid4()
    with pytest.raises(DBAPIError, match="row-level security"), tenant_session(a) as s:
        s.execute(
            text(
                "INSERT INTO kb.document_chunks (id, tenant_id, document_id, version_id, chunk_no, "
                "content, token_count, embedding, embedding_model, doc_type, sensitivity) "
                "VALUES (:i, :t, :d, :v, 0, 'x', 1, CAST(:e AS halfvec(1024)), 'm-1', "
                "'circular', 'C1')"
            ),
            {
                "i": cid,
                "t": b,
                "d": docs["b1"].document_id,
                "v": docs["b1"].versions[0],
                "e": vector_literal(R.topic_vector("x")),
            },
        )


def test_FR_KB_009_query_log_holds_no_plaintext_question(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        cols = {
            r.column_name: r.data_type
            for r in c.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'kb' AND table_name = 'queries'"
                )
            )
        }
    assert cols["question_ciphertext"] == "bytea"
    assert cols["answer_ciphertext"] == "bytea"
    assert cols["question_hmac"] == "bytea"
    assert not {"question", "question_text", "answer", "answer_text", "question_sha256"} & set(cols)
