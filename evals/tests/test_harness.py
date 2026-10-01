"""End to end: the stub adapters prove the gates pass and trip (docs/06 §13, 14 · M2 exit)."""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from sos_evals import cli, datasets, gates, report, runner
from sos_evals.adapters import AnswerSegment, AskResult, Citation, Retrieved
from sos_evals.schema import Asker, EvalItem
from sos_evals.stubs import PerfectStub

DATA = datasets.load()
PACKAGE = Path(runner.__file__).parent


def _failed(result: report.Report) -> set[str]:
    return {g.gate.metric for g in result.gates if not g.passed}


@pytest.mark.parametrize("suite", ["fast", "full"])
def test_perfect_stub_passes_every_gate(suite: datasets.Suite) -> None:
    result = cli.build_report(adapter="stub-perfect", suite=suite, fail_on_soft=True)
    assert _failed(result) == set()
    assert result.exit_code == 0
    values = result.run.metrics
    assert values.leakage_count == 0
    assert values.injection_success_count == 0
    assert values.recall_at_10 == values.mrr_at_10 == values.citation_precision == 1.0
    assert values.refusal_correctness == values.citation_coverage == values.language_match == 1.0
    assert values.false_refusal_rate == 0.0


@pytest.mark.parametrize("suite", ["fast", "full"])
def test_FR_KB_010_SEC_018_leaky_stub_trips_the_leakage_gate(suite: datasets.Suite) -> None:
    result = cli.build_report(adapter="stub-leaky", suite=suite)
    assert result.exit_code == gates.EXIT_HARD_FAIL
    assert "leakage_count" in _failed(result)
    probes = [o for o in result.run.outcomes if o.category == "permissions"]
    assert probes
    assert all(o.leaks for o in probes)


@pytest.mark.parametrize("suite", ["fast", "full"])
def test_SEC_019_injectable_stub_trips_the_injection_gate(suite: datasets.Suite) -> None:
    result = cli.build_report(adapter="stub-injectable", suite=suite)
    assert result.exit_code == gates.EXIT_HARD_FAIL
    assert "injection_success_count" in _failed(result)
    injected = {i.id for i in DATA.items if i.injection}
    flagged = {o.id for o in result.run.outcomes if o.injection_signals}
    assert injected & {o.id for o in result.run.outcomes} <= flagged


@pytest.mark.parametrize("suite", ["fast", "full"])
def test_ADR_0036_telugu_stub_trips_the_english_first_gates(suite: datasets.Suite) -> None:
    """A system that ignores SOS_TELUGU_ENABLED=false (keeps answering Telugu in Telugu) fails
    both English-first hard gates, and only those."""
    result = cli.build_report(adapter="stub-telugu", suite=suite)
    assert result.exit_code == gates.EXIT_HARD_FAIL
    hard_failed = {
        g.gate.metric for g in result.gates if not g.passed and g.gate.severity == "hard"
    }
    assert hard_failed == {"english_first_telugu_outputs", "english_first_english_answer_rate"}
    kinds = {o.kind for o in result.run.english_first_outcomes if o.telugu_fields}
    assert kinds == {"ask", "circular", "notice", "conversation"}


@pytest.mark.parametrize("suite", ["fast", "full"])
def test_ADR_0036_perfect_stub_is_english_only_with_telugu_hidden(suite: datasets.Suite) -> None:
    result = cli.build_report(adapter="stub-perfect", suite=suite)
    m = result.run.metrics
    assert m.english_first_telugu_outputs == 0
    assert m.english_first_english_answer_rate == 1.0
    assert m.english_first_items > 0
    kinds = {o.kind for o in result.run.english_first_outcomes}
    assert kinds == {"ask", "circular", "notice", "conversation"}
    # The main pass still measures the Telugu path with the switch on (FR-KB-006).
    assert m.language_match == 1.0


def test_committed_baselines_match_the_perfect_stub() -> None:
    """Refresh with `python -m sos_evals run --suite <s> --write-baseline` when data changes."""
    for suite in ("fast", "full"):
        base = report.load_baseline(cli.baseline_path("stub-perfect", suite))
        assert base is not None, suite
        result = cli.build_report(adapter="stub-perfect", suite=suite)
        assert base.dataset_sha256 == result.dataset_sha256, suite
        assert base.metrics == result.run.metrics, suite


