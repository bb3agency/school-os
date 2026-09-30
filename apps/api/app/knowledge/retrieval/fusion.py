"""Pure ranking steps after the SQL branches (docs/06 §6): RRF, boosts, diversity, merging.

Everything here works on candidates that already passed :func:`..acl.acl_predicate` in SQL; it
never widens the set, only orders, trims and merges it. Deterministic: the same candidates always
give the same order (ties broken by chunk ID), so evaluation runs are reproducible.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from app.knowledge.config.retrieval import Boosts, Diversity

VERIFIED_ANSWER_DOC_TYPE = "verified_answer"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One chunk from one branch list, with its 1-based rank in that list."""

    list_no: int
    """Position of the branch list (query text x branch), used only to order the RRF sum."""
    rank: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    chunk_no: int
    page_from: int | None
    page_to: int | None
    doc_type: str
    issued_on: dt.date | None


@dataclass(frozen=True, slots=True)
class Scored:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    chunk_no: int
    page_from: int | None
    page_to: int | None
    doc_type: str
    issued_on: dt.date | None
    score: float


def _order(item: Scored) -> tuple[float, str]:
    return (-item.score, str(item.chunk_id))


def reciprocal_rank_fusion(candidates: Iterable[Candidate], k: int) -> list[Scored]:
    """``score = sum(1 / (k + rank))`` over every list a chunk appears in, best first."""
    ordered = sorted(candidates, key=lambda c: (c.list_no, c.rank, str(c.chunk_id)))
    scores: dict[uuid.UUID, float] = {}
    first: dict[uuid.UUID, Candidate] = {}
    for c in ordered:
        scores[c.chunk_id] = scores.get(c.chunk_id, 0.0) + 1.0 / (k + c.rank)
        first.setdefault(c.chunk_id, c)
    fused = [
        Scored(
            chunk_id=cid,
            document_id=c.document_id,
            version_id=c.version_id,
            chunk_no=c.chunk_no,
            page_from=c.page_from,
            page_to=c.page_to,
            doc_type=c.doc_type,
            issued_on=c.issued_on,
            score=scores[cid],
        )
        for cid, c in first.items()
    ]
    return sorted(fused, key=_order)


def apply_boosts(items: Sequence[Scored], boosts: Boosts, *, prefer_latest: bool) -> list[Scored]:
    """Recency (only when the question implies latest/current) and verified-answer boosts.

    Recency is relative to the newest ``issued_on`` among the candidates (no wall clock, so the
    result is reproducible): ``1 + (max_factor - 1) * 0.5 ** (age_days / half_life_days)``.
    With the shipped neutral factors (1.0) the order is unchanged.
    """
    newest = max((i.issued_on for i in items if i.issued_on is not None), default=None)
    out = []
    for item in items:
        factor = 1.0
        if (
            prefer_latest
            and boosts.recency_when_latest_intent
            and newest is not None
            and item.issued_on is not None
        ):
            age = (newest - item.issued_on).days
            decay = 0.5 ** (age / boosts.recency_half_life_days)
            factor *= 1.0 + (boosts.recency_max_factor - 1.0) * decay
        if boosts.verified_answers and item.doc_type == VERIFIED_ANSWER_DOC_TYPE:
            factor *= boosts.verified_answer_factor
        out.append(replace(item, score=item.score * factor) if factor != 1.0 else item)
    return sorted(out, key=_order)


def apply_rerank(
    items: Sequence[Scored], head_scores: Sequence[float], fusion_k: int
) -> list[Scored]:
    """Reorder the first ``len(head_scores)`` items by the reranker's scores (ties keep their
    fused order); the rest follow in fused order. Scores become ``1 / (fusion_k + rank)`` of the
    new order, so they stay on the RRF scale for merging and display. Never adds or drops."""
    head = list(items[: len(head_scores)])
    if len(head) != len(head_scores):
        raise ValueError("one rerank score per head item")
    ordered = [
        item
        for _, _, item in sorted(
            zip(head_scores, range(len(head)), head, strict=True), key=lambda t: (-t[0], t[1])
        )
    ]
    ordered += items[len(head_scores) :]
    return [replace(item, score=1.0 / (fusion_k + rank)) for rank, item in enumerate(ordered, 1)]


def diversify(items: Sequence[Scored], diversity: Diversity, k: int) -> list[Scored]:
    """At most ``max_chunks_per_document`` per document, at most ``k`` in total, best first."""
    per_doc: dict[uuid.UUID, int] = {}
    chosen: list[Scored] = []
    for item in items:
        if len(chosen) >= k:
            break
        n = per_doc.get(item.document_id, 0)
        if n >= diversity.max_chunks_per_document:
            continue
        per_doc[item.document_id] = n + 1
        chosen.append(item)
    return chosen


def _pages_touch(a: Scored, b: Scored) -> bool:
    if a.page_from is None or a.page_to is None or b.page_from is None or b.page_to is None:
        return a.page_from == b.page_from
    return a.page_from <= b.page_to and b.page_from <= a.page_to


def adjacent_groups(items: Sequence[Scored]) -> list[list[Scored]]:
    """Group consecutive chunks (same version, chunk numbers n and n+1) that share a page.

    Groups are ordered by their best member's position in ``items``; each group's members are
    in chunk order. A chunk that joins nothing is a group of one.
    """
    position = {item.chunk_id: i for i, item in enumerate(items)}
    by_version: dict[uuid.UUID, list[Scored]] = {}
    for item in items:
        by_version.setdefault(item.version_id, []).append(item)
    groups: list[list[Scored]] = []
    for members in by_version.values():
        members.sort(key=lambda m: m.chunk_no)
        current = [members[0]]
        for m in members[1:]:
            prev = current[-1]
            if m.chunk_no == prev.chunk_no + 1 and _pages_touch(prev, m):
                current.append(m)
            else:
                groups.append(current)
                current = [m]
        groups.append(current)
    groups.sort(key=lambda g: min(position[m.chunk_id] for m in g))
    return groups


__all__ = [
    "VERIFIED_ANSWER_DOC_TYPE",
    "Candidate",
    "Scored",
    "adjacent_groups",
    "apply_boosts",
    "apply_rerank",
    "diversify",
    "reciprocal_rank_fusion",
]
