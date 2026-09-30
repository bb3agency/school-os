"""Hybrid retrieval over ``kb.document_chunks`` (docs/06 §6; FR-KB-001, FR-KB-002, SEC-018).

Per query text (the original first, then translations when ``translated_query_fusion`` is on)
three candidate lists run in ONE SQL statement (``UNION ALL``), each with
:func:`.acl.acl_predicate` in its own WHERE clause, before its own ORDER BY/LIMIT:

- ``vector``: ``ORDER BY embedding <=> :qvec LIMIT n`` (HNSW; ``hnsw.ef_search`` and pgvector
  iterative scans set per transaction from config);
- ``full_text``: ``content_tsv @@ :tsquery`` ranked by ``ts_rank_cd`` (``simple`` config; the
  tsquery is built once per text by PostgreSQL, never by string concatenation in Python);
- ``trigram``: ``:q <% context_header`` ranked by ``word_similarity`` (typo- and
  transliteration-tolerant match on title, issuer, reference number, subject and section).

Candidates are fused with Reciprocal Rank Fusion, boosted, diversified and trimmed in Python
(:mod:`.fusion`), then the detail query (again under the ACL predicate) loads content, title and
version number. Adjacent chunks from the same page are merged. Tenant isolation is RLS.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from opentelemetry import trace
from sqlalchemy import (
    ARRAY,
    Float,
    Select,
    Text,
    Uuid,
    bindparam,
    cast,
    func,
    literal,
    select,
    text,
    union_all,
)
from sqlalchemy.dialects.postgresql import REGCONFIG, TSQUERY, TSVECTOR
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.redaction import mask_aadhaar
from app.knowledge import sources
from app.knowledge.config.retrieval import RetrievalConfig, load_retrieval_config
from app.knowledge.domain import AclKeys, RankedChunk, RetrievalQuery, SearchFilters
from app.knowledge.interfaces import Reranker
from app.knowledge.models import EMBEDDING_DIMENSIONS, DocumentChunk, HalfVector
from app.knowledge.retrieval.acl import acl_predicate
from app.knowledge.retrieval.fusion import (
    Candidate,
    Scored,
    adjacent_groups,
    apply_boosts,
    apply_rerank,
    diversify,
    reciprocal_rank_fusion,
)

BranchName = Literal["vector", "full_text", "trigram"]
BRANCHES: Final[tuple[BranchName, ...]] = ("vector", "full_text", "trigram")
MAX_K: Final = 100

log = get_logger(__name__)
tracer = trace.get_tracer("app.knowledge.retrieval")

_C = DocumentChunk
_CANDIDATE_COLUMNS = (
    _C.id,
    _C.document_id,
    _C.version_id,
    _C.chunk_no,
    _C.page_from,
    _C.page_to,
    _C.doc_type,
    _C.issued_on,
)

# kb.documents / kb.document_versions belong to app.documents; the detail query only reads the
# title and version number of chunks that already passed the ACL predicate (RLS applies).
_DETAILS_SQL: Final = text(
    "SELECT v.id AS version_id, v.version_no, d.title "
    "FROM kb.document_versions v JOIN kb.documents d "
    "  ON d.tenant_id = v.tenant_id AND d.id = v.document_id "
    "WHERE v.id = ANY(:version_ids)"
).bindparams(bindparam("version_ids", type_=ARRAY(Uuid())))

_SETTINGS_SQL: Final = text(
    "SELECT set_config('hnsw.ef_search', :ef_search, true), "
    "set_config('hnsw.iterative_scan', :iterative_scan, true), "
    "set_config('hnsw.max_scan_tuples', :max_scan_tuples, true), "
    "set_config('pg_trgm.word_similarity_threshold', :word_similarity_threshold, true)"
)


@dataclass(frozen=True, slots=True)
class QueryText:
    """One query text with its embedding and the tsquery PostgreSQL built from it."""

    text: str
    vector: tuple[float, ...]
    tsquery: str


def _tsquery_sql(config: RetrievalConfig) -> Select[tuple[str]]:
    fts = config.branches.full_text
    ts_config = cast(bindparam("ts_config", fts.ts_config), REGCONFIG)
    q = bindparam("q", type_=Text)
    if fts.match == "websearch":
        return select(cast(func.websearch_to_tsquery(ts_config, q), Text))
    # OR of the question's lexemes (plainto_tsquery joins them with AND; phrases stay intact).
    return select(func.replace(cast(func.plainto_tsquery(ts_config, q), Text), " & ", " | "))


def branch_statements(
    config: RetrievalConfig,
    acl: AclKeys,
    filters: SearchFilters,
    query: QueryText,
    index: int = 0,
) -> dict[BranchName, Select[Any]]:
    """The three candidate lists for one query text: ``(id, r)`` rows plus candidate columns.

    Each carries ``acl_predicate`` in its own WHERE clause (docs/06 §6: a shared CTE would be
    materialised and stop the indexes from being used). Exposed for the EXPLAIN tests.
    """
    b = config.branches

    qvec = bindparam(f"qvec_{index}", list(query.vector), type_=HalfVector(EMBEDDING_DIMENSIONS))
    distance = _C.embedding.op("<=>", return_type=Float)(qvec)
    vec_inner = (
        select(*_CANDIDATE_COLUMNS, distance.label("score"))
        .where(acl_predicate(acl, filters))
        .order_by(distance)
        .limit(b.vector.limit)
        .subquery(f"vector_{index}")
    )
    vector = select(
        *(vec_inner.c[c.key] for c in _CANDIDATE_COLUMNS),
        func.row_number().over(order_by=(vec_inner.c.score, vec_inner.c.id)).label("r"),
    )

    tsq = cast(bindparam(f"tsquery_{index}", query.tsquery, type_=Text), TSQUERY)
    # Contextual retrieval (docs/06 §4.11): the chunk's context is searched with its text.
    tsv: Any = _C.content_tsv
    if config.contextual:
        tsv = _C.content_tsv.op("||", return_type=TSVECTOR)(_C.context_tsv)
    ts_rank = func.ts_rank_cd(tsv, tsq)
    fts_inner = (
        select(*_CANDIDATE_COLUMNS, ts_rank.label("score"))
        .where(acl_predicate(acl, filters), tsv.op("@@")(tsq))
        .order_by(ts_rank.desc(), _C.id)
        .limit(b.full_text.limit)
        .subquery(f"full_text_{index}")
    )
    full_text = select(
        *(fts_inner.c[c.key] for c in _CANDIDATE_COLUMNS),
        func.row_number().over(order_by=(fts_inner.c.score.desc(), fts_inner.c.id)).label("r"),
    )

    qtext = bindparam(f"qtext_{index}", query.text, type_=Text)
    keyword: Any = _C.context_header
    if config.contextual:
        keyword = func.concat_ws(" ", _C.context_header, _C.chunk_context)
    similarity = func.word_similarity(qtext, keyword)
    trg_inner = (
        select(*_CANDIDATE_COLUMNS, similarity.label("score"))
        .where(acl_predicate(acl, filters), qtext.op("<%")(keyword))
        .order_by(similarity.desc(), _C.id)
        .limit(b.trigram.limit)
        .subquery(f"trigram_{index}")
    )
    trigram = select(
        *(trg_inner.c[c.key] for c in _CANDIDATE_COLUMNS),
        func.row_number().over(order_by=(trg_inner.c.score.desc(), trg_inner.c.id)).label("r"),
    )
    return {"vector": vector, "full_text": full_text, "trigram": trigram}


def apply_session_settings(session: Session, config: RetrievalConfig) -> None:
    """Transaction-local planner settings (``set_config(..., true)`` = ``SET LOCAL``)."""
    v = config.branches.vector
    session.execute(
        _SETTINGS_SQL,
        {
            "ef_search": str(config.hnsw_ef_search),
            "iterative_scan": v.iterative_scan,
            "max_scan_tuples": str(v.max_scan_tuples),
            "word_similarity_threshold": str(config.branches.trigram.word_similarity_threshold),
        },
    )


class HybridRetriever:
    """:class:`app.knowledge.interfaces.Retriever` over ``kb.document_chunks``.

    ``reranker`` (optional, docs/06 §6): used only while ``rerank.provider`` is not ``off``;
    it receives the question and the text of the best ``rerank.candidates`` fused candidates,
    read again under the caller's ACL predicate and Aadhaar-masked again (invariants 4, 8)."""

    def __init__(
        self,
        config: RetrievalConfig | None = None,
        *,
        reranker: Reranker | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config or load_retrieval_config()
        self._reranker = reranker if self._config.rerank.enabled else None
        self._clock = clock

    @property
    def config(self) -> RetrievalConfig:
        return self._config

    @property
    def reranker(self) -> Reranker | None:
        return self._reranker

    def query_texts(self, session: Session, query: RetrievalQuery) -> list[QueryText]:
        if not query.texts or len(query.texts) != len(query.vectors):
            raise ValueError("RetrievalQuery needs one vector per text and at least one text")
        count = len(query.texts) if self._config.translated_query_fusion.enabled else 1
        tsquery_sql = _tsquery_sql(self._config)
        out = []
        for text_, vector in zip(query.texts[:count], query.vectors[:count], strict=True):
            if len(vector) != EMBEDDING_DIMENSIONS:
                raise ValueError(f"query vectors must have {EMBEDDING_DIMENSIONS} dimensions")
            tsquery = session.execute(tsquery_sql, {"q": text_}).scalar_one()
            out.append(QueryText(text=text_, vector=tuple(vector), tsquery=str(tsquery)))
        return out

    def candidates(
        self, session: Session, acl: AclKeys, filters: SearchFilters, texts: Sequence[QueryText]
    ) -> list[Candidate]:
        """Every branch list for every text, in one statement (all ACL-filtered in SQL)."""
        parts = []
        list_no = 0
        for index, qt in enumerate(texts):
            for name, stmt in branch_statements(self._config, acl, filters, qt, index).items():
                sub = stmt.subquery(f"{name}_{index}_ranked")
                parts.append(select(literal(list_no).label("list_no"), *sub.c))
                list_no += 1
        rows = session.execute(union_all(*parts)).all()
        return [
            Candidate(
                list_no=r.list_no,
                rank=r.r,
                chunk_id=r.id,
                document_id=r.document_id,
                version_id=r.version_id,
                chunk_no=r.chunk_no,
                page_from=r.page_from,
                page_to=r.page_to,
                doc_type=r.doc_type,
                issued_on=r.issued_on,
            )
            for r in rows
        ]

    def search(self, session: Session, acl: AclKeys, query: RetrievalQuery) -> list[RankedChunk]:
        if query.k < 1:
            raise ValueError("k must be at least 1")
        k = min(query.k, MAX_K)
        config = self._config
        apply_session_settings(session, config)
        texts = self.query_texts(session, query)
        fused = reciprocal_rank_fusion(
            self.candidates(session, acl, query.filters, texts), config.fusion.k
        )
        boosted = apply_boosts(fused, config.boosts, prefer_latest=query.prefer_latest)
        if self._reranker is not None and boosted:
            reranked = self._rerank(session, acl, query, boosted)
            if reranked is not None:
                boosted, k = reranked, min(k, config.rerank.keep)
        chosen = diversify(boosted, config.diversity, k)
        return self._materialise(session, acl, query.filters, chosen)

    def _passages(
        self, session: Session, acl: AclKeys, filters: SearchFilters, head: Sequence[Scored]
    ) -> dict[uuid.UUID, str]:
        """The reranker's input for ``head``: read again UNDER the ACL predicate (a candidate
        whose visibility changed meanwhile is dropped), Aadhaar-masked again, capped."""
        rerank = self._config.rerank
        rows = session.execute(
            select(_C.id, _C.context_header, _C.chunk_context, _C.content).where(
                _C.id.in_([c.chunk_id for c in head]), acl_predicate(acl, filters)
            )
        ).all()
        out = {}
        for row in rows:
            parts = [row.context_header, row.chunk_context] if rerank.include_context else []
            head_text = "\n".join(p for p in parts if p)
            text_ = f"{head_text}\n\n{row.content}" if head_text else row.content
            out[row.id] = mask_aadhaar(text_)[: rerank.max_passage_chars]
        return out

    def _rerank(
        self, session: Session, acl: AclKeys, query: RetrievalQuery, boosted: Sequence[Scored]
    ) -> list[Scored] | None:
        """Rerank the best ``rerank.candidates``; None (keep the fused order) on any failure or
        a call slower than ``rerank.latency_budget_ms`` (docs/06 §15: never block an answer)."""
        reranker = self._reranker
        if reranker is None:
            return None
        rerank = self._config.rerank
        texts = self._passages(session, acl, query.filters, boosted[: rerank.candidates])
        head = [c for c in boosted[: rerank.candidates] if c.chunk_id in texts]
        tail = [c for c in boosted[: rerank.candidates] if c.chunk_id not in texts]
        tail += boosted[rerank.candidates :]
        budget_s = rerank.latency_budget_ms / 1000
        started = self._clock()
        outcome, error_type = "ok", None
        scores: list[float] = []
        with tracer.start_as_current_span("retrieval.rerank") as span:
            try:
                scores = reranker.rerank(
                    mask_aadhaar(query.texts[0]),
                    [texts[c.chunk_id] for c in head],
                    timeout_s=budget_s,
                )
                if len(scores) != len(head) or not all(math.isfinite(s) for s in scores):
                    outcome, error_type = "invalid", "InvalidScores"
            except Exception as exc:  # any provider failure: keep the fused order
                outcome, error_type = "failed", type(exc).__name__
            elapsed_ms = max(0, int((self._clock() - started) * 1000))
            if outcome == "ok" and elapsed_ms > rerank.latency_budget_ms:
                outcome = "over_budget"
            span.set_attributes(
                {
                    "retrieval.rerank.provider": reranker.name,
                    "retrieval.rerank.model": reranker.model,
                    "retrieval.rerank.candidates": len(head),
                    "retrieval.rerank.latency_ms": elapsed_ms,
                    "retrieval.rerank.outcome": outcome,
                }
            )
        fields: dict[str, object] = {
            "action": reranker.name,
            "outcome": outcome,
            "count": len(head),
            "duration_ms": elapsed_ms,
        }
        if outcome != "ok":
            log.warning("knowledge.rerank.fallback", error_type=error_type, **fields)
            return None
        log.info("knowledge.rerank", **fields)
        return apply_rerank([*head, *tail], scores, self._config.fusion.k)

    def _materialise(
        self, session: Session, acl: AclKeys, filters: SearchFilters, chosen: Sequence[Scored]
    ) -> list[RankedChunk]:
        if not chosen:
            return []
        ids = [c.chunk_id for c in chosen]
        # Re-applies the ACL predicate: content is only ever read under the caller's filter.
        rows = session.execute(
            select(_C.id, _C.content).where(_C.id.in_(ids), acl_predicate(acl, filters))
        ).all()
        content: dict[uuid.UUID, str] = dict(rows)
        versions = {c.version_id for c in chosen if c.chunk_id in content}
        details = {
            r.version_id: (r.version_no, r.title)
            for r in session.execute(_DETAILS_SQL, {"version_ids": sorted(versions, key=str)})
        }
        visible = [c for c in chosen if c.chunk_id in content and c.version_id in details]
        if self._config.diversity.merge_adjacent_same_page:
            groups = adjacent_groups(visible)
        else:
            groups = [[c] for c in visible]
        out = []
        for group in groups:
            best = min(group, key=lambda m: (-m.score, str(m.chunk_id)))
            version_no, title = details[best.version_id]
            pages_from = [m.page_from for m in group if m.page_from is not None]
            pages_to = [m.page_to for m in group if m.page_to is not None]
            page_from = min(pages_from) if pages_from else None
            page_to = max(pages_to) if pages_to else None
            out.append(
                RankedChunk(
                    chunk_id=best.chunk_id,
                    document_id=best.document_id,
                    version_id=best.version_id,
                    version_no=version_no,
                    page_from=page_from,
                    page_to=page_to,
                    doc_type=best.doc_type,
                    title=title,
                    issued_on=best.issued_on,
                    content="\n".join(content[m.chunk_id] for m in group),
                    score=best.score,
                    source=sources.document_page(
                        best.document_id, version_no=version_no, page=page_from or 1
                    ),
                )
            )
        return out
