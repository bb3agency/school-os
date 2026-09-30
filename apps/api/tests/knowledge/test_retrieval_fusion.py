"""Reciprocal Rank Fusion, boosts, diversity and merging (docs/06 §6; FR-KB-001). Pure: no DB."""

from __future__ import annotations

import datetime as dt
import random
import uuid

import pytest

from app.knowledge.config.retrieval import Boosts, Diversity, load_retrieval_config
from app.knowledge.retrieval.fusion import (
    Candidate,
    Scored,
    adjacent_groups,
    apply_boosts,
    apply_rerank,
    diversify,
    reciprocal_rank_fusion,
)

DOC_A, DOC_B = uuid.UUID(int=1), uuid.UUID(int=2)
VER_A, VER_B = uuid.UUID(int=11), uuid.UUID(int=12)


def chunk_id(n: int) -> uuid.UUID:
    return uuid.UUID(int=1000 + n)


def cand(list_no: int, rank: int, n: int, *, doc: uuid.UUID = DOC_A, **kw: object) -> Candidate:
    fields: dict[str, object] = {
        "version_id": VER_A if doc == DOC_A else VER_B,
        "chunk_no": n,
        "page_from": n + 1,
        "page_to": n + 1,
        "doc_type": "circular",
        "issued_on": None,
    }
    fields.update(kw)
    return Candidate(
        list_no=list_no,
        rank=rank,
        chunk_id=chunk_id(n),
        document_id=doc,
        **fields,  # type: ignore[arg-type]
    )


def test_FR_KB_001_rrf_sums_reciprocal_ranks_with_k_60() -> None:
    fused = reciprocal_rank_fusion(
        [cand(0, 1, 1), cand(0, 2, 2), cand(1, 1, 2), cand(2, 3, 1), cand(2, 1, 3)], k=60
    )
    scores = {f.chunk_id: f.score for f in fused}
    assert scores[chunk_id(1)] == pytest.approx(1 / 61 + 1 / 63)
    assert scores[chunk_id(2)] == pytest.approx(1 / 62 + 1 / 61)
    assert scores[chunk_id(3)] == pytest.approx(1 / 61)
    assert [f.chunk_id for f in fused] == [chunk_id(2), chunk_id(1), chunk_id(3)]


def test_FR_KB_001_rrf_ordering_is_deterministic() -> None:
    """Same candidates in any arrival order give the same ranking; ties break by chunk ID."""
    candidates = [cand(lst, r, n) for lst in range(6) for r, n in enumerate(range(20), start=1)]
    candidates += [cand(6, 1, 50), cand(7, 1, 51)]  # an exact tie (1/61 each)
    expected = [f.chunk_id for f in reciprocal_rank_fusion(candidates, k=60)]
    rng = random.Random(7)
    for _ in range(20):
        shuffled = candidates[:]
        rng.shuffle(shuffled)
        assert [f.chunk_id for f in reciprocal_rank_fusion(shuffled, k=60)] == expected
    assert expected.index(chunk_id(50)) < expected.index(chunk_id(51))


def _scored(n: int, score: float, *, doc: uuid.UUID = DOC_A, **kw: object) -> Scored:
    fields: dict[str, object] = {
        "version_id": VER_A if doc == DOC_A else VER_B,
        "chunk_no": n,
        "page_from": n + 1,
        "page_to": n + 1,
        "doc_type": "circular",
        "issued_on": None,
    }
    fields.update(kw)
    return Scored(chunk_id=chunk_id(n), document_id=doc, score=score, **fields)  # type: ignore[arg-type]


def test_FR_KB_001_rerank_reorders_the_head_keeps_the_tail_and_the_rrf_scale() -> None:
    items = [_scored(n, 0.05 - n / 1000) for n in range(1, 6)]
    out = apply_rerank(items, [0.1, 0.9, 0.9], fusion_k=60)
    # Ties keep the fused order (2 before 3); items beyond the head follow in fused order.
    assert [s.chunk_no for s in out] == [2, 3, 1, 4, 5]
    assert [s.score for s in out] == [1 / 61, 1 / 62, 1 / 63, 1 / 64, 1 / 65]
    assert {s.chunk_id for s in out} == {s.chunk_id for s in items}  # never adds or drops
    with pytest.raises(ValueError, match="one rerank score"):
        apply_rerank(items[:1], [0.1, 0.2], fusion_k=60)


def test_shipped_boosts_are_neutral() -> None:
    boosts = load_retrieval_config().boosts
    items = [
        _scored(1, 0.03, issued_on=dt.date(2020, 1, 1)),
        _scored(2, 0.02, issued_on=dt.date(2026, 9, 1), doc_type="verified_answer"),
    ]
    assert apply_boosts(items, boosts, prefer_latest=True) == items


def test_recency_boost_applies_only_to_latest_intent() -> None:
    boosts = Boosts(
        recency_when_latest_intent=True,
        recency_max_factor=2.0,
        recency_half_life_days=30,
        verified_answers=True,
        verified_answer_factor=1.0,
    )
    old = _scored(1, 0.030, issued_on=dt.date(2025, 1, 1))
    new = _scored(2, 0.020, issued_on=dt.date(2026, 9, 1))
    assert [i.chunk_id for i in apply_boosts([old, new], boosts, prefer_latest=False)] == [
        chunk_id(1),
        chunk_id(2),
    ]
    boosted = apply_boosts([old, new], boosts, prefer_latest=True)
    assert [i.chunk_id for i in boosted] == [chunk_id(2), chunk_id(1)]
    assert boosted[0].score == pytest.approx(0.040)


def test_verified_answer_boost() -> None:
    boosts = Boosts(
        recency_when_latest_intent=False,
        recency_max_factor=1.0,
        recency_half_life_days=30,
        verified_answers=True,
        verified_answer_factor=1.5,
    )
    plain = _scored(1, 0.030)
    verified = _scored(2, 0.025, doc_type="verified_answer")
    assert [i.chunk_id for i in apply_boosts([plain, verified], boosts, prefer_latest=False)] == [
        chunk_id(2),
        chunk_id(1),
    ]


def test_diversity_caps_chunks_per_document_and_total() -> None:
    items = [_scored(n, 1.0 - n / 100) for n in range(5)] + [
        _scored(10 + n, 0.5 - n / 100, doc=DOC_B) for n in range(5)
    ]
    chosen = diversify(
        items, Diversity(max_chunks_per_document=3, merge_adjacent_same_page=True), 5
    )
    assert [c.chunk_id for c in chosen] == [chunk_id(n) for n in (0, 1, 2, 10, 11)]


def test_adjacent_chunks_on_the_same_page_are_grouped() -> None:
    items = [
        _scored(3, 0.9, page_from=2, page_to=2),
        _scored(20, 0.8, doc=DOC_B),
        _scored(2, 0.7, page_from=1, page_to=2),
        _scored(5, 0.6, page_from=3, page_to=3),  # not adjacent to 3 (chunk 4 missing)
    ]
    groups = adjacent_groups(items)
    assert [[m.chunk_no for m in g] for g in groups] == [[2, 3], [20], [5]]