def test_diff_marks_direction_per_metric() -> None:
    base = cli.build_report(adapter="stub-perfect", suite="full").run.metrics
    current = base.model_copy(update={"mrr_at_10": 0.8, "leakage_count": 2, "latency_p95_ms": 1.0})
    rows = {row.metric: row for row in report.diff(base, current)}
    assert rows["mrr_at_10"].change == "worse"
    assert rows["leakage_count"].change == "worse"
    assert rows["latency_p95_ms"].change == "better"
    assert rows["recall_at_10"].change == "same"
    assert report.diff(None, current) == ()


class _Scripted:
    """An adapter whose answers the test chooses, for checks the stubs do not exercise."""

    name = "scripted"

    def __init__(self, answers: Mapping[str, AskResult]) -> None:
        self._answers = answers
        self._perfect = PerfectStub(DATA.corpus, DATA.items)

    def retrieve(self, question: str, asker: Asker, k: int) -> Retrieved:
        return self._perfect.retrieve(question, asker, k)

    def ask(self, question: str, asker: Asker) -> AskResult:
        return self._answers.get(question) or self._perfect.ask(question, asker)


def _item(category: str) -> EvalItem:
    return next(i for i in DATA.items if i.category == category and not i.leakage_probe)


def _run_one(item: EvalItem, answer: AskResult) -> runner.ItemOutcome:
    adapter = _Scripted({item.question: answer})
    return runner.run([item], DATA.corpus, adapter, adapter).outcomes[0]


def test_FR_KB_005_invented_quote_and_superseded_version_are_invalid_citations() -> None:
    item = _item("temporal")
    source = item.expected_sources[0]
    old = source.replace("/v2#", "/v1#")
    answer = AskResult(
        segments=(
            AnswerSegment(
                text="Exams begin on 22/09/2026.",
                citations=(
                    Citation(source=source, cited_text="exams begin on 01/01/2027"),
                    Citation(source=old, cited_text="quarterly examinations"),
                ),
            ),
        ),
        refused=False,
        provided_sources=(source, old),
    )
    outcome = _run_one(item, answer)
    assert outcome.citations == 2
    assert outcome.valid_citations == 0
    assert [e.split(":", 1)[0] for e in outcome.citation_errors] == ["text_mismatch", "not_latest"]
    assert outcome.covered_segments == 0
    assert outcome.factual_segments == 1


def test_FR_KB_005_per_sentence_recall_and_unsupported_sentences() -> None:
    """docs/06 §13.2: an uncited sentence lowers citation recall; one with a figure is a
    high-severity unsupported sentence (hard gate); a lead-in needs no citation."""
    item = _item("documents")
    source = item.expected_sources[0]
    lead = DATA.corpus[source].content.split("\n")[0]
    answer = AskResult(
        segments=(
            AnswerSegment(text="Here is what the records say:"),
            AnswerSegment(
                text=f" {lead} [1]", citations=(Citation(source=source, cited_text=lead),)
            ),
            AnswerSegment(text=" The office opens at 07:45 on 01/01/2031."),
            AnswerSegment(text=" Parents are welcome."),
        ),
        refused=False,
        provided_sources=(source,),
    )
    adapter = _Scripted({item.question: answer})
    result = runner.run([item], DATA.corpus, adapter, adapter)
    outcome = result.outcomes[0]
    assert outcome.factual_sentences >= 3
    assert outcome.unsupported_sentences == 2
    assert outcome.high_severity_sentences == 1
    assert outcome.cited_sentences == outcome.factual_sentences - 2
    values = result.metrics
    assert values.citation_recall is not None
    assert values.citation_recall < 1.0
    assert values.unsupported_high_severity_count == 1
    failed = {g.gate.metric for g in gates.evaluate(gates.load_gates(), values) if not g.passed}
    assert {"unsupported_high_severity_count", "citation_recall"} <= failed


def test_per_sentence_metrics_skip_refusals_and_fail_without_data() -> None:
    item = _item("unanswerable")
    answer = AskResult(
        segments=(AnswerSegment(text="Not found in school records you can access."),),
        refused=True,
        provided_sources=(),
    )
    adapter = _Scripted({item.question: answer})
    result = runner.run([item], DATA.corpus, adapter, adapter)
    assert result.outcomes[0].factual_sentences == 0
    assert result.metrics.citation_recall is None
    assert result.metrics.unsupported_high_severity_count is None


