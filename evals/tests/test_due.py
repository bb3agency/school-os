"""The "what's due this week" set and its scoring (D74; docs/06 §13.10; list_due_tasks)."""

from __future__ import annotations

import pytest

from sos_evals import cli, datasets, due
from sos_evals.due import DueCitation, DueResult


def test_D74_cases_include_a_permission_limited_caller_with_others_tasks_due() -> None:
    cases = datasets.load().due
    limited = [
        c
        for c in cases
        if c.asker not in due.SCHOOL_WIDE and any(not t.mine and 0 <= t.days <= 7 for t in c.tasks)
    ]
    assert len(limited) >= 3
    assert any(c.locale == "te" for c in cases)
    assert any(not c.expected for c in cases)  # nothing of their own due: no title at all


def test_D74_the_perfect_stub_passes_and_the_leaky_stub_leaks() -> None:
    good = cli.build_report(adapter="stub-perfect", suite="fast").run.metrics
    assert good.due_items == len(datasets.load().due)
    assert (good.due_leakage_count, good.due_citation_validity, good.due_task_recall) == (
        0,
        1.0,
        1.0,
    )
    bad = cli.build_report(adapter="stub-leaky", suite="fast")
    assert bad.run.metrics.due_leakage_count
    assert any(g.gate.metric == "due_leakage_count" and not g.passed for g in bad.gates)


def test_D74_an_uncited_title_fails_citation_validity() -> None:
    case = next(c for c in datasets.load().due if c.expected)
    first = next(t for t in case.tasks if t.key == case.expected[0])
    result = DueResult(
        text=f"{first.title}.",
        citations=(DueCitation(source="sos://task/x", cited_text=first.title),),
        provided_sources=(),
    )
    outcome = due.score(case, result)
    assert (outcome.stated, outcome.valid) == (1, 0)


def test_D74_a_wrong_answer_key_is_refused() -> None:
    case = datasets.load().due[0]
    broken = case.model_copy(update={"expected": tuple(t.key for t in case.tasks)})
    with pytest.raises(ValueError, match="expected must be"):
        due.validate_cases([broken])
