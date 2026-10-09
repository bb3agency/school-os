"""Authorised-retrieval recall: a synthetic shared-tier corpus and an exact oracle (docs/06 §6).

Used by ``test_retrieval_recall.py`` and by the eval bridge (``eval_bridge.py``, the
``authorised_recall`` pass of ``sos_evals``). Synthetic only (invariant 11).

Corpus: one school holds ``chunks`` chunks, ``other_schools`` more schools hold
``other_chunks`` each, all in the one shared ``kb.document_chunks`` table and its one HNSW graph
(the shared tier). Embeddings are clustered like real ones (topic + sub-topic centre + noise,
``CLUSTERS`` clusters), made by PostgreSQL from centres the test computes with a seeded
generator (``setseed`` for the noise), so the data is the same on every run. Every chunk of the
school carries ONE of ``BUCKETS`` synthetic section ids in ``acl_sections``, spread evenly and
independently of its cluster; a caller holding the first ``n`` section ids sees ``n`` % of the
school (``keys(selectivity)``), like a class teacher who sees their own sections only.

Oracle (:func:`exact_neighbours`): every chunk that passes :func:`acl_predicate` for the caller,
read as ``sos_app`` in a ``tenant_session`` (RLS applies, never bypassed), with its exact cosine
distance and NO ``ORDER BY``/``LIMIT`` (so no index can rank it); sorted in Python by
``(distance, id)``. The production path (:func:`production_neighbours`) is exactly what
``HybridRetriever.search`` runs for the vector branch: the session settings, the route and
``vector_statement``.

Recall@k (:func:`recall_at_k`) is tie-tolerant: a returned chunk is a hit when its exact distance
is within ``TIE_EPSILON`` of the k-th exact distance (halfvec distances tie often enough that
comparing ids alone would count an equally near chunk as a miss).
"""

from __future__ import annotations

import math
import random
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Engine, Float, bindparam, select, text
from sqlalchemy.orm import Session

from app.core.db import tenant_session
from app.knowledge.config.retrieval import RetrievalConfig
from app.knowledge.domain import AclKeys, SearchFilters
from app.knowledge.models import EMBEDDING_DIMENSIONS, DocumentChunk, HalfVector
from app.knowledge.retrieval.acl import acl_predicate
from app.knowledge.retrieval.hybrid import (
    VectorRoute,
    apply_session_settings,
    vector_route,
    vector_statement,
)

DIM: Final = EMBEDDING_DIMENSIONS
BUCKETS: Final = 100
TOPICS: Final = 16
SUBTOPICS: Final = 8
CLUSTERS: Final = TOPICS * SUBTOPICS
SUB_WEIGHT: Final = 0.5
NOISE: Final = 0.06
"""Uniform noise of +-NOISE/2 per dimension: a noise norm of about 0.55 against unit topics."""
TIE_EPSILON: Final = 1e-4
SELECTIVITIES: Final[tuple[float, ...]] = (1.0, 0.2, 0.05, 0.01)
NAMESPACE: Final = uuid.UUID("6f1d3c2e-8a55-4f0e-9d7b-5b1c0e2a9f31")

_C = DocumentChunk


def _unit(rng: random.Random) -> list[float]:
    vec = [rng.gauss(0.0, 1.0) for _ in range(DIM)]
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec]


def centres(seed: int) -> list[list[float]]:
    """``CLUSTERS`` centres: a topic direction plus a weighted sub-topic direction."""
    rng = random.Random(f"recall-centres:{seed}")  # synthetic data, not security relevant
    topics = [_unit(rng) for _ in range(TOPICS)]
    out = []
    for t in range(TOPICS):
        for _ in range(SUBTOPICS):
            sub = _unit(rng)
            out.append([a + SUB_WEIGHT * b for a, b in zip(topics[t], sub, strict=True)])
    return out


def section(bucket: int) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"recall-bucket-{bucket}")


def keys(selectivity: float) -> AclKeys:
    """A caller who sees ``selectivity`` of the school (the first n buckets' section ids)."""
    n = max(1, round(selectivity * BUCKETS))
    return AclKeys(
        roles=frozenset(),
        section_ids=frozenset(section(b) for b in range(n)),
        class_ids=frozenset(),
        membership_id=uuid.uuid5(NAMESPACE, "recall-member"),
        school_wide=False,
    )


