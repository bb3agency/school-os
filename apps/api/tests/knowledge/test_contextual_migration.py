"""0040_contextual_retrieval walks down and up on a POPULATED database (invariant 12, docs/06).$

FR-KB-001, FR-KB-009. A fresh database at head gets a synthetic school, a document version with a
contextualised chunk and a plain one, and ledger rows for the ``contextualize`` feature with a
document id. The generated ``context_tsv`` follows ``chunk_context``; the status checks refuse an
inconsistent row. Downgrading drops the new columns and index (documented as lossy), keeps the
chunks and the metered rows, and refuses new ``contextualize`` rows; upgrading again restores
everything with the old chunks at ``none``.
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
from sqlalchemy.exc import IntegrityError

from app.knowledge.models import vector_literal

pytestmark = pytest.mark.db
REVISION = "0040_contextual_retrieval"
PREVIOUS = "0037_notice_drafting"
DB = "schoolos_kb_contextual_migration"


def _vec() -> str:
    return vector_literal([1.0 if i == 3 else 0.0 for i in range(1024)])


@pytest.fixture
def populated(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> Iterator[tuple[Config, Engine, dict[str, uuid.UUID]]]:
    url = test_database.create_fresh(DB)
    cfg = make_alembic_config(url)
    command.upgrade(cfg, "head")
    admin = create_engine(
        make_url(test_database.admin_url).set(database=DB).render_as_string(hide_password=False)
    )
    t, d, v = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'S', 'active')"
            ),
            {"i": t, "c": f"ctx-{t.hex[:10]}"},
        )
        c.execute(
            text(
                "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, sensitivity, "
                "created_by) VALUES (:d, :t, 'circular', 'circular', 'Synthetic', 'C1', :u)"
            ),
            {"d": d, "t": t, "u": uuid.uuid4()},
        )
        c.execute(
            text(
                "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
                "(:v, :t, :d, 1, :k, :h, 'application/pdf', 10, 'ready', :u)"
            ),
            {
                "v": v,
                "t": t,
                "d": d,
                "k": f"t/{t}/docs/{d}/v1/original.pdf",
                "h": hashlib.sha256(v.bytes).digest(),
                "u": uuid.uuid4(),
            },
        )
    ids = {"tenant": t, "document": d, "version": v}
    _chunk(admin, ids, 1, context="Synthetic science fair circular, payment part.", status="ok")
    _chunk(admin, ids, 2)
    _call(admin, ids)
    try:
        yield cfg, admin, ids
    finally:
        admin.dispose()


def _chunk(
    admin: Engine,
    ids: dict[str, uuid.UUID],
    number: int,
    *,
    context: str = "",
    status: str = "none",
    model: str | None = "claude-haiku-4-5-20251001",
    prompt: str | None = "contextualize.v1",
) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.document_chunks (id, tenant_id, document_id, version_id, chunk_no, "
                "content, token_count, embedding, embedding_model, doc_type, sensitivity, "
                "is_latest, chunk_context, context_status, context_model, context_prompt) VALUES "
                "(:i, :t, :d, :v, :n, 'Pay the said fee by the date above.', 9, "
                "CAST(:e AS halfvec(1024)), 'synthetic-embed-1', 'circular', 'C1', true, :cx, "
                ":st, :m, :p)"
            ),
            {
                "i": uuid.uuid4(),
                "t": ids["tenant"],
                "d": ids["document"],
                "v": ids["version"],
                "n": number,
                "e": _vec(),
                "cx": context,
                "st": status,
                "m": model if status == "ok" else None,
                "p": prompt if status == "ok" else None,
            },
        )


def _call(admin: Engine, ids: dict[str, uuid.UUID], *, document: bool = True) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.llm_calls (id, tenant_id, feature, role, provider, model, outcome, "
                "attempts, latency_ms, input_tokens, output_tokens, cost_usd"
                + (", document_id" if document else "")
                + ") VALUES (:i, :t, 'contextualize', 'contextualize', 'fake', "
                "'claude-haiku-4-5-20251001', 'ok', 1, 5, 10, 2, 0.0001"
                + (", :d" if document else "")
                + ")"
            ),
            {"i": uuid.uuid4(), "t": ids["tenant"], "d": ids["document"]},
        )


def _scalar(admin: Engine, sql: str, **params: Any) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def _columns(admin: Engine, table: str) -> set[str]:
    with admin.connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'kb' AND table_name = :t"
                ),
                {"t": table},
            ).scalars()
        )


def test_FR_KB_001_context_tsv_is_generated_from_the_chunk_context(
    populated: tuple[Config, Engine, dict[str, uuid.UUID]],
) -> None:
    _, admin, _ = populated
    assert _scalar(
        admin,
        "SELECT count(*) FROM kb.document_chunks WHERE context_tsv @@ "
        "to_tsquery('simple', 'exhibition | science')",
    )
    assert (
        _scalar(admin, "SELECT count(*) FROM kb.document_chunks WHERE context_tsv = ''::tsvector")
        == 1
    )
    assert _scalar(
        admin,
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'document_chunks_context_pending'",
    )


@pytest.mark.parametrize(
    ("context", "status", "model"),
    [
        ("", "ok", "claude-haiku-4-5-20251001"),  # ok needs a context
        ("A context without a status", "none", None),  # a context means ok
        ("A context", "ok", None),  # ok needs the model and prompt
        ("", "accepted", None),  # unknown status
        ("x" * 1001, "ok", "claude-haiku-4-5-20251001"),  # length cap
    ],
)
def test_FR_KB_001_context_columns_refuse_inconsistent_rows(
    populated: tuple[Config, Engine, dict[str, uuid.UUID]],
    context: str,
    status: str,
    model: str | None,
) -> None:
    _, admin, ids = populated
    with pytest.raises(IntegrityError), admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.document_chunks (id, tenant_id, document_id, version_id, "
                "chunk_no, content, token_count, embedding, embedding_model, doc_type, "
                "sensitivity, chunk_context, context_status, context_model, context_prompt) "
                "VALUES (:i, :t, :d, :v, 9, 'text', 1, CAST(:e AS halfvec(1024)), "
                "'synthetic-embed-1', 'circular', 'C1', :cx, :st, :m, :p)"
            ),
            {
                "i": uuid.uuid4(),
                "t": ids["tenant"],
                "d": ids["document"],
                "v": ids["version"],
                "e": _vec(),
                "cx": context,
                "st": status,
                "m": model,
                "p": "contextualize.v1" if model else None,
            },
        )


def test_invariant_12_0040_round_trips_on_a_populated_database(
    populated: tuple[Config, Engine, dict[str, uuid.UUID]],
) -> None:
    cfg, admin, ids = populated
    command.downgrade(cfg, PREVIOUS)
    assert not {"chunk_context", "context_tsv", "context_status"} & _columns(
        admin, "document_chunks"
    )
    assert "document_id" not in _columns(admin, "llm_calls")
    assert _scalar(admin, "SELECT count(*) FROM kb.document_chunks") == 2
    assert _scalar(admin, "SELECT count(*) FROM kb.llm_calls WHERE feature = 'contextualize'") == 1
    with pytest.raises(IntegrityError):
        _call(admin, ids, document=False)

    command.upgrade(cfg, REVISION)
    assert (
        _scalar(admin, "SELECT count(*) FROM kb.document_chunks WHERE context_status = 'none'") == 2
    )
    _chunk(admin, ids, 3, context="Synthetic sports day circular, timings.", status="ok")
    _call(admin, ids)
    assert (
        _scalar(
            admin, "SELECT count(*) FROM kb.llm_calls WHERE document_id = :d", d=ids["document"]
        )
        == 1
    )
