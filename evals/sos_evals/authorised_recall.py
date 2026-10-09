"""Authorised retrieval recall (docs/06 §6 "Authorised recall", §13.2; FR-KB-001, FR-KB-007).

The question: when a caller's permissions narrow the candidates heavily (a class teacher who
sees 1-5 % of a school), does the production vector search still find the nearest passages the
caller MAY see? An approximate index over a graph shared by every school can stop early and
return too few or the wrong passages, which the answer then reports as "not found in school
records" (a false refusal no other metric attributes to retrieval).

The harness fixes the probes: :data:`LEVELS` (ACL selectivity: the share of the school's chunks
the caller may see) and a number of query vectors per level (:data:`FAST_QUERIES` /
:data:`FULL_QUERIES`). The system under test builds a synthetic clustered vector corpus for one
school (and other schools in the same index), and for each probe returns:

- ``exact``: the oracle's k nearest AUTHORISED chunks with their exact distances (the same
  permission filter, ranked exactly with no index), nearest first;
- ``returned``: what the production path returned, each with its exact distance, or ``None``
  when the chunk is not authorised for the caller (a leak: scored as a miss and counted).

Recall@k is tie-tolerant: a returned chunk counts when its exact distance is within
:data:`TIE_EPSILON` of the k-th exact distance. ``authorised_recall_at_10`` is the mean over every
probe; ``authorised_recall_at_10_critical`` over the critical levels (selectivity <= 5 %: the
narrow callers who get false "not found"). Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict, Field

K: Final = 10
TIE_EPSILON: Final = 1e-4
FAST_QUERIES: Final = 4
FULL_QUERIES: Final = 12


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecallLevel(_Model):
    id: str = Field(pattern=r"^sel-[0-9]{3}$")
    selectivity: float = Field(gt=0.0, le=1.0)
    """Share of the school's chunks the caller may see."""
    critical: bool
    """A narrow caller (<= 5 %): the class or section teacher of FR-KB-007's false refusals."""


LEVELS: Final[tuple[RecallLevel, ...]] = (
    RecallLevel(id="sel-100", selectivity=1.0, critical=False),
    RecallLevel(id="sel-020", selectivity=0.2, critical=False),
    RecallLevel(id="sel-005", selectivity=0.05, critical=True),
    RecallLevel(id="sel-001", selectivity=0.01, critical=True),
)


class RecallHit(_Model):
    chunk: str
    distance: float | None = Field(default=None, ge=0.0)
    """Exact distance to the query; None = the chunk is not authorised for the caller."""


class RecallRetrieved(_Model):
    exact: tuple[RecallHit, ...]
    """The oracle's k nearest authorised chunks, nearest first (fewer when fewer exist)."""
    returned: tuple[RecallHit, ...]
    """The production path's top k, best first."""
    authorised: int = Field(ge=0)
    route: str | None = None
    """How the system ranked (e.g. ``exact`` or ``ann``), for the report."""


class AuthorisedRecallAdapter(Protocol):
    """Optionally also ``prepare_authorised_recall(levels, queries) -> None``: called once
    before the probes (the application adapter builds the vector corpus there)."""

    name: str

    def retrieve_authorised(self, level: RecallLevel, query: int, k: int) -> RecallRetrieved:
        """Query vector number ``query`` as a caller who sees ``level.selectivity``."""
        ...


class RecallOutcome(_Model):
    level: str
    selectivity: float
    critical: bool
    query: int
    recall: float
    authorised: int
    leaks: int
    route: str | None


class AuthorisedRecallMetrics(_Model):
    authorised_recall_items: int = 0
    authorised_recall_at_10: float | None = None
    authorised_recall_at_10_critical: float | None = None
    authorised_recall_leakage_count: int | None = None


def recall_at_k(result: RecallRetrieved, k: int = K) -> float:
    """Tie-tolerant recall@k; 1.0 when nothing is authorised and nothing is returned."""
    want = min(k, len(result.exact))
    if want == 0:
        return 1.0 if not result.returned else 0.0
    kth = result.exact[want - 1].distance
    if kth is None:
        raise ValueError("the oracle's hits must carry distances")
    hits = sum(
        1 for h in result.returned[:k] if h.distance is not None and h.distance <= kth + TIE_EPSILON
    )
    return min(hits, want) / want


def score(level: RecallLevel, query: int, result: RecallRetrieved, k: int = K) -> RecallOutcome:
    distances = [h.distance for h in result.exact if h.distance is not None]
    if len(distances) != len(result.exact) or distances != sorted(distances):
        raise ValueError(f"{level.id}/{query}: the oracle list must be sorted with distances")
    return RecallOutcome(
        level=level.id,
        selectivity=level.selectivity,
        critical=level.critical,
        query=query,
        recall=recall_at_k(result, k),
        authorised=result.authorised,
        leaks=sum(1 for h in result.returned if h.distance is None),
        route=result.route,
    )


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate(outcomes: Sequence[RecallOutcome]) -> AuthorisedRecallMetrics:
    if not outcomes:
        return AuthorisedRecallMetrics()
    return AuthorisedRecallMetrics(
        authorised_recall_items=len(outcomes),
        authorised_recall_at_10=_mean([o.recall for o in outcomes]),
        authorised_recall_at_10_critical=_mean([o.recall for o in outcomes if o.critical]),
        authorised_recall_leakage_count=sum(1 for o in outcomes if o.leaks),
    )


def by_level(outcomes: Sequence[RecallOutcome]) -> dict[str, tuple[float, int, str]]:
    """Per level: mean recall, authorised chunks and the routes seen (the report's table)."""
    table: dict[str, tuple[float, int, str]] = {}
    for level in LEVELS:
        mine = [o for o in outcomes if o.level == level.id]
        if mine:
            routes = ", ".join(sorted({o.route or "?" for o in mine}))
            table[level.id] = (sum(o.recall for o in mine) / len(mine), mine[0].authorised, routes)
    return table


def run(
    adapter: AuthorisedRecallAdapter, *, fast: bool, levels: Sequence[RecallLevel] = LEVELS
) -> tuple[AuthorisedRecallMetrics, tuple[RecallOutcome, ...]]:
    queries = FAST_QUERIES if fast else FULL_QUERIES
    prepare = getattr(adapter, "prepare_authorised_recall", None)
    if callable(prepare):
        prepare(tuple(levels), queries)
    outcomes = [
        score(level, q, adapter.retrieve_authorised(level, q, K))
        for level in levels
        for q in range(queries)
    ]
    return aggregate(outcomes), tuple(outcomes)


__all__ = [
    "FAST_QUERIES",
    "FULL_QUERIES",
    "LEVELS",
    "TIE_EPSILON",
    "AuthorisedRecallAdapter",
    "AuthorisedRecallMetrics",
    "K",
    "RecallHit",
    "RecallLevel",
    "RecallOutcome",
    "RecallRetrieved",
    "aggregate",
    "by_level",
    "recall_at_k",
    "run",
    "score",
]
