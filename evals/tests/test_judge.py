"""Faithfulness and answer correctness with a calibrated judge (FA-07; docs/06 §13.11)."""

from __future__ import annotations

import tomllib
from collections.abc import Sequence

import pytest

from sos_evals import cli, datasets, gates, judge
from sos_evals.adapters import AnswerSegment, AskResult, Citation, ModelCall
from sos_evals.judge import CalibrationItem, StubJudge

DATA = datasets.load()
CONFIG = judge.load_config()
STUB = StubJudge(CONFIG.stub)


class _AlwaysYes:
    """A judge that approves everything (the failure calibration must catch)."""

    name = "always-yes"

    def __init__(self) -> None:
        self.calls: list[ModelCall] = []

    def supported(self, claim: str, passages: Sequence[str]) -> bool:
        return True

    def correct(self, question: str, answer: str, reference: str) -> bool:
        return True


def test_FR_KB_053_calibration_set_is_large_balanced_and_labelled() -> None:
    """docs/06 §13.2: the judge is calibrated on >= 100 labelled items, both kinds, both labels."""
    items = DATA.judge_calibration
    assert len(items) >= 100
    for kind in ("faithfulness", "correctness"):
        labels = [i.label for i in items if i.kind == kind]
        assert True in labels
        assert False in labels
    assert {i.perturbation for i in items} >= {
        "verbatim",
        "first_clause",
        "date_shift",
        "added_figure",
        "other_passage",
        "other_document",
        "not_found",
    }


def test_FR_KB_054_min_agreement_is_pinned_to_the_gate() -> None:
    (gate,) = [g for g in gates.load_gates() if g.metric == "judge_agreement"]
    assert gate.threshold == CONFIG.min_agreement
    data = tomllib.loads(judge.JUDGE_FILE.read_text(encoding="utf-8"))
    assert data["live"]["faithfulness_system"].strip()


def test_FR_KB_053_stub_judge_agrees_with_every_label() -> None:
    agreement, kappa, outcomes = judge.calibrate(STUB, DATA.judge_calibration)
    assert agreement == 1.0
    assert kappa == 1.0
    assert len(outcomes) == len(DATA.judge_calibration)


def test_FR_KB_054_an_uncalibrated_judge_measures_nothing() -> None:
    """A judge that says yes to everything disagrees with the negative labels: faithfulness and
    correctness stay None, so their gates fail (missing evidence is not a pass)."""
    yes = _AlwaysYes()
    found, _ = judge.measure(
        yes, DATA.judge_calibration, [], DATA.corpus, min_agreement=CONFIG.min_agreement
    )
    assert found.judge_agreement is not None
    assert found.judge_agreement < CONFIG.min_agreement
    assert found.faithfulness is None
    assert found.answer_correctness is None


@pytest.mark.parametrize(
    ("claim", "supported"),
    [
        ("The meeting is on 10/10/2026 from 10:00 to 13:00.", True),
        ("The meeting is on 11/10/2026 from 10:00 to 13:00.", False),
        ("The meeting is on 10/10/2026; the fee is Rs 4,250.", False),
        ("Sports day prizes go to every house captain.", False),
    ],
)
def test_FR_KB_051_stub_judge_checks_figures_and_words(claim: str, supported: bool) -> None:
    passage = "Circular: the parent-teacher meeting is on 10/10/2026 from 10:00 to 13:00."
    assert STUB.supported(claim, [passage]) is supported


def test_FR_KB_052_correctness_needs_every_reference_figure() -> None:
    assert STUB.correct("When?", "It is on 10/10/2026 at 10:00.", "10/10/2026, 10:00")
    assert not STUB.correct("When?", "It is on 10/10/2026.", "10/10/2026, 10:00")
    assert not STUB.correct("When?", "Not found in school records.", "10/10/2026")


def _item_and_answer(text: str, *, cite: bool, refused: bool = False) -> tuple[object, AskResult]:
    item = next(i for i in DATA.items if i.id == "documents-001")
    source = item.expected_sources[0]
    lead = DATA.corpus[source].content.split("\n", 1)[0]
    citations = (Citation(source=source, cited_text=lead),) if cite else ()
    answer = AskResult(
        segments=(AnswerSegment(text=text, citations=citations),),
        refused=refused,
        provided_sources=(source,),
    )
    return item, answer


def test_FR_KB_051_uncited_sentence_is_unfaithful_without_asking_the_judge() -> None:
    item, answer = _item_and_answer("The meeting is on 10/10/2026 from 10:00 to 13:00.", cite=False)
    result = judge.judge_item(_AlwaysYes(), item, answer, DATA.corpus)  # type: ignore[arg-type]
    assert (result.claims, result.supported) == (1, 0)


def test_FR_KB_051_the_passage_label_with_type_title_and_issue_date_is_supported() -> None:
    """The application prefixes each cited sentence with the source's label ("Circular · Title ·
    15/09/2026 (p.1):"); that metadata belongs to the source, so it is not an unsupported claim."""
    item = next(i for i in DATA.items if i.id == "documents-001")
    source = DATA.corpus[item.expected_sources[0]]
    assert source.issued_on is not None
    lead = source.content.split("\n", 1)[0]
    label = f"Circular · {source.title} · {source.issued_on:%d/%m/%Y} (p.1): {lead}"
    _, answer = _item_and_answer(label, cite=True)
    result = judge.judge_item(STUB, item, answer, DATA.corpus)
    assert (result.claims, result.supported) == (1, 1)


def test_FR_KB_052_a_refusal_of_an_answerable_question_is_incorrect() -> None:
    item, answer = _item_and_answer("Not found in school records.", cite=False, refused=True)
    result = judge.judge_item(STUB, item, answer, DATA.corpus)  # type: ignore[arg-type]
    assert result.correct is False
    assert result.claims == 0


@pytest.mark.parametrize("suite", ["fast", "full"])
def test_FR_KB_051_FR_KB_052_perfect_stub_is_faithful_and_correct(suite: datasets.Suite) -> None:
    m = cli.build_report(adapter="stub-perfect", suite=suite).run.metrics
    assert m.judge_agreement == 1.0
    assert m.faithfulness == 1.0
    assert m.answer_correctness is not None
    assert m.answer_correctness >= 0.85
    assert m.faithfulness_claims > 0
    assert m.answer_correctness_items > 0


def test_FR_KB_051_leaky_stub_fails_faithfulness() -> None:
    """Answers from sources it was not given (invalid citations) are not faithful."""
    result = cli.build_report(adapter="stub-leaky", suite="fast")
    m = result.run.metrics
    assert m.faithfulness is not None
    assert m.faithfulness < 0.95


def test_calibration_items_reject_missing_inputs() -> None:
    bad = CalibrationItem(
        id="judge-001", kind="faithfulness", perturbation="x", text="t", label=True
    )
    with pytest.raises(ValueError, match="passages"):
        judge.validate_calibration([bad])
