"""Baseline, diff and the markdown/JSON report of an evaluation run (docs/12 §6)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from sos_evals import authorised_recall, contextual, cost, extraction
from sos_evals.gates import GateResult
from sos_evals.runner import ItemOutcome, Metrics, RunResult

LOWER_IS_BETTER = frozenset(
    {
        "leakage_count",
        "leakage_rate",
        "injection_success_count",
        "injection_success_rate",
        "false_refusal_rate",
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_p99_ms",
        "retrieval_latency_p95_ms",
        "circular_hallucinated_deadlines",
        "fee_leakage_count",
        "fee_guessed_link_count",
        "due_leakage_count",
        "status_leakage_count",
        "ctx_leakage_count",
        "conversation_leakage_count",
        "english_first_telugu_outputs",
        "conversation_scope_violations",
        "authorised_recall_leakage_count",
        "cost_per_answer_usd",
        "cost_per_answer_inr",
        "cost_per_question_usd",
        "extraction_unmasked_aadhaar_count",
        "extraction_needless_redactions",
    }
)
_MAX_LISTED = 20
_LOCALES = ("en", "te", "mixed")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Baseline(_Model):
    adapter: str
    suite: str
    dataset_sha256: str
    metrics: Metrics


class MetricDelta(_Model):
    metric: str
    baseline: float | None
    current: float | None
    delta: float | None
    change: Literal["better", "worse", "same", "n/a"]


class Report(_Model):
    adapter: str
    suite: str
    dataset_sha256: str
    passed_hard_gates: bool
    exit_code: int
    gates: tuple[GateResult, ...]
    run: RunResult
    baseline_dataset_sha256: str | None
    diff: tuple[MetricDelta, ...]


def load_baseline(path: Path) -> Baseline | None:
    if not path.is_file():
        return None
    return Baseline.model_validate_json(path.read_text(encoding="utf-8"))


def write_baseline(path: Path, baseline: Baseline) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(baseline.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def diff(baseline: Metrics | None, current: Metrics) -> tuple[MetricDelta, ...]:
    if baseline is None:
        return ()
    rows = []
    for name in Metrics.model_fields:
        if name == "items":
            continue
        old, new = getattr(baseline, name), getattr(current, name)
        if old is None or new is None:
            rows.append(
                MetricDelta(metric=name, baseline=old, current=new, delta=None, change="n/a")
            )
            continue
        delta = float(new) - float(old)
        if abs(delta) < 1e-9:
            change: Literal["better", "worse", "same", "n/a"] = "same"
        elif (delta < 0) == (name in LOWER_IS_BETTER):
            change = "better"
        else:
            change = "worse"
        rows.append(MetricDelta(metric=name, baseline=old, current=new, delta=delta, change=change))
    return tuple(rows)


def fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.3f}"


def to_markdown(report: Report) -> str:
    status = "PASS" if report.passed_hard_gates else "FAIL"
    lines = [
        f"# RAG evaluation: {status}",
        "",
        f"- Adapter: `{report.adapter}` · suite: `{report.suite}` · items: "
        f"{report.run.metrics.items}",
        f"- Dataset sha256: `{report.dataset_sha256[:16]}`",
        f"- Exit code: {report.exit_code}",
        "",
        "## Gates",
        "",
        "| Gate | Severity | Value | Threshold | Result | Requirements |",
        "|---|---|---|---|---|---|",
    ]
    for result in report.gates:
        gate = result.gate
        lines.append(
            f"| {gate.metric} | {gate.severity} | {fmt(result.value)} | {gate.op} "
            f"{fmt(gate.threshold)} | {'pass' if result.passed else '**FAIL**'} | "
            f"{', '.join(gate.requirements)} |"
        )
    names = [
        n
        for n in Metrics.model_fields
        if n != "items"
        and not n.startswith(
            (
                "circular_",
                "fee_",
                "due_",
                "status_",
                "ctx_",
                "conversation_",
                "followup_",
                "memory_",
                "authorised_recall_",
                "cost_",
                "judge_",
                "faithfulness",
                "answer_correctness",
                "extraction_",
            )
        )
    ]
    lines += [
        "",
        "## By category",
        "",
        "| Category | Items | " + " | ".join(names) + " |",
        "|---|---|" + "---|" * len(names),
    ]
    for category, values in report.run.by_category.items():
        cells = " | ".join(fmt(getattr(values, n)) for n in names)
        lines.append(f"| {category} | {values.items} | {cells} |")
    lines += ["", "## Diff against baseline", ""]
    if not report.diff:
        lines.append("No baseline for this adapter and suite.")
    else:
        if report.baseline_dataset_sha256 != report.dataset_sha256:
            lines += ["The dataset changed since the baseline; compare with care.", ""]
        lines += ["| Metric | Baseline | Current | Delta | Change |", "|---|---|---|---|---|"]
        for row in report.diff:
            change = f"**{row.change}**" if row.change == "worse" else row.change
            lines.append(
                f"| {row.metric} | {fmt(row.baseline)} | {fmt(row.current)} | "
                f"{fmt(row.delta)} | {change} |"
            )
    lines += ["", "## Failures", ""]
    lines += _failures(report.run.outcomes) or ["None."]
    lines += ["", "## Circular reading (M4)", ""]
    lines += _circular_lines(report.run) or ["Not measured."]
    lines += ["", "## Fee dues from Tally (M6)", ""]
    lines += _fee_lines(report.run) or ["Not measured."]
    lines += ["", "## What's due this week (D74)", ""]
    lines += _due_lines(report.run) or ["Not measured."]
    lines += ["", "## School status questions (D86)", ""]
    lines += _status_lines(report.run) or ["Not measured."]
    lines += ["", "## Contextual retrieval and reranking (docs/06 §13.6)", ""]
    lines += _contextual_lines(report.run) or ["Not measured."]
    lines += ["", "## Ask conversations (ADR-0034)", ""]
    lines += _conversation_lines(report.run) or ["Not measured."]
    lines += ["", "## English first, Telugu hidden (ADR-0036)", ""]
    lines += _english_first_lines(report.run) or ["Not measured."]
    lines += ["", "## Authorised retrieval recall (docs/06 §6)", ""]
    lines += _authorised_recall_lines(report.run) or ["Not measured."]
    lines += ["", "## Cost per answer (FA-02, NFR-CST-003)", ""]
    lines += cost.report_lines(report.run.metrics, report.run.cost_report) or ["Not measured."]
    lines += ["", "## Faithfulness and answer correctness (FA-07, docs/06 §13.11)", ""]
    lines += _judge_lines(report.run) or ["Not measured."]
    lines += ["", "## Register-photo extraction (FA-07, docs/06 §13.12)", ""]
    lines += _extraction_lines(report.run) or ["Not measured."]
    return "\n".join(lines) + "\n"


def _judge_lines(run: RunResult) -> list[str]:
    m = run.metrics
    if run.judge_name is None:
        return []
    lines = [
        f"- Judge: `{run.judge_name}` · calibration items {m.judge_calibration_items} · "
        f"agreement {fmt(m.judge_agreement)} · kappa {fmt(m.judge_kappa)}",
        f"- Faithfulness {fmt(m.faithfulness)} over {m.faithfulness_claims} factual sentences · "
        f"answer correctness {fmt(m.answer_correctness)} over {m.answer_correctness_items} "
        "questions",
    ]
    if run.judge_cost_usd is not None:
        lines.append(f"- Judge spend (eval cost, not per answer): ${run.judge_cost_usd:.4f}")
    wrong = [o for o in run.judge_outcomes if o.verdict != o.label]
    if wrong:
        lines.append(
            "- Disagreements: "
            + ", ".join(f"`{o.id}` ({o.kind}, {o.perturbation})" for o in wrong[:_MAX_LISTED])
        )
    return lines


def _extraction_lines(run: RunResult) -> list[str]:
    """Counts and leak kinds only: never a value a page carried (invariant 4)."""
    if not run.extraction_outcomes:
        return []
    m = run.metrics
    lines = [
        f"- Pages {m.extraction_items} · planted numbers {m.extraction_planted} · redaction "
        f"recall {fmt(m.extraction_aadhaar_redaction_recall)} · unmasked "
        f"{fmt(m.extraction_unmasked_aadhaar_count)} · field accuracy "
        f"{fmt(m.extraction_field_accuracy)} · row recall {fmt(m.extraction_row_recall)} · "
        f"images withheld {m.extraction_images_withheld} · needless redactions "
        f"{m.extraction_needless_redactions}",
    ]
    for kind in ("printed", "handwritten"):
        sub = extraction.aggregate([o for o in run.extraction_outcomes if o.script == kind])
        lines.append(
            f"- {kind}: field accuracy {fmt(sub.extraction_field_accuracy)}, row recall "
            f"{fmt(sub.extraction_row_recall)}"
        )
    for o in run.extraction_outcomes:
        if o.leaked_planted or o.unmasked_numbers:
            lines.append(
                f"- `{o.id}` ({o.script}, {o.locale}): {o.unmasked_numbers} unmasked number(s) "
                f"in {', '.join(o.leak_places) or 'output'}"
            )
    return lines


def _failures(outcomes: Sequence[ItemOutcome]) -> list[str]:
    lines: list[str] = []
    for o in outcomes:
        problems = []
        if o.leaks:
            problems.append("leak: " + ", ".join(o.leaks[:3]))
        if o.injection_signals:
            problems.append("injection: " + ", ".join(o.injection_signals[:3]))
        if o.citation_errors:
            problems.append("citations: " + ", ".join(o.citation_errors[:3]))
        if o.refusal_correct is False:
            problems.append("did not refuse (or cited sources while refusing)")
        if not o.expect_refusal and o.refused:
            problems.append("refused an answerable question")
        if o.covered_segments < o.factual_segments:
            problems.append("uncited factual sentence")
        if o.unsupported_sentences:
            problems.append(
                f"{o.unsupported_sentences} unsupported sentence(s), "
                f"{o.high_severity_sentences} with a figure"
            )
        if not o.language_match:
            problems.append("language mismatch")
        if problems:
            lines.append(f"- `{o.id}` ({o.category}): " + "; ".join(problems))
    if len(lines) > _MAX_LISTED:
        lines = [*lines[:_MAX_LISTED], f"- … and {len(lines) - _MAX_LISTED} more (see report.json)"]
    return lines


def _circular_lines(run: RunResult) -> list[str]:
    if not run.circular_outcomes:
        return []
    m = run.metrics
    lines = [
        f"- Circulars: {m.circular_items} · recall {fmt(m.circular_deadline_recall)} · "
        f"precision {fmt(m.circular_deadline_precision)} · citation validity "
        f"{fmt(m.circular_citation_validity)} · hallucinated "
        f"{fmt(m.circular_hallucinated_deadlines)} · complete "
        f"{fmt(m.circular_complete_rate)} · metadata {fmt(m.circular_metadata_accuracy)}",
    ]
    for o in run.circular_outcomes:
        problems = []
        if o.failed:
            problems.append("not read (manual review)")
        if o.found < o.expected:
            problems.append(f"missed {o.expected - o.found} of {o.expected} deadlines")
        if o.correct < o.suggested:
            problems.append(f"{o.suggested - o.correct} suggestion(s) not a deadline")
        if o.valid_citations < o.suggested:
            problems.append(f"{o.suggested - o.valid_citations} invalid citation(s)")
        if o.hallucinated:
            problems.append(f"{o.hallucinated} date(s) not in the circular")
        if o.metadata_right < o.metadata_checked:
            problems.append("metadata wrong")
        if problems:
            lines.append(f"- `{o.id}` ({o.locale}): " + "; ".join(problems))
    return lines


def _fee_lines(run: RunResult) -> list[str]:
    if not run.fee_outcomes:
        return []
    m = run.metrics
    lines = [
        f"- Cases: {m.fee_items} · figure accuracy {fmt(m.fee_figure_accuracy)} · leakage "
        f"{fmt(m.fee_leakage_count)} · guessed links {fmt(m.fee_guessed_link_count)} · "
        f"citation validity {fmt(m.fee_citation_validity)} · refusal correctness "
        f"{fmt(m.fee_refusal_correctness)}",
    ]
    for o in run.fee_outcomes:
        problems = []
        if o.figure_correct is False:
            problems.append("figure missing or wrong")
        if o.refusal_correct is False:
            problems.append("stated a figure it must not")
        if o.leaks:
            problems.append("leak: " + ", ".join(o.leaks[:3]))
        if o.guessed:
            problems.append("mapped an unlinked ledger by name")
        if o.valid_amounts < o.stated:
            problems.append(f"{o.stated - o.valid_amounts} uncited amount(s)")
        if problems:
            lines.append(f"- `{o.id}` ({o.locale}): " + "; ".join(problems))
    return lines


def _status_lines(run: RunResult) -> list[str]:
    if not run.status_outcomes:
        return []
    m = run.metrics
    lines = [
        f"- Cases: {m.status_items} · leakage {fmt(m.status_leakage_count)} · citation "
        f"validity {fmt(m.status_citation_validity)} · recall {fmt(m.status_recall)}",
    ]
    for o in run.status_outcomes:
        problems = []
        if o.leaks:
            problems.append("leak: " + ", ".join(o.leaks[:3]))
        if o.found < o.expected:
            problems.append(f"{o.expected - o.found} expected fact(s) missing")
        if o.valid < o.stated:
            problems.append(f"{o.stated - o.valid} uncited fact(s)")
        if problems:
            lines.append(f"- `{o.id}` ({o.tool}, {o.locale}): " + "; ".join(problems))
    return lines


def _due_lines(run: RunResult) -> list[str]:
    if not run.due_outcomes:
        return []
    m = run.metrics
    lines = [
        f"- Cases: {m.due_items} · leakage {fmt(m.due_leakage_count)} · citation validity "
        f"{fmt(m.due_citation_validity)} · task recall {fmt(m.due_task_recall)}",
    ]
    for o in run.due_outcomes:
        problems = []
        if o.leaks:
            problems.append("leak: " + ", ".join(o.leaks[:3]))
        if o.found < o.expected:
            problems.append(f"{o.expected - o.found} expected task(s) missing")
        if o.valid < o.stated:
            problems.append(f"{o.stated - o.valid} uncited task(s)")
        if problems:
            lines.append(f"- `{o.id}` ({o.locale}): " + "; ".join(problems))
    return lines


def _contextual_lines(run: RunResult) -> list[str]:
    if not run.contextual_outcomes:
        return []
    m = run.metrics
    lines = [
        f"- Questions: {m.ctx_items} ({sum(o.needs_context for o in run.contextual_outcomes)} "
        f"about a page that does not name its subject) · leakage {fmt(m.ctx_leakage_count)} · "
        f"recall gain from contexts {fmt(m.ctx_recall_gain_contextual)} · MRR gain from "
        f"reranking {fmt(m.ctx_mrr_gain_rerank)}",
        "",
        "| Variant | Recall@5 | MRR@10 | "
        + " | ".join(f"Recall@5 {loc}" for loc in _LOCALES)
        + " |",
        "|---|---|---|" + "---|" * len(_LOCALES),
    ]
    table = contextual.by_locale(run.contextual_outcomes)
    for variant in contextual.VARIANTS:
        per_locale = " | ".join(fmt(table.get(loc, {}).get(variant)) for loc in _LOCALES)
        lines.append(
            f"| {variant} | {fmt(getattr(m, f'ctx_recall_at_5_{variant}'))} | "
            f"{fmt(getattr(m, f'ctx_mrr_at_10_{variant}'))} | {per_locale} |"
        )
    leaks = [o for o in run.contextual_outcomes if o.leaks]
    lines += [f"- `{o.id}` leak: " + ", ".join(o.leaks[:3]) for o in leaks]
    return lines


def _conversation_lines(run: RunResult) -> list[str]:
    if not run.conversation_outcomes:
        return []
    m = run.metrics
    lines = [
        f"- Cases: {m.conversation_items} · leakage {fmt(m.conversation_leakage_count)} · scope "
        f"violations {fmt(m.conversation_scope_violations)} · context accuracy "
        f"{fmt(m.conversation_context_accuracy)} · follow-up language "
        f"{fmt(m.followup_language_match)} · memory preference {fmt(m.memory_preference_applied)}",
    ]
    for o in run.conversation_outcomes:
        problems = []
        if o.leaks:
            problems.append("leak: " + ", ".join(o.leaks[:3]))
        if o.violations:
            problems.append("violation: " + ", ".join(o.violations[:3]))
        if o.context_ok is False:
            problems.append("follow-up not understood (expected source not cited)")
        if o.followups_match is False:
            problems.append("follow-ups in another language")
        if o.preference_ok is False:
            problems.append("memory preference not applied")
        if problems:
            lines.append(f"- `{o.id}` step {o.step} ({o.category}): " + "; ".join(problems))
    return lines


def _english_first_lines(run: RunResult) -> list[str]:
    if not run.english_first_outcomes:
        return []
    m = run.metrics
    lines = [
        f"- With SOS_TELUGU_ENABLED off: {m.english_first_items} probes, "
        f"{m.english_first_fields} shown texts · Telugu outputs "
        f"{fmt(m.english_first_telugu_outputs)} · English answers "
        f"{fmt(m.english_first_english_answer_rate)}",
    ]
    for o in run.english_first_outcomes:
        if o.telugu_fields or o.english_answer is False:
            problems = list(o.telugu_fields[:5])
            if o.english_answer is False:
                problems.append("answer not in English")
            lines.append(f"- `{o.id}` ({o.kind}): " + ", ".join(problems))
    return lines


def _authorised_recall_lines(run: RunResult) -> list[str]:
    if not run.authorised_recall_outcomes:
        return []
    m = run.metrics
    lines = [
        f"- Probes: {m.authorised_recall_items} · recall@10 {fmt(m.authorised_recall_at_10)} · "
        f"critical (<= 5 % visible) {fmt(m.authorised_recall_at_10_critical)} · leakage "
        f"{fmt(m.authorised_recall_leakage_count)}",
        "",
        "| Level | Visible share | Authorised chunks | Recall@10 | Route |",
        "|---|---|---|---|---|",
    ]
    table = authorised_recall.by_level(run.authorised_recall_outcomes)
    for level in authorised_recall.LEVELS:
        if level.id in table:
            recall, authorised, routes = table[level.id]
            lines.append(
                f"| {level.id} | {level.selectivity:.0%} | {authorised} | {fmt(recall)} | "
                f"{routes} |"
            )
    return lines


def write(report: Report, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / "report.json", directory / "report.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    md_path.write_text(to_markdown(report), encoding="utf-8", newline="\n")
    return json_path, md_path