def queries(seed: int, count: int) -> list[list[float]]:
    """Query vectors near cluster centres (seeded; the same list on every run)."""
    rng = random.Random(f"recall-queries:{seed}")  # synthetic data, not security relevant
    cs = centres(seed)
    out = []
    for i in range(count):
        centre = cs[(i * 37 + 11) % CLUSTERS]
        out.append([c + NOISE * (rng.random() - 0.5) for c in centre])
    return out


@dataclass(frozen=True)
class RecallCorpus:
    tenant: uuid.UUID
    others: tuple[uuid.UUID, ...]
    chunks: int
    seed: int

    @property
    def tenants(self) -> tuple[uuid.UUID, ...]:
        return (self.tenant, *self.others)


_GENERATE: Final = text(
    """
    INSERT INTO kb.document_chunks (id, tenant_id, document_id, version_id, chunk_no, page_from,
      page_to, context_header, content, token_count, embedding, embedding_model, doc_type,
      sensitivity, acl_sections, is_latest)
    SELECT md5(CAST(:t AS text) || '/' || g)::uuid, :t, :d, :v, g, g + 1, g + 1,
      '[Circular] Synthetic recall corpus - page ' || g,
      'synthetic recall chunk ' || g,
      30,
      CAST(CAST(ARRAY(SELECT c.v[i] + :noise * (random() - 0.5)
                      FROM generate_series(1, 1024) AS i WHERE g >= 0 ORDER BY i)
                AS vector(1024)) AS halfvec(1024)),
      'synthetic-embed-1', 'circular', 'C1',
      ARRAY[(:sections)[1 + (g * 7919) % 100]], true
    FROM generate_series(0, :n - 1) AS g
    JOIN recall_centres c ON c.cluster = g % :clusters
    """
)


def _school(
    admin: Engine, support: object, chunks: int, seed: int, sections: Sequence[uuid.UUID]
) -> uuid.UUID:
    make_tenant = getattr(support, "make_tenant")  # noqa: B009 (module loaded by path)
    make_document = getattr(support, "make_document")  # noqa: B009
    add_version = getattr(support, "add_version")  # noqa: B009
    tenant: uuid.UUID = make_tenant(admin)
    doc = make_document(admin, tenant, title="Synthetic recall corpus")
    version = add_version(admin, doc)
    with admin.begin() as c:
        c.execute(text("CREATE TEMP TABLE recall_centres (cluster int PRIMARY KEY, v float8[])"))
        c.execute(
            text("INSERT INTO recall_centres (cluster, v) VALUES (:c, :v)"),
            [{"c": i, "v": v} for i, v in enumerate(centres(seed))],
        )
        c.execute(text("SELECT setseed(:s)"), {"s": (seed % 1000) / 1000})
        c.execute(
            _GENERATE,
            {
                "t": tenant,
                "d": doc.document_id,
                "v": version,
                "n": chunks,
                "noise": NOISE,
                "sections": list(sections),
                "clusters": CLUSTERS,
            },
        )
        c.execute(text("DROP TABLE recall_centres"))
    return tenant


def build(
    admin: Engine,
    support: object,
    *,
    chunks: int,
    other_schools: int,
    other_chunks: int,
    seed: int = 7,
) -> RecallCorpus:
    """The corpus (admin engine: test set-up only), then ``ANALYZE`` like autovacuum would."""
    school_sections = [section(b) for b in range(BUCKETS)]
    tenant = _school(admin, support, chunks, seed, school_sections)
    others = []
    for i in range(other_schools):
        own = [uuid.uuid5(NAMESPACE, f"other-{i}-{b}") for b in range(BUCKETS)]
        others.append(_school(admin, support, other_chunks, seed + 1 + i, own))
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text("ANALYZE kb.document_chunks"))
    return RecallCorpus(tenant, tuple(others), chunks, seed)


# --- oracle and production path ----------------------------------------------------------------

Neighbour = tuple[uuid.UUID, float]