def test_FR_KB_007_refusal_with_citations_is_not_a_correct_refusal() -> None:
    item = _item("unanswerable")
    visible_source = next(
        iter(PerfectStub(DATA.corpus, DATA.items).retrieve(item.question, item.asker, 10).sources)
    )
    lead = DATA.corpus[visible_source].content.split("\n")[0]
    answer = AskResult(
        segments=(
            AnswerSegment(
                text="Not found.", citations=(Citation(source=visible_source, cited_text=lead),)
            ),
        ),
        refused=True,
        provided_sources=(visible_source,),
    )
    assert _run_one(item, answer).refusal_correct is False


def test_FR_KB_007_answering_an_unanswerable_question_fails_refusal() -> None:
    item = _item("unanswerable")
    answer = AskResult(
        segments=(AnswerSegment(text="Swimming is on Fridays."),),
        refused=False,
        provided_sources=(),
    )
    result = runner.run(
        [item], DATA.corpus, _Scripted({item.question: answer}), _Scripted({item.question: answer})
    )
    assert result.metrics.refusal_correctness == 0.0


def test_FR_KB_010_content_leak_without_citation_is_caught_by_marker() -> None:
    probe = next(i for i in DATA.items if i.leakage_probe)
    secret = DATA.corpus[probe.probe_sources[0]]
    answer = AskResult(
        segments=(AnswerSegment(text=f"Not found, but {secret.marker} says otherwise."),),
        refused=True,
        provided_sources=(),
    )
    outcome = _run_one(probe, answer)
    assert outcome.leaks == (f"marker:{secret.source}",)


def test_invariant_4_twelve_digit_number_in_an_answer_is_a_leak() -> None:
    item = _item("documents")
    answer = AskResult(
        segments=(AnswerSegment(text="Aadhaar 2345 6789 0123."),),
        refused=False,
        provided_sources=(),
    )
    assert _run_one(item, answer).leaks == ("aadhaar_like:8",)


def test_FR_KB_006_telugu_question_answered_in_english_is_a_language_mismatch() -> None:
    item = next(i for i in DATA.items if i.locale == "te" and not i.expect_refusal)
    answer = AskResult(
        segments=(AnswerSegment(text="Holidays begin on 02/10/2026."),),
        refused=False,
        provided_sources=(),
    )
    assert _run_one(item, answer).language_match is False


def test_runner_measures_latency_when_the_adapter_does_not() -> None:
    item = _item("documents")
    answer = AskResult(segments=(AnswerSegment(text="x"),), refused=True, provided_sources=())
    outcome = _run_one(item, answer)
    assert outcome.ask_latency_ms >= 0.0


def test_cli_exit_codes_and_report_files(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert cli.main(["run", "--adapter", "stub-perfect", "--suite", "full", "--out", str(out)]) == 0
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert data["passed_hard_gates"] is True
    markdown = (out / "report.md").read_text(encoding="utf-8")
    for heading in (
        "# RAG evaluation: PASS",
        "## Gates",
        "## By category",
        "## Diff against baseline",
        "## Failures",
    ):
        assert heading in markdown
    assert cli.main(["run", "--adapter", "stub-leaky", "--out", str(out)]) == 1
    assert "# RAG evaluation: FAIL" in (out / "report.md").read_text(encoding="utf-8")
    assert cli.main(["run", "--adapter", "stub-injectable", "--out", str(out)]) == 1


def test_cli_write_baseline_then_diff(tmp_path: Path) -> None:
    baseline = tmp_path / "b.json"
    args = ["run", "--suite", "fast", "--out", str(tmp_path), "--baseline", str(baseline)]
    assert cli.main([*args, "--write-baseline"]) == 0
    assert report.load_baseline(baseline) is not None
    assert cli.main(args) == 0
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert {row["change"] for row in data["diff"]} == {"same"}


def test_cli_harness_errors_exit_3(tmp_path: Path) -> None:
    assert cli.main(["run", "--datasets", str(tmp_path), "--out", str(tmp_path)]) == 3


def test_cli_generate_check(tmp_path: Path) -> None:
    assert cli.main(["generate", "--check", "--out", str(tmp_path)]) == 1
    assert cli.main(["generate", "--out", str(tmp_path)]) == 0
    assert cli.main(["generate", "--check", "--out", str(tmp_path)]) == 0


def test_harness_imports_no_application_code_and_no_llm_sdk() -> None:
    """CLAUDE.md §11: LLM calls only in app/knowledge/gateway; the harness uses adapters."""
    forbidden = ("app", "anthropic", "openai", "voyageai", "httpx", "requests", "psycopg")
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in forbidden, f"{path.name} imports {name}"
