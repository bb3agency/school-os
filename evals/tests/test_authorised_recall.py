"""Authorised retrieval recall metric and gates (docs/06 §6; FR-KB-001, FR-KB-007)."""

from __future__ import annotations

import pytest

from sos_evals import authorised_recall as ar
from sos_evals import cli, gates, report


def _hits(*distances: float | None, prefix: str = "c") -> tuple[ar.RecallHit, ...]:
    return tuple(ar.RecallHit(chunk=f"{prefix}{i}", distance=d) for i, d in enumerate(distances))


def test_FR_KB_001_recall_counts_the_nearest_authorised_chunks() -> None:
    exact = _hits(0.1, 0.2, 0.3)
    assert ar.recall_at_k(ar.RecallRetrieved(exact=exact, returned=exact, authorised=3)) == 1.0
    worse = _hits(0.1, 0.9, 0.95, prefix="w")
    got = ar.recall_at_k(ar.RecallRetrieved(exact=exact, returned=worse, authorised=3))
    assert got == pytest.approx(1 / 3)


def test_FR_KB_001_recall_is_tie_tolerant() -> None:
    """An equally near chunk the oracle ordered differently is not a miss."""
    exact = _hits(0.1, 0.2)
    tied = _hits(0.1, 0.2 + ar.TIE_EPSILON / 2, prefix="t")
    assert ar.recall_at_k(ar.RecallRetrieved(exact=exact, returned=tied, authorised=5)) == 1.0


def test_FR_KB_007_too_few_results_lower_recall() -> None:
    """The false "not found": the path returns fewer chunks than are authorised."""
    exact = _hits(*(0.01 * i for i in range(1, 11)))
    short = ar.RecallRetrieved(exact=exact, returned=exact[:3], authorised=40)
    assert ar.recall_at_k(short) == pytest.approx(0.3)


def test_FR_KB_002_a_chunk_outside_the_acl_is_a_miss_and_a_leak() -> None:
    exact = _hits(0.1, 0.2)
    leaked = (ar.RecallHit(chunk="x", distance=None), exact[0])
    result = ar.RecallRetrieved(exact=exact, returned=leaked, authorised=2)
    outcome = ar.score(ar.LEVELS[-1], 0, result)
    assert outcome.recall == 0.5
    assert outcome.leaks == 1
    assert ar.aggregate([outcome]).authorised_recall_leakage_count == 1


def test_nothing_authorised_and_nothing_returned_is_perfect() -> None:
    assert ar.recall_at_k(ar.RecallRetrieved(exact=(), returned=(), authorised=0)) == 1.0


def test_the_oracle_list_must_be_sorted_with_distances() -> None:
    bad = ar.RecallRetrieved(exact=_hits(0.3, 0.1), returned=(), authorised=2)
    with pytest.raises(ValueError, match="sorted"):
        ar.score(ar.LEVELS[0], 0, bad)
    missing = ar.RecallRetrieved(exact=_hits(None), returned=(), authorised=1)
    with pytest.raises(ValueError, match="sorted"):
        ar.score(ar.LEVELS[0], 0, missing)


def test_critical_levels_are_the_narrow_callers() -> None:
    assert {level.id for level in ar.LEVELS if level.critical} == {"sel-005", "sel-001"}
    assert all(level.selectivity <= 0.05 for level in ar.LEVELS if level.critical)
    assert {level.selectivity for level in ar.LEVELS} == {1.0, 0.2, 0.05, 0.01}


def test_aggregate_separates_the_critical_levels() -> None:
    full = ar.RecallOutcome(
        level="sel-100", selectivity=1.0, critical=False, query=0, recall=1.0,
        authorised=4000, leaks=0, route="ann",
    )  # fmt: skip
    narrow = full.model_copy(
        update={"level": "sel-001", "selectivity": 0.01, "critical": True, "recall": 0.5}
    )
    m = ar.aggregate([full, narrow])
    assert m.authorised_recall_at_10 == pytest.approx(0.75)
    assert m.authorised_recall_at_10_critical == pytest.approx(0.5)
    assert ar.aggregate([]).authorised_recall_at_10 is None  # not measured: the gate fails


def test_FR_KB_001_soft_gates_are_the_documented_ones() -> None:
    mine = {
        (g.metric, g.op, g.threshold, g.severity)
        for g in gates.load_gates()
        if g.metric.startswith("authorised_recall")
    }
    assert mine == {
        ("authorised_recall_at_10", ">=", 0.90, "soft"),
        ("authorised_recall_at_10_critical", ">=", 0.95, "soft"),
    }


@pytest.mark.parametrize("suite", ["fast", "full"])
def test_perfect_stub_has_full_authorised_recall(suite: str) -> None:
    result = cli.build_report(adapter="stub-perfect", suite=suite)  # type: ignore[arg-type]
    m = result.run.metrics
    queries = ar.FAST_QUERIES if suite == "fast" else ar.FULL_QUERIES
    assert m.authorised_recall_items == len(ar.LEVELS) * queries
    assert m.authorised_recall_at_10 == 1.0
    assert m.authorised_recall_at_10_critical == 1.0
    assert m.authorised_recall_leakage_count == 0
    assert "## Authorised retrieval recall" in report.to_markdown(result)


def test_leaky_stub_loses_authorised_recall() -> None:
    result = cli.build_report(adapter="stub-leaky", suite="fast")
    m = result.run.metrics
    assert m.authorised_recall_leakage_count == m.authorised_recall_items
    soft_failed = {g.gate.metric for g in result.gates if not g.passed}
    assert "authorised_recall_at_10_critical" in soft_failed
