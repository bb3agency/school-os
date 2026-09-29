"""M4 circular reading eval (docs/06 §13.3; FR-CIR-003, FR-CIR-008): data, metrics and gates."""

from __future__ import annotations

from datetime import date

import pytest

from sos_evals import circulars, datasets, gates, runner
from sos_evals.circular_cases import CASES
from sos_evals.circulars import CircularCase, CircularResult, SuggestedDeadline
from sos_evals.stubs import PerfectStub

DATA = datasets.load()


def _perfect() -> PerfectStub:
    return PerfectStub(DATA.corpus, DATA.items)


def test_FR_CIR_008_dataset_covers_english_telugu_and_code_mixed() -> None:
    assert DATA.circulars == CASES
    locales = {c.locale for c in DATA.circulars}
    assert locales == {"en", "te", "mixed"}
    assert sum(len(c.expected_deadlines) for c in DATA.circulars) >= 30
    assert any(not c.expected_deadlines for c in DATA.circulars), "a circular with no deadline"
    for case in DATA.circulars:
        assert "synthetic" in case.text.casefold() or any(
            place in case.text
            for place in ("Sitarampuram", "Kondapalli", "సీతారాంపురం", "కొండపల్లి", "నమూనా")
        ), case.id  # invented offices only


def test_expected_deadlines_must_be_written_in_the_circular() -> None:
    bad = CircularCase(
        id="circ-bad",
        locale="en",
        title="x",
        lines=("Submit by 15/10/2026.",),
        expected_deadlines=(date(2026, 10, 16),),
    )
    with pytest.raises(ValueError, match="not written"):
        circulars.validate_cases([bad])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("by 15/10/2026", {date(2026, 10, 15)}),
        ("on 5th November 2026", {date(2026, 11, 5)}),
        ("by December 5, 2026", {date(2026, 12, 5)}),
        ("20 అక్టోబర్ 2026 లోగా", {date(2026, 10, 20)}),
        ("10.11.2026 లోపు", {date(2026, 11, 10)}),
        ("31/02/2026", set()),
    ],
)
def test_harness_reads_dates_on_its_own(text: str, expected: set[date]) -> None:
    assert circulars.dates_in(text) == expected


def test_perfect_reader_scores_one() -> None:
    reading, outcomes = circulars.run(DATA.circulars, _perfect())
    assert reading.circular_deadline_recall == 1.0
    assert reading.circular_deadline_precision == 1.0
    assert reading.circular_citation_validity == 1.0
    assert reading.circular_hallucinated_deadlines == 0
    assert all(o.complete for o in outcomes)


class Hallucinating:
    """Adds a deadline the circular never writes, with a made-up quote."""

    name = "hallucinating"

    def read_circular(self, case: CircularCase) -> CircularResult:
        base = _perfect().read_circular(case)
        extra = SuggestedDeadline(due_on=date(2030, 1, 1), quote="Pay by 01/01/2030.")
        return base.model_copy(update={"deadlines": (*base.deadlines, extra)})


class Forgetful:
    """Reads nothing (as if every circular needed manual review)."""

    name = "forgetful"

    def read_circular(self, case: CircularCase) -> CircularResult:
        return CircularResult(failed=True)


def _gate_failures(adapter: object) -> set[str]:
    stub = _perfect()
    result = runner.run(
        DATA.select("fast"),
        DATA.corpus,
        stub,
        stub,
        circular=adapter,  # type: ignore[arg-type]
        circular_cases=DATA.circulars,
    )
    return {
        r.gate.metric
        for r in gates.evaluate(gates.load_gates(), result.metrics)
        if not r.passed and r.gate.severity == "hard"
    }


def test_FR_CIR_003_a_hallucinated_deadline_trips_the_hard_gates() -> None:
    failed = _gate_failures(Hallucinating())
    assert "circular_hallucinated_deadlines" in failed
    assert "circular_citation_validity" in failed


def test_FR_CIR_008_reading_nothing_trips_recall() -> None:
    assert "circular_deadline_recall" in _gate_failures(Forgetful())


def test_FR_CIR_008_unmeasured_reading_fails_its_gates() -> None:
    stub = _perfect()
    result = runner.run(DATA.select("fast"), DATA.corpus, stub, stub)
    failed = {
        r.gate.metric
        for r in gates.evaluate(gates.load_gates(), result.metrics)
        if not r.passed and r.gate.severity == "hard"
    }
    assert "circular_deadline_recall" in failed
    assert "circular_hallucinated_deadlines" in failed
