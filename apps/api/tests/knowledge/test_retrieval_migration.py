"""0021_kb_tables walks down and up on a POPULATED database (CLAUDE.md §6.12, §8; docs/05 §14).

A fresh database is migrated to head and seeded with a synthetic school, a document with two
versions, chunks (one promoted), an embedding-cache entry, a query-log row and a verified
answer. Downgrading to 0020 drops the four M2 tables (documented as lossy) and leaves the M1
document tables and their data intact; upgrading again recreates them empty and usable.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.knowledge.models import vector_literal

pytestmark = pytest.mark.db
REVISION = "0021_kb_tables"
PREVIOUS = "0020_provisioning_runs"
DB = "schoolos_kb_migration"
KB_TABLES = {"document_chunks", "embedding_cache", "queries", "verified_answers"}


def _vec(seed: int) -> str:
    return vector_literal([1.0 if i == seed else 0.0 for i in range(1024)])


def _seed(admin: Engine) -> dict[str, uuid.UUID]:
    t, d, v1, v2 = (uuid.uuid4() for _ in range(4))
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'S', 'active')"
            ),
            {"i": t, "c": f"kbm-{t.hex[:10]}"},
        )
        c.execute(
            text(
                "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, sensitivity, "
                "created_by) VALUES (:d, :t, 'circular', 'circular', 'Synthetic', 'C1', :u)"
            ),
            {"d": d, "t": t, "u": uuid.uuid4()},
        )
        for n, v in ((1, v1), (2, v2)):
            c.execute(
                text(
                    "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                    "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
                    "(:v, :t, :d, :n, :k, :h, 'application/pdf', 10, 'ready', :u)"
                ),
                {
                    "v": v,
                    "t": t,
                    "d": d,
                    "n": n,
                    "k": f"t/{t}/docs/{d}/v{n}/original.pdf",
                    "h": hashlib.sha256(v.bytes).digest(),
                    "u": uuid.uuid4(),
                },
            )
            c.execute(
                text(
                    "INSERT INTO kb.document_chunks (id, tenant_id, document_id, version_id, "
                    "chunk_no, content, token_count, embedding, embedding_model, doc_type, "
                    "sensitivity, acl_roles, is_latest) VALUES (:i, :t, :d, :v, 0, :c, 3, "
                    "CAST(:e AS halfvec(1024)), 'synthetic-embed-1', 'circular', 'C1', "
                    "ARRAY['teacher'], :l)"
                ),
                {
                    "i": uuid.uuid4(),
                    "t": t,
                    "d": d,
                    "v": v,
                    "c": f"synthetic chunk v{n}",
                    "e": _vec(n),
                    "l": n == 2,
                },
            )
        c.execute(
            text(
                "INSERT INTO kb.embedding_cache (tenant_id, model, input_type, content_sha256, "
                "embedding) VALUES (:t, 'synthetic-embed-1', 'query', :h, "
                "CAST(:e AS halfvec(1024)))"
            ),
            {"t": t, "h": hashlib.sha256(b"synthetic").digest(), "e": _vec(7)},
        )
        c.execute(
            text(
                "INSERT INTO kb.queries (id, tenant_id, session_id, user_id, question_ciphertext, "
                "question_hmac, key_version, mode, status) VALUES (:i, :t, :s, :u, :q, :h, 1, "
                "'full', 'answered')"
            ),
            {
                "i": uuid.uuid4(),
                "t": t,
                "s": uuid.uuid4(),
                "u": uuid.uuid4(),
                "q": b"\x01synthetic-ciphertext",
                "h": hashlib.sha256(b"k").digest(),
            },
        )
        c.execute(
            text(
                "INSERT INTO kb.verified_answers (id, tenant_id, question_canonical, language, "
                "answer_text, citations, verified_by, verified_at) VALUES (:i, :t, 'q', 'en', "
                "'a', CAST(:c AS jsonb), :u, now())"
            ),
            {
                "i": uuid.uuid4(),
                "t": t,
                "c": f'[{{"source": "sos://doc/{d}/v2#p1", "cited_text": "synthetic"}}]',
                "u": uuid.uuid4(),
            },
        )
    return {"tenant": t, "document": d}


@pytest.fixture
def populated(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> Iterator[tuple[Config, Engine]]:
    url = test_database.create_fresh(DB)
    cfg = make_alembic_config(url)
    command.upgrade(cfg, "head")
    admin = create_engine(
        make_url(test_database.admin_url).set(database=DB).render_as_string(hide_password=False)
    )
    try:
        yield cfg, admin
    finally:
        admin.dispose()


def _kb_tables(admin: Engine) -> set[str]:
    with admin.connect() as c:
        return set(
            c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'kb'")).scalars()
        )


def _count(admin: Engine, sql: str) -> int:
    with admin.connect() as c:
        return int(c.execute(text(sql)).scalar_one())


def test_CLAUDE_6_12_kb_tables_round_trip_with_data(populated: tuple[Config, Engine]) -> None:
    cfg, admin = populated
    ids = _seed(admin)
    assert _kb_tables(admin) >= KB_TABLES
    assert _count(admin, "SELECT count(*) FROM kb.document_chunks") == 2

    command.downgrade(cfg, PREVIOUS)
    assert not KB_TABLES & _kb_tables(admin)
    assert _count(admin, "SELECT count(*) FROM kb.document_versions") == 2
    assert (
        _count(
            admin,
            "SELECT count(*) FROM pg_constraint "
            "WHERE conname = 'document_versions_tenant_document_id_key'",
        )
        == 0
    )

    command.upgrade(cfg, REVISION)
    assert _kb_tables(admin) >= KB_TABLES
    assert _count(admin, "SELECT count(*) FROM kb.document_chunks") == 0
    # Usable again with the M1 data still present: re-seed a second school, walk once more.
    _seed(admin)
    command.downgrade(cfg, PREVIOUS)
    command.upgrade(cfg, "head")
    assert _count(admin, f"SELECT count(*) FROM kb.documents WHERE id = '{ids['document']}'") == 1
