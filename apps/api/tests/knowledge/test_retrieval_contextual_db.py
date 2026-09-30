"""Contextual retrieval and reranking in SQL retrieval (docs/06 §4.11, §6; FR-KB-001, FR-KB-002,
SEC-018; invariants 4 and 8).

Through ``sos_app`` in a ``tenant_session`` (RLS applies). Contexts are searched by the full-text
and keyword branches only while ``contextual_chunks`` is on, and never returned as content.
The reranker sees ONLY the question and candidates that passed the caller's ACL predicate in SQL
(a forbidden passage that matches best is never sent), Aadhaar-masked again and capped; its
order is used, at most ``keep`` passages come back, and any failure or a call over the latency
budget keeps the fused order. The SQL chunk store writes and reads contexts.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.redaction import contains_full_aadhaar, verhoeff_check_digit
from app.knowledge import repository as repo
from app.knowledge.config.retrieval import RetrievalConfig, load_retrieval_config
from app.knowledge.domain import Chunk
from app.knowledge.ingestion.ports import (
    ChunkAcl,
    ChunkContext,
    ChunkFilters,
    IndexedChunk,
    VersionIndex,
)
from app.knowledge.rerank import FakeReranker
from app.knowledge.retrieval import HybridRetriever
from app.knowledge.retrieval.hybrid import branch_statements
from app.knowledge.store import SqlChunkStore


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

_BODY = "84736291055"
AADHAAR = _BODY + verhoeff_check_digit(_BODY)  # synthetic, Verhoeff-valid, issued to nobody
TEACHER = R.keys(roles=["teacher"])
FORBIDDEN = "Science exhibition payment of Rs. 900 for the principal's restricted memo."


def config(
    *, contextual: bool = False, rerank: str = "off", **rerank_changes: Any
) -> RetrievalConfig:
    base = load_retrieval_config().with_overrides(
        contextual_chunks="on" if contextual else "off",
        rerank=rerank,  # type: ignore[arg-type]
    )
    if rerank_changes:
        base = base.model_copy(update={"rerank": base.rerank.model_copy(update=rerank_changes)})
    return base


class RecordingReranker:
    """Wraps a scorer; keeps every query and passage it was sent."""

    name = "recording"
    model = "synthetic-rerank-1"

    def __init__(self, scorer: Any = None, *, fail: bool = False) -> None:
        self.scorer = scorer or FakeReranker()
        self.fail = fail
        self.calls: list[tuple[str, list[str], float]] = []

    def rerank(self, query: str, passages: Sequence[str], *, timeout_s: float) -> list[float]:
        self.calls.append((query, list(passages), timeout_s))
        if self.fail:
            raise TimeoutError("synthetic provider timeout")
        scores: list[float] = self.scorer.rerank(query, passages, timeout_s=timeout_s)
        return scores

    @property
    def sent(self) -> list[str]:
        return [p for _, passages, _ in self.calls for p in passages]


class Reverse:
    """Scores the passages in reverse of the order they were given."""

    def rerank(self, query: str, passages: Sequence[str], *, timeout_s: float) -> list[float]:
        return [float(i) for i in range(len(passages))]


@pytest.fixture
def school(admin_engine: Engine) -> Iterator[dict[str, Any]]:
    tid = R.make_tenant(admin_engine)
    payment = R.make_document(admin_engine, tid, title="Circular No. 14/2026-27")
    R.add_version(admin_engine, payment)
    R.add_chunks(
        admin_engine,
        payment,
        [
            "Sub: Science exhibition 2026 for classes VI to X.",
            "Each student pays Rs. 150 at the office counter by the said date.",
        ],
        topic="circular-14",
        header="[Circular] Circular No. 14/2026-27",
        acl_roles=["teacher"],
        contexts=["", "Science exhibition 2026 circular, payment part."],
    )
    sports = R.make_document(admin_engine, tid, title="Circular No. 15/2026-27")
    R.add_version(admin_engine, sports)
    R.add_chunks(
        admin_engine,
        sports,
        [
            "Sports day payment: each student pays Rs. 50 for the kit.",
            f"Kit helpdesk number {AADHAAR[:4]} {AADHAAR[4:8]} {AADHAAR[8:]} is not a real one.",
        ],
        topic="circular-15",
        header="[Circular] Circular No. 15/2026-27",
        acl_roles=["teacher"],
    )
    secret = R.make_document(admin_engine, tid, title="Restricted memo")
    R.add_version(admin_engine, secret)
    R.add_chunks(
        admin_engine,
        secret,
        [FORBIDDEN],
        topic="circular-14",
        header="[Circular] Restricted memo",
        acl_roles=["principal"],
        contexts=["Science exhibition payment memo for the principal."],
    )
    yield {"tenant": tid, "payment": payment, "sports": sports, "secret": secret}
    R.delete_tenant_data(admin_engine, [tid])


def search(tid: uuid.UUID, retriever: HybridRetriever, question: str, **kw: Any) -> list[Any]:
    with tenant_session(tid) as s:
        found: list[Any] = retriever.search(s, TEACHER, R.query(question, "unrelated", **kw))
        return found


def branch_ids(tid: uuid.UUID, cfg: RetrievalConfig, question: str, branch: str) -> set[uuid.UUID]:
    retriever = HybridRetriever(cfg)
    with tenant_session(tid) as s:
        (qt,) = retriever.query_texts(s, R.query(question, "unrelated"))
        stmt = branch_statements(cfg, TEACHER, R.SearchFilters(), qt)[branch]  # type: ignore[index]
        return {r.id for r in s.execute(stmt)}


# --- contextual branches -------------------------------------------------------------------------


def test_FR_KB_001_full_text_finds_a_chunk_by_its_context_only_when_on(
    school: dict[str, Any], app_engine: Engine
) -> None:
    tid, payment = school["tenant"], school["payment"]
    target = payment.latest_chunks[1]  # its text never says "exhibition"
    assert target not in branch_ids(tid, config(), "exhibition", "full_text")
    assert target in branch_ids(tid, config(contextual=True), "exhibition", "full_text")


def test_FR_KB_001_keyword_branch_matches_the_context_only_when_on(
    school: dict[str, Any], app_engine: Engine
) -> None:
    tid, payment = school["tenant"], school["payment"]
    target = payment.latest_chunks[1]
    assert target not in branch_ids(tid, config(), "science exhibiton", "trigram")
    assert target in branch_ids(tid, config(contextual=True), "science exhibiton", "trigram")


def test_invariant_8_contexts_never_widen_visibility_nor_replace_content(
    school: dict[str, Any], app_engine: Engine
) -> None:
    tid = school["tenant"]
    found = search(tid, HybridRetriever(config(contextual=True)), "science exhibition payment")
    assert found
    assert school["secret"].document_id not in {c.document_id for c in found}
    contents = {c.content for c in found}
    assert "Each student pays Rs. 150 at the office counter by the said date." in contents
    assert all("circular, payment part" not in c for c in contents)  # context is never content


# --- reranking -----------------------------------------------------------------------------------


def test_invariant_8_reranker_sees_only_permitted_masked_passages(
    school: dict[str, Any], app_engine: Engine
) -> None:
    tid = school["tenant"]
    recorder = RecordingReranker()
    retriever = HybridRetriever(config(contextual=True, rerank="voyage"), reranker=recorder)
    found = search(tid, retriever, "science exhibition payment kit helpdesk")
    assert recorder.calls
    sent = recorder.sent
    # The best lexical match is forbidden to the caller: never sent, never returned.
    assert all("restricted memo" not in p and "principal" not in p for p in sent)
    assert school["secret"].document_id not in {c.document_id for c in found}
    # Aadhaar-like numbers are masked again before anything leaves retrieval (invariant 4).
    assert all(not contains_full_aadhaar(p) for p in sent)
    assert any("XXXX XXXX" in p for p in sent)
    # Context header and chunk context travel with the passage (include_context).
    assert any(p.startswith("[Circular] Circular No. 14/2026-27\nScience exhibition") for p in sent)
    (query, _, timeout_s) = recorder.calls[0]
    assert query == "science exhibition payment kit helpdesk"
    assert timeout_s == config().rerank.latency_budget_ms / 1000


def test_FR_KB_001_reranker_order_is_used_and_keep_caps_the_result(
    school: dict[str, Any], app_engine: Engine
) -> None:
    tid = school["tenant"]
    plain = search(tid, HybridRetriever(config()), "payment")
    reversed_ = search(
        tid,
        HybridRetriever(
            config(rerank="voyage", keep=2, candidates=2), reranker=RecordingReranker(Reverse())
        ),
        "payment",
    )
    assert len(reversed_) <= 2
    assert [c.chunk_id for c in reversed_] == [c.chunk_id for c in plain[:2]][::-1]
    assert reversed_[0].score > reversed_[-1].score


def test_NFR_AVL_004_reranker_failure_or_slowness_keeps_the_fused_order(
    school: dict[str, Any], app_engine: Engine
) -> None:
    tid = school["tenant"]
    plain = [c.chunk_id for c in search(tid, HybridRetriever(config()), "payment")]
    failing = RecordingReranker(fail=True)
    got = search(tid, HybridRetriever(config(rerank="voyage"), reranker=failing), "payment")
    assert failing.calls
    assert [c.chunk_id for c in got] == plain

    ticks = iter([0.0, 5.0])  # the call "took" 5 s, over the 1.5 s budget
    slow = HybridRetriever(
        config(rerank="voyage"),
        reranker=RecordingReranker(Reverse()),
        clock=lambda: next(ticks),
    )
    assert [c.chunk_id for c in search(tid, slow, "payment")] == plain


def test_FR_KB_001_rerank_off_never_calls_the_reranker(
    school: dict[str, Any], app_engine: Engine
) -> None:
    recorder = RecordingReranker()
    retriever = HybridRetriever(config(), reranker=recorder)
    assert retriever.reranker is None
    search(school["tenant"], retriever, "payment")
    assert recorder.calls == []


# --- the SQL chunk store -------------------------------------------------------------------------


def test_FR_KB_001_sql_store_writes_contexts_and_reads_them_back_for_reuse(
    admin_engine: Engine, app_engine: Engine
) -> None:
    tid = R.make_tenant(admin_engine)
    doc = R.make_document(admin_engine, tid)
    vid = R.add_version(admin_engine, doc)
    try:
        chunks = [
            Chunk(i, f"Synthetic passage {i}.", "[Circular] Synthetic", (), 1, 1, 3, "en")
            for i in (1, 2, 3)
        ]
        contexts = [
            ChunkContext(
                "Synthetic exhibition circular.", "ok", "synthetic-context-1", "contextualize.v1"
            ),
            ChunkContext(status="deferred"),
            ChunkContext(status="rejected", model="synthetic-context-1", prompt="contextualize.v1"),
        ]
        index = VersionIndex(
            document_id=doc.document_id,
            version_id=vid,
            version_no=1,
            embedding_model=R.MODEL,
            filters=ChunkFilters("circular", "Synthetic", None, None, "C1"),
            acl=ChunkAcl(roles=("teacher",)),
            is_latest=True,
            chunks=tuple(
                IndexedChunk(c, tuple(R.topic_vector(f"s{c.chunk_no}")), x)
                for c, x in zip(chunks, contexts, strict=True)
            ),
        )
        store = SqlChunkStore()
        with tenant_session(tid) as s:
            store.replace_version(s, index)
            store.set_latest(s, doc.document_id, vid)
        with tenant_session(tid) as s:
            stored = store.version_contexts(s, vid)
            pending = repo.documents_needing_context(s, limit=10)
        assert {n: v.context for n, v in stored.items()} == dict(
            zip((1, 2, 3), contexts, strict=True)
        )
        assert stored[1].content_sha256 == hashlib.sha256(b"Synthetic passage 1.").digest()
        assert pending == [(doc.document_id, vid)]  # chunk 2 is deferred
        with admin_engine.connect() as c:
            tsv: str = c.execute(
                text(
                    "SELECT context_tsv::text FROM kb.document_chunks "
                    "WHERE chunk_no = 1 AND version_id = :v"
                ),
                {"v": vid},
            ).scalar_one()
        assert "'exhibition'" in tsv
    finally:
        R.delete_tenant_data(admin_engine, [tid])
