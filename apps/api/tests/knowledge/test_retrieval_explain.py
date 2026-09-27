"""EXPLAIN: each retrieval branch reaches one school's rows through an index (docs/06 §6).

A shared-tier-like table (one large synthetic school with 3,000 chunks and nine small ones with
300 each, random 1024-dim vectors) is queried as ``sos_app`` under RLS with the retrieval
settings applied. What the planner does, and what these tests pin:

- vector branch: for the large school an HNSW index scan (``ORDER BY embedding <=> :q``); for
  a small school the planner prefers the exact nearest neighbours of its few rows via the
  tenant-leading btree. Either way no sequential scan over every school. When HNSW is used, the
  pgvector iterative scan fills the candidate list although most of the shared graph belongs to
  other schools (without it RLS drops most of the ``ef_search`` neighbours);
- full-text and keyword branches: the tenant-leading btree, never a sequential scan of every
  school's chunks. They cannot use GIN: under FORCE RLS PostgreSQL only turns LEAKPROOF operators
  into index conditions, and ``@@``, ``%``/``<%`` and ``&&`` are not leakproof. The last test pins
  that fact, so if it ever changes the GIN indexes docs/05 sketches become worth adding.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.db import tenant_session
from app.knowledge.config.retrieval import RetrievalConfig, load_retrieval_config
from app.knowledge.domain import SearchFilters
from app.knowledge.retrieval.hybrid import (
    HybridRetriever,
    apply_session_settings,
    branch_statements,
)


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

LARGE_SCHOOL_CHUNKS = 3000
SMALL_SCHOOLS = 9
SMALL_SCHOOL_CHUNKS = 300

_GENERATE = text(
    """
    INSERT INTO kb.document_chunks (id, tenant_id, document_id, version_id, chunk_no, page_from,
      page_to, context_header, content, token_count, embedding, embedding_model, doc_type,
      sensitivity, acl_roles, is_latest)
    SELECT gen_random_uuid(), :t, :d, :v, g, g + 1, g + 1,
      '[Circular] Synthetic DEO - Rc.No.' || g || '/B/2026 - Subject: topic' || (g % 50),
      'synthetic circular ' || g || ' about topic' || (g % 50) || ' word' || (g % 97) || ' '
        || md5(g::text || CAST(:t AS text)),
      30,
      CAST(CAST((SELECT array_agg(random() - 0.5) FROM generate_series(1, 1024) WHERE g >= 0)
                AS vector(1024)) AS halfvec(1024)),
      'synthetic-embed-1', 'circular', 'C1', ARRAY['teacher'], true
    FROM generate_series(0, :n - 1) AS g
    """
)


@dataclasses.dataclass
class Corpus:
    large: uuid.UUID
    small: list[uuid.UUID]


def _school(admin: Engine, chunks: int) -> uuid.UUID:
    t: uuid.UUID = R.make_tenant(admin)
    doc = R.make_document(admin, t)
    v = R.add_version(admin, doc)
    with admin.begin() as c:
        c.execute(text("SELECT setseed(0.42)"))
        c.execute(_GENERATE, {"t": t, "d": doc.document_id, "v": v, "n": chunks})
    return t


@pytest.fixture(scope="module")
def corpus(admin_engine: Engine, app_engine: Engine) -> Iterator[Corpus]:
    # Start from a compact table. The large school holds about half of the rows, so whether
    # its tenant btree beats a sequential scan depends on its rows lying together on disk.
    # After earlier corpora were deleted (a shuffled order rebuilds this module fixture
    # between other modules' tests), new rows filled scattered free space and the planner
    # rightly chose a sequential scan. VACUUM FULL makes the layout the same in every order.
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text("VACUUM FULL kb.document_chunks"))
    large = _school(admin_engine, LARGE_SCHOOL_CHUNKS)
    small = [_school(admin_engine, SMALL_SCHOOL_CHUNKS) for _ in range(SMALL_SCHOOLS)]
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text("ANALYZE kb.document_chunks"))
    yield Corpus(large, small)
    R.delete_tenant_data(admin_engine, [large, *small])


@pytest.fixture(autouse=True)
def _fresh_statistics(admin_engine: Engine) -> None:
    """Plans follow the table statistics. Other modules insert and delete chunks between these
    tests when the order is shuffled, so analyse the table as it is now (as autovacuum would)
    instead of relying on the statistics taken when the corpus was built."""
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text("ANALYZE kb.document_chunks"))


def _explain(s: Session, config: RetrievalConfig, branch: str) -> list[dict[str, Any]]:
    apply_session_settings(s, config)
    qt = HybridRetriever(config).query_texts(s, R.query("topic7 Rc.No.57/B/2026", "unrelated"))[0]
    stmt = branch_statements(config, R.keys(roles={"teacher"}), SearchFilters(), qt)[branch]  # type: ignore[index]
    nodes: list[dict[str, Any]] = R.plan_nodes(s.execute(R.Explain(stmt)).scalar_one())
    return nodes


def _plan(tenant: uuid.UUID, branch: str) -> list[dict[str, Any]]:
    with tenant_session(tenant) as s:
        return _explain(s, load_retrieval_config(), branch)


def _no_seq_scan(nodes: list[dict[str, Any]]) -> None:
    assert not any(n["Node Type"] == "Seq Scan" for n in nodes), nodes


def _uses_tenant_btree(nodes: list[dict[str, Any]]) -> bool:
    return any(
        n.get("Index Name") in {"document_chunks_filter", "document_chunks_document"}
        and "tenant_id" in n.get("Index Cond", "")
        for n in nodes
    )


def test_FR_KB_001_vector_branch_uses_hnsw_for_a_large_school(corpus: Corpus) -> None:
    nodes = _plan(corpus.large, "vector")
    assert any(n.get("Index Name") == "document_chunks_embedding_hnsw" for n in nodes), nodes
    _no_seq_scan(nodes)


def test_FR_KB_001_vector_branch_uses_an_index_for_a_small_school(corpus: Corpus) -> None:
    nodes = _plan(corpus.small[3], "vector")
    assert _uses_tenant_btree(nodes) or any(
        n.get("Index Name") == "document_chunks_embedding_hnsw" for n in nodes
    ), nodes
    _no_seq_scan(nodes)


@pytest.mark.parametrize("branch", ["full_text", "trigram"])
@pytest.mark.parametrize("which", ["large", "small"])
def test_FR_KB_001_text_branches_use_the_tenant_index(
    corpus: Corpus, branch: str, which: str
) -> None:
    tenant = corpus.large if which == "large" else corpus.small[3]
    nodes = _plan(tenant, branch)
    assert _uses_tenant_btree(nodes), nodes
    _no_seq_scan(nodes)


def _vector_rows_through_hnsw(tenant: uuid.UUID, config: RetrievalConfig) -> int:
    """Rows the vector branch returns when the planner is made to use HNSW (small school)."""
    with tenant_session(tenant) as s:
        s.execute(
            text(
                "SELECT set_config('enable_seqscan', 'off', true), "
                "set_config('enable_bitmapscan', 'off', true), "
                "set_config('enable_indexscan', 'on', true), "
                "set_config('enable_sort', 'off', true)"
            )
        )
        nodes = _explain(s, config, "vector")
        assert any(n.get("Index Name") == "document_chunks_embedding_hnsw" for n in nodes), nodes
        qt = HybridRetriever(config).query_texts(s, R.query("x", "unrelated"))[0]
        stmt = branch_statements(config, R.keys(roles={"teacher"}), SearchFilters(), qt)["vector"]
        return len(s.execute(stmt).all())


def test_FR_KB_001_iterative_scan_fills_the_vector_list_for_one_school(corpus: Corpus) -> None:
    config = load_retrieval_config()
    limit = config.branches.vector.limit
    assert config.branches.vector.iterative_scan != "off"
    assert _vector_rows_through_hnsw(corpus.small[3], config) == limit
    # Without it, HNSW returns ef_search neighbours from ALL schools and RLS drops most of them.
    vector_off = config.branches.vector.model_copy(update={"iterative_scan": "off"})
    off = config.model_copy(
        update={"branches": config.branches.model_copy(update={"vector": vector_off})}
    )
    assert _vector_rows_through_hnsw(corpus.small[3], off) < limit


def test_hybrid_search_returns_only_the_schools_chunks(corpus: Corpus) -> None:
    for tenant in (corpus.large, corpus.small[0]):
        with tenant_session(tenant) as s:
            results = HybridRetriever().search(
                s, R.keys(roles={"teacher"}), R.query("topic7", "x", k=12)
            )
            ids = [r.chunk_id for r in results]
            owned: set[uuid.UUID] = set(
                s.execute(
                    text("SELECT id FROM kb.document_chunks WHERE id = ANY(:i)"), {"i": ids}
                ).scalars()
            )
        assert results
        assert set(ids) == owned
        # One document per school here: diversity caps the list (docs/06 §6).
        assert len(results) <= load_retrieval_config().diversity.max_chunks_per_document


def test_rls_blocks_gin_for_text_operators(admin_engine: Engine) -> None:
    """Why there is no GIN index on content_tsv / context_header / ACL arrays (see docstring)."""
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT o.oprname, p.proleakproof "
                "FROM pg_operator o JOIN pg_proc p ON p.oid = o.oprcode "
                "WHERE (o.oprname = '@@' AND o.oprleft = 'tsvector'::regtype "
                "       AND o.oprright = 'tsquery'::regtype) "
                "   OR (o.oprname IN ('<%', '%') AND o.oprleft = 'text'::regtype) "
                "   OR (o.oprname = '&&' AND o.oprleft = 'anyarray'::regtype)"
            )
        ).all()
    assert {r.oprname for r in rows} == {"@@", "<%", "%", "&&"}
    assert not any(r.proleakproof for r in rows), rows