def exact_neighbours(session: Session, acl: AclKeys, vector: Sequence[float]) -> list[Neighbour]:
    """EVERY authorised chunk with its exact cosine distance, nearest first (the oracle).

    Same RLS session and the same ``acl_predicate`` as production; no ORDER BY and no LIMIT,
    so the HNSW index cannot take part (it only serves ``ORDER BY <=> LIMIT``)."""
    qvec = bindparam("oracle_q", list(vector), type_=HalfVector(DIM))
    distance = _C.embedding.op("<=>", return_type=Float)(qvec)
    rows = session.execute(select(_C.id, distance).where(acl_predicate(acl, SearchFilters()))).all()
    return sorted(((r[0], float(r[1])) for r in rows), key=lambda n: (n[1], str(n[0])))


_FORCE_HNSW: Final = text(
    "SELECT set_config('enable_seqscan', 'off', true), "
    "set_config('enable_bitmapscan', 'off', true), "
    "set_config('enable_sort', 'off', true)"
)


def production_neighbours(
    session: Session,
    config: RetrievalConfig,
    acl: AclKeys,
    vector: Sequence[float],
    *,
    route: VectorRoute | None = None,
    force_hnsw: bool = False,
) -> tuple[VectorRoute, list[uuid.UUID]]:
    """The vector branch exactly as ``HybridRetriever.search`` runs it (``route`` None = the
    production routing decision), best first. ``force_hnsw`` (measurement only) disables the
    planner's alternatives so an ``ann`` route really walks the HNSW graph: the planner may
    otherwise rank a narrow filter exactly through the tenant btree, which hides the index's
    recall; production cannot rely on that estimate."""
    if force_hnsw:
        session.execute(_FORCE_HNSW)
    apply_session_settings(session, config)
    chosen = route or vector_route(session, config, acl, SearchFilters())
    stmt = vector_statement(config, acl, SearchFilters(), vector, route=chosen)
    rows = session.execute(stmt).all()
    return chosen, [r.id for r in sorted(rows, key=lambda r: r.r)]


def recall_at_k(returned: Sequence[uuid.UUID], exact: Sequence[Neighbour], k: int) -> float:
    """Tie-tolerant recall@k of ``returned`` against the oracle list (1.0 when nothing is
    authorised and nothing is returned). A returned id outside ``exact`` raises: it would be a
    chunk the caller may not see (the test must fail loudly, never score it)."""
    distances = dict(exact)
    leaked = [i for i in returned if i not in distances]
    if leaked:
        raise AssertionError(f"retrieval returned {len(leaked)} chunk(s) outside the ACL")
    want = min(k, len(exact))
    if want == 0:
        return 1.0
    kth = exact[want - 1][1]
    hits = sum(1 for i in returned[:k] if distances[i] <= kth + TIE_EPSILON)
    return min(hits, want) / want


def measure(
    corpus: RecallCorpus,
    config: RetrievalConfig,
    selectivity: float,
    vectors: Sequence[Sequence[float]],
    *,
    k: int = 10,
    route: VectorRoute | None = None,
    force_hnsw: bool = False,
) -> Measurement:
    """Mean recall@k over ``vectors``, the authorised count, how often each route ran and the
    mean time of the production path (routing count included, oracle excluded)."""
    acl = keys(selectivity)
    scores = []
    routes: dict[str, int] = {}
    authorised = 0
    elapsed = 0.0
    for vector in vectors:
        with tenant_session(corpus.tenant) as s:
            exact = exact_neighbours(s, acl, vector)
        with tenant_session(corpus.tenant) as s:
            started = time.perf_counter()
            chosen, returned = production_neighbours(
                s, config, acl, vector, route=route, force_hnsw=force_hnsw
            )
            elapsed += time.perf_counter() - started
        authorised = len(exact)
        routes[chosen] = routes.get(chosen, 0) + 1
        scores.append(recall_at_k(returned, exact, k))
    return Measurement(
        recall=sum(scores) / len(scores),
        authorised=authorised,
        routes=routes,
        path_ms=elapsed * 1000 / len(vectors),
    )


@dataclass(frozen=True)
class Measurement:
    recall: float
    authorised: int
    routes: dict[str, int]
    path_ms: float


def with_vector(config: RetrievalConfig, **update: object) -> RetrievalConfig:
    """``config`` with vector-branch (and ``hnsw_ef_search``) settings replaced."""
    ef = update.pop("hnsw_ef_search", None)
    vector = config.branches.vector.model_copy(update=update)
    out = config.model_copy(
        update={"branches": config.branches.model_copy(update={"vector": vector})}
    )
    if ef is not None:
        out = out.model_copy(update={"hnsw_ef_search": ef})
    return out
