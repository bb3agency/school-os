"""The school status set and its scoring (D86; docs/06 §13.13; FA-04, FC-11 tools; FR-KB-079)."""

from __future__ import annotations

import pytest

from sos_evals import cli, datasets, status
from sos_evals.status import StatusCitation, StatusResult


def test_FR_KB_079_every_tool_has_cases_with_traps() -> None:
    cases = datasets.load().status
    assert {c.tool for c in cases} == set(status.GRANTS)
    for tool in status.GRANTS:
        mine = [c for c in cases if c.tool == tool]
        assert any(c.expected for c in mine), tool  # something to state
        assert any(c.asker not in status.GRANTS[tool] for c in mine), tool  # no permission
    scoped = [c for c in cases if status.GRANTS[c.tool].get(c.asker) == "scoped"]
    assert any(not i.in_reach for c in scoped for i in c.items)  # outside the teacher's section
    assert any(
        i.students < status.SMALL_CELL_MIN
        for c in cases
        if c.tool == "get_attendance_summary"
        for i in c.items
    )
    assert any(not i.shown for c in cases if c.tool == "get_certificates" for i in c.items)
    assert any(c.locale == "te" for c in cases)


def test_FR_KB_079_the_perfect_stub_passes_and_the_leaky_stub_leaks() -> None:
    good = cli.build_report(adapter="stub-perfect", suite="fast").run.metrics
    assert good.status_items == len(datasets.load().status)
    assert (good.status_leakage_count, good.status_citation_validity, good.status_recall) == (
        0,
        1.0,
        1.0,
    )
    bad = cli.build_report(adapter="stub-leaky", suite="fast")
    assert bad.run.metrics.status_leakage_count
    assert any(g.gate.metric == "status_leakage_count" and not g.passed for g in bad.gates)


def test_FR_KB_079_an_uncited_fact_fails_citation_validity() -> None:
    case = next(c for c in datasets.load().status if c.expected)
    first = next(i for i in case.items if i.key == case.expected[0])
    result = StatusResult(
        text=f"{first.phrase}.",
        citations=(StatusCitation(source="sos://compliance/x", cited_text=first.phrase),),
        provided_sources=(),
    )
    outcome = status.score(case, result)
    assert (outcome.stated, outcome.valid) == (1, 0)


def test_FR_KB_079_a_wrong_answer_key_is_refused() -> None:
    case = next(c for c in datasets.load().status if len(c.expected) < len(c.items))
    broken = case.model_copy(update={"expected": tuple(i.key for i in case.items)})
    with pytest.raises(ValueError, match="expected must be"):
        status.validate_cases([broken])
