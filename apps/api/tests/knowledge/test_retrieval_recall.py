"""Authorised retrieval recall: the vector branch against an exact oracle (docs/06 §6).

FR-KB-001 (recall), FR-KB-002 / SEC-018 (filters in SQL before ranking), FR-KB-007 (a missed
passage becomes a false "not found in school records").

The shared tier keeps every school's chunks in one HNSW graph. A caller whose ACL narrows the
candidates heavily (a class teacher who sees ~1-2 % of a school) is the hard case for an
approximate index: most graph neighbours are filtered out (other schools by RLS, other sections
by ``acl_predicate``) and the iterative scan may stop before it has found the true nearest
authorised chunks. These tests measure recall@10 of the PRODUCTION vector path (settings, route
and statement exactly as ``HybridRetriever.search`` runs them) against an exact oracle over the
same RLS session and the same predicate (``recall_support.exact_neighbours``), at ACL
selectivities of 100, 20, 5 and 1 % of the school.

CI corpus (seconds to build): one school of ``CHUNKS`` chunks and ``OTHER_SCHOOLS`` schools of
``OTHER_CHUNKS``. The heavier sweep (``-m recall_sweep`` with ``SOS_RECALL_SWEEP=1``) builds a
larger table and prints recall for iterative_scan x ef_search x max_scan_tuples, both routes;
its numbers are the table in docs/06 §6. The HNSW graph itself is not deterministic (pgvector
draws node levels from the backend's random state), so assertions keep a margin; the exact route
is deterministic (recall 1.0).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.knowledge.config.retrieval import load_retrieval_config
from app.knowledge.domain import SearchFilters
from app.knowledge.retrieval.hybrid import (
    HybridRetriever,
    authorised_count,
    vector_route,
    vector_statement,
)


def _load(name: str, file: str) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


R = _load("sos_test_kb_retrieval_support", "retrieval_support.py")
RS = _load("sos_test_kb_recall_support", "recall_support.py")
pytestmark = pytest.mark.db

CHUNKS = 4000
OTHER_SCHOOLS = 4
OTHER_CHUNKS = 500
QUERIES = 12
K = 10
SMALL_THRESHOLD = 100
REQUIRED_RECALL = 0.95
"""The soft gate for critical (narrow) callers in evals/gates.toml; asserted here per level."""


@pytest.fixture(scope="module")
def corpus(admin_engine: Engine, app_engine: Engine) -> Iterator[Any]:
    built = RS.build(
        admin_engine, R, chunks=CHUNKS, other_schools=OTHER_SCHOOLS, other_chunks=OTHER_CHUNKS
    )
    yield built
    R.delete_tenant_data(admin_engine, built.tenants)


@pytest.fixture(autouse=True)
def _fresh_statistics(admin_engine: Engine) -> None:
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text("ANALYZE kb.document_chunks"))


def _explain(tenant: Any, stmt: Any) -> list[dict[str, object]]:
    with tenant_session(tenant) as s:
        RS.apply_session_settings(s, load_retrieval_config())
        nodes: list[dict[str, object]] = R.plan_nodes(s.execute(R.Explain(stmt)).scalar_one())
        return nodes


def _uses_hnsw(nodes: list[dict[str, object]]) -> bool:
    return any(n.get("Index Name") == "document_chunks_embedding_hnsw" for n in nodes)


# --- the oracle and the corpus ------------------------------------------------------------------


def test_FR_KB_001_selectivity_levels_see_the_intended_share(corpus: Any) -> None:
    for selectivity in RS.SELECTIVITIES:
        with tenant_session(corpus.tenant) as s:
            n = authorised_count(s, RS.keys(selectivity), SearchFilters(), 100_000)
        assert n == round(CHUNKS * selectivity), (selectivity, n)


def test_FR_KB_002_oracle_is_exact_and_stays_inside_rls_and_the_acl(corpus: Any) -> None:
    acl = RS.keys(0.05)
    vector = RS.queries(1, 1)[0]
    with tenant_session(corpus.tenant) as s:
        exact = RS.exact_neighbours(s, acl, vector)
        # The oracle ranks every authorised row: the planner has nothing to approximate.
        stmt = RS.select(RS._C.id).where(RS.acl_predicate(acl, SearchFilters()))
        total: int = s.execute(text("SELECT count(*) FROM kb.document_chunks")).scalar_one()
    assert len(exact) == round(CHUNKS * 0.05)
    assert total == CHUNKS  # RLS: only this school's rows, never the other schools'
    assert [d for _, d in exact] == sorted(d for _, d in exact)
    assert not _uses_hnsw(_explain(corpus.tenant, stmt))


def test_FR_KB_002_recall_refuses_to_score_a_chunk_outside_the_acl(corpus: Any) -> None:
    vector = RS.queries(1, 1)[0]
    with tenant_session(corpus.tenant) as s:
        narrow = RS.exact_neighbours(s, RS.keys(0.01), vector)
        wide = RS.exact_neighbours(s, RS.keys(1.0), vector)
    outside = next(i for i, _ in wide if i not in dict(narrow))
    with pytest.raises(AssertionError, match="outside the ACL"):
        RS.recall_at_k([outside], narrow, K)


# --- the production path ------------------------------------------------------------------------


@pytest.mark.parametrize("selectivity", RS.SELECTIVITIES)
def test_FR_KB_001_authorised_recall_at_10_meets_the_gate(corpus: Any, selectivity: float) -> None:
    """Recall@10 of the production vector branch (routing included) at every selectivity."""
    config = load_retrieval_config()
    got = RS.measure(
        corpus,
        config,
        selectivity,
        RS.queries(corpus.seed, QUERIES),
        k=K,
    )
    assert got.authorised == round(CHUNKS * selectivity)
    assert got.recall >= REQUIRED_RECALL, (selectivity, got)


@pytest.mark.parametrize("selectivity", RS.SELECTIVITIES)
def test_FR_KB_001_ann_route_above_a_small_threshold_meets_the_gate(
    corpus: Any, selectivity: float
) -> None:
    """With the threshold at ``SMALL_THRESHOLD`` the wider callers go through HNSW (iterative
    scan, production ef_search and scan budget) and the narrowest one exactly: both meet the
    gate on the CI table."""
    config = RS.with_vector(load_retrieval_config(), exact_search_max_rows=SMALL_THRESHOLD)
    got = RS.measure(corpus, config, selectivity, RS.queries(corpus.seed, QUERIES), k=K)
    expected = "exact" if round(CHUNKS * selectivity) <= SMALL_THRESHOLD else "ann"
    assert set(got.routes) == {expected}
    assert got.recall >= REQUIRED_RECALL, (selectivity, got)


def test_FR_KB_007_routing_protects_narrow_callers_from_a_weak_index_setting(
    corpus: Any,
) -> None:
    """Routing does not depend on how well the graph walk goes: with iterative scan OFF (the
    setting that, forced through HNSW in the sweep of docs/06 §6, finds 7 % of a 1 % caller's
    nearest passages, and 88 % with a scan budget scaled to a ~0.8 M-row shared table) the 1 %
    caller is still ranked exactly and finds all of them. The negative half is measured by the
    sweep, not asserted here: whether one small CI graph walk fails depends on pgvector's random
    node levels, so it would be a flaky assertion."""
    base = RS.with_vector(load_retrieval_config(), iterative_scan="off")
    routed = RS.measure(corpus, base, 0.01, RS.queries(corpus.seed, QUERIES), k=K)
    assert routed.routes == {"exact": QUERIES}
    assert routed.recall == 1.0


def test_FR_KB_001_routing_uses_exact_search_below_the_threshold(corpus: Any) -> None:
    config = load_retrieval_config()
    cap = config.branches.vector.exact_search_max_rows
    assert cap > 0, "routing must be on in retrieval.yaml (docs/06 §6)"
    for selectivity in RS.SELECTIVITIES:
        expected = "exact" if round(CHUNKS * selectivity) <= cap else "ann"
        with tenant_session(corpus.tenant) as s:
            got = vector_route(s, config, RS.keys(selectivity), SearchFilters())
        assert got == expected, (selectivity, cap)


def test_FR_KB_001_routing_off_always_uses_the_index(corpus: Any) -> None:
    config = RS.with_vector(load_retrieval_config(), exact_search_max_rows=0)
    with tenant_session(corpus.tenant) as s:
        assert vector_route(s, config, RS.keys(0.01), SearchFilters()) == "ann"


def test_FR_KB_001_exact_route_is_the_oracle_and_never_uses_hnsw(corpus: Any) -> None:
    config = load_retrieval_config()
    acl = RS.keys(0.01)
    vector = RS.queries(corpus.seed, 1)[0]
    stmt = vector_statement(config, acl, SearchFilters(), vector, route="exact")
    assert not _uses_hnsw(_explain(corpus.tenant, stmt))
    with tenant_session(corpus.tenant) as s:
        exact = RS.exact_neighbours(s, acl, vector)
        _, got = RS.production_neighbours(s, config, acl, vector, route="exact")
    limit = config.branches.vector.limit
    assert got == [i for i, _ in exact[:limit]]


def test_FR_KB_002_search_under_exact_route_returns_only_authorised_chunks(
    corpus: Any,
) -> None:
    """The whole hybrid search, routed exact, returns only chunks the caller may see."""
    acl = RS.keys(0.01)
    vector = RS.queries(corpus.seed, 1)[0]
    query = R.RetrievalQuery(
        texts=("synthetic recall chunk",), vectors=(tuple(vector),), k=12, filters=SearchFilters()
    )
    with tenant_session(corpus.tenant) as s:
        allowed = {i for i, _ in RS.exact_neighbours(s, acl, vector)}
        results = HybridRetriever().search(s, acl, query)
    assert results
    assert {r.chunk_id for r in results} <= allowed


# --- heavier sweep (docs/06 §6 table) -------------------------------------------------------------

SWEEP = os.environ.get("SOS_RECALL_SWEEP") == "1"
SWEEP_CHUNKS = int(os.environ.get("SOS_RECALL_SWEEP_CHUNKS", "20000"))
SWEEP_OTHER_SCHOOLS = int(os.environ.get("SOS_RECALL_SWEEP_OTHER_SCHOOLS", "8"))
SWEEP_OTHER_CHUNKS = int(os.environ.get("SOS_RECALL_SWEEP_OTHER_CHUNKS", "2500"))
SWEEP_QUERIES = int(os.environ.get("SOS_RECALL_SWEEP_QUERIES", "20"))


@pytest.mark.recall_sweep
@pytest.mark.timeout(0)
@pytest.mark.skipif(not SWEEP, reason="heavy sweep: set SOS_RECALL_SWEEP=1 and -m recall_sweep")
def test_FR_KB_001_recall_sweep(admin_engine: Engine, app_engine: Engine) -> None:
    """Prints recall@10 and latency for each setting and selectivity (docs/06 §6)."""
    built = RS.build(
        admin_engine,
        R,
        chunks=SWEEP_CHUNKS,
        other_schools=SWEEP_OTHER_SCHOOLS,
        other_chunks=SWEEP_OTHER_CHUNKS,
    )
    try:
        base = load_retrieval_config()
        vectors = RS.queries(built.seed, SWEEP_QUERIES)
        rows = []
        # max_scan_tuples below the production 20 000 stands in for a larger shared graph:
        # the scan budget per authorised chunk is what decides whether a narrow list fills.
        settings = [
            ("off", 64, 20000),
            ("relaxed_order", 64, 20000),
            ("relaxed_order", 64, 5000),
            ("relaxed_order", 64, 1000),
            ("relaxed_order", 128, 20000),
            ("strict_order", 64, 20000),
            ("strict_order", 64, 1000),
        ]
        for scan, ef, tuples in settings:
            config = RS.with_vector(
                base, iterative_scan=scan, hnsw_ef_search=ef, max_scan_tuples=tuples
            )
            for selectivity in RS.SELECTIVITIES:
                for forced in (False, True):
                    m = RS.measure(
                        built, config, selectivity, vectors, k=K, route="ann", force_hnsw=forced
                    )
                    route = "ann/hnsw" if forced else "ann/planner"
                    rows.append(
                        (route, scan, ef, tuples, selectivity, m.authorised, m.recall, m.path_ms)
                    )
        for selectivity in RS.SELECTIVITIES:
            m = RS.measure(built, base, selectivity, vectors, k=K, route="exact")
            rows.append(("exact", "-", 0, 0, selectivity, m.authorised, m.recall, m.path_ms))
        sys.stdout.write(
            f"\nrecall sweep: school {SWEEP_CHUNKS} chunks, {SWEEP_OTHER_SCHOOLS} x "
            f"{SWEEP_OTHER_CHUNKS} other chunks, {len(vectors)} queries, k={K}\n"
            "route | iterative_scan | ef_search | max_scan_tuples | selectivity | authorised "
            "| recall@10 | ms/query\n"
        )
        for row in rows:
            sys.stdout.write(
                f"{row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]:.0%} | {row[5]} | "
                f"{row[6]:.3f} | {row[7]:.1f}\n"
            )
    finally:
        R.delete_tenant_data(admin_engine, built.tenants)
