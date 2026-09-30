"""Contextual retrieval and reranking eval set (docs/06 §13.6; FR-KB-001, FR-KB-002, SEC-018).

The committed set matches its generator and is consistent (titles never name the subject, most
questions are about a page that never names it, restricted memos are never expected); scoring
finds pages by range, counts a restricted or unknown page (retrieved or sent to a reranker) as a
leak; the perfect stub passes and the leaky stub trips the hard leakage gate.
"""

from __future__ import annotations

import pytest

from sos_evals import cli, contextual, datasets, gates
from sos_evals.contextual import (
    ContextualSet,
    CtxHit,
    CtxQuestion,
    CtxRetrieved,
    needs_context,
    score,
)
from sos_evals.contextual_cases import CASES


def test_FR_KB_001_the_committed_set_is_the_generated_one_and_is_consistent() -> None:
    data = datasets.load().contextual
    assert data == CASES
    locales = {q.locale for q in CASES.questions}
    assert locales == {"en", "te", "mixed"}
    hard = [q for q in CASES.questions if needs_context(q, CASES.document(q.document))]
    assert len(hard) >= len(CASES.questions) // 2  # the set measures what contexts are for
    assert {q.page for q in CASES.questions} == {1, 2, 3}  # page 1 is the control group
    assert CASES.restricted
    assert all(not CASES.document(q.document).restricted for q in CASES.questions)
    assert CASES.select(fast=True)
    assert set(CASES.select(fast=True)) < set(CASES.questions)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"document": "ctx-rs-01"}, "visible"),
        ({"topic": "sports day"}, "topic"),
        ({"page": 9}, "outside"),
        ({"document": "ctx-unknown"}, "unknown"),
    ],
)
def test_validation_rejects_a_wrong_answer_key(change: dict[str, object], match: str) -> None:
    question = CASES.questions[1].model_copy(update=change)
    with pytest.raises(ValueError, match=match):
        ContextualSet(documents=CASES.documents, questions=(question,))


def test_validation_rejects_a_title_naming_the_topic() -> None:
    doc = CASES.documents[0]
    renamed = doc.model_copy(update={"title": "Science exhibition circular"})
    with pytest.raises(ValueError, match="title"):
        ContextualSet(documents=(renamed, *CASES.documents[1:]), questions=CASES.questions[:3])


def test_FR_KB_002_scoring_ranks_pages_and_counts_restricted_or_unknown_pages_as_leaks() -> None:
    q: CtxQuestion = CASES.questions[1]  # page 2 of ctx-en-01
    doc = CASES.document(q.document)
    good = CtxRetrieved(
        hits=(
            CtxHit(document="ctx-en-02", page_from=2, page_to=2),
            CtxHit(document=q.document, page_from=1, page_to=3),  # a merged page range
        )
    )
    leaky = CtxRetrieved(
        hits=(CtxHit(document=q.document, page_from=2, page_to=2),),
        reranked_documents=("ctx-rs-01", None),
    )
    outcome = score(q, doc, {"plain": good, "rerank": leaky}, CASES.restricted)
    assert outcome.ranks == {"plain": 2, "rerank": 1}
    assert outcome.leaks == ("rerank:reranker:ctx-rs-01", "rerank:reranker:None")
    unknown = CtxRetrieved(hits=(CtxHit(document=None, page_from=1, page_to=1),))
    assert score(q, doc, {"plain": unknown}, CASES.restricted).leaks == ("plain:retrieved:None",)


def test_aggregate_gains_and_missing_evidence() -> None:
    assert contextual.aggregate(()).ctx_recall_gain_contextual is None  # a gate on it fails


@pytest.mark.parametrize("suite", ["fast", "full"])
def test_FR_KB_002_perfect_stub_passes_and_leaky_stub_trips_the_contextual_leakage_gate(
    suite: datasets.Suite,
) -> None:
    perfect = cli.build_report(adapter="stub-perfect", suite=suite)
    m = perfect.run.metrics
    assert m.ctx_leakage_count == 0
    assert m.ctx_recall_at_5_contextual == 1.0
    assert m.ctx_recall_gain_contextual is not None
    assert m.ctx_recall_gain_contextual > 0
    leaky = cli.build_report(adapter="stub-leaky", suite=suite)
    assert leaky.run.metrics.ctx_leakage_count == leaky.run.metrics.ctx_items
    failed = {r.gate.metric for r in leaky.gates if not r.passed and r.gate.severity == "hard"}
    assert "ctx_leakage_count" in failed
    assert leaky.exit_code == gates.EXIT_HARD_FAIL
