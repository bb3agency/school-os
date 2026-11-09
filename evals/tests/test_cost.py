"""Cost per answer (sos_evals.cost; FA-02, NFR-CST-003..005, FR-KB-046)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest

from sos_evals import cli, cost, gates, report
from sos_evals.adapters import AnswerSegment, AskResult, ModelCall
from sos_evals.stubs import ANSWERED_CALLS

BOOK = cost.load_price_book()
CALL = ModelCall(role="answer", model="gemini-3.8-flash", input_tokens=10_000, output_tokens=800)


def answer(
    *calls: ModelCall, refused: bool = False, mode: Literal["full", "search_only"] = "full"
) -> AskResult:
    return AskResult(
        segments=(AnswerSegment(text="x"),),
        refused=refused,
        provided_sources=(),
        mode=mode,
        calls=calls,
    )


def test_NFR_CST_004_price_in_force_on_the_call_date() -> None:
    intro = BOOK.price_on("gemini-3.8-flash", date(2026, 12, 31))
    january = BOOK.price_on("gemini-3.8-flash", date(2027, 1, 1))
    assert intro.output_usd_per_mtok == Decimal("4.125")
    assert january.output_usd_per_mtok == Decimal("8.25")
    # 10k x 1.65 + 800 x 8.25 per million tokens.
    assert BOOK.call_cost(CALL, date(2027, 1, 1)) == Decimal("0.0231")


def test_NFR_CST_003_cache_tokens_at_the_provider_multipliers() -> None:
    call = CALL.model_copy(update={"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 10})
    write, read = BOOK.cache_multipliers["gemini"]
    price = BOOK.price_on("gemini-3.8-flash", date(2027, 1, 1)).input_usd_per_mtok
    assert BOOK.call_cost(call, date(2027, 1, 1)) == 10 * price * read / Decimal(1_000_000)
    assert write == Decimal("1.00")


def test_FR_KB_046_role_without_model_uses_the_configured_model() -> None:
    assert (
        BOOK.model_of(ModelCall(role="answer", input_tokens=1, output_tokens=1))
        == (BOOK.role_models["answer"])
    )


def test_NFR_CST_003_unknown_model_is_a_harness_error() -> None:
    with pytest.raises(ValueError, match="no price"):
        BOOK.call_cost(CALL.model_copy(update={"model": "nope-1"}), date(2027, 1, 1))


def test_NFR_CST_003_per_billable_answer_and_per_question() -> None:
    answers = [
        answer(CALL, CALL),
        answer(CALL, refused=True),
        answer(CALL, mode="search_only"),
    ]
    metrics, summary = cost.measure(answers, BOOK, price_date=date(2027, 1, 1))
    assert summary is not None
    assert (metrics.cost_questions, metrics.cost_answers, metrics.cost_calls) == (3, 1, 4)
    assert metrics.cost_per_answer_usd == pytest.approx(0.0462)
    assert metrics.cost_per_question_usd == pytest.approx(0.0924 / 3, abs=1e-6)
    assert metrics.cost_per_answer_inr == pytest.approx(0.0462 * 95.97, abs=1e-3)
    assert summary.price_date == "2027-01-01"
    assert summary.unverified_models == ("gemini-3.8-flash",)


def test_NFR_CST_005_projection_overrides_call_dates() -> None:
    dated = CALL.model_copy(update={"called_on": date(2026, 11, 9)})
    as_called, _ = cost.measure([answer(dated)], BOOK)
    projected, _ = cost.measure([answer(dated)], BOOK, price_date=date(2027, 1, 1))
    assert as_called.cost_per_answer_usd == pytest.approx(0.01155)
    assert projected.cost_per_answer_usd == pytest.approx(0.0231)


def test_NFR_CST_003_no_recorded_call_is_not_a_pass() -> None:
    """The gate then fails (evals/tests/test_gates.py: cost_per_answer_usd None)."""
    metrics, summary = cost.measure([answer()], BOOK)
    assert metrics.cost_per_answer_usd is None
    assert summary is None
    assert cost.report_lines(metrics, summary) == []


def test_NFR_CST_003_soft_gate_pinned_to_the_budget_estimate() -> None:
    gate = next(g for g in gates.load_gates() if g.metric == "cost_per_answer_usd")
    assert (gate.op, gate.severity) == ("<=", "soft")
    assert Decimal(str(gate.threshold)) == BOOK.budgeted_cost_per_answer_usd
    assert "NFR-CST-003" in gate.requirements


def test_NFR_CST_003_stub_profile_fits_the_budget_estimate() -> None:
    per_answer = sum(BOOK.call_cost(c, date(2026, 11, 9)) for c in ANSWERED_CALLS)
    assert per_answer <= BOOK.budgeted_cost_per_answer_usd


def test_NFR_CST_005_report_line_and_cli_projection(tmp_path: Path) -> None:
    built = cli.build_report(adapter="stub-perfect", suite="fast", price_date=date(2027, 1, 1))
    assert built.run.metrics.cost_per_answer_usd is not None
    markdown = report.to_markdown(built)
    assert "## Cost per answer (FA-02, NFR-CST-003)" in markdown
    assert "prices in force on: 2027-01-01" in markdown
    args = ["run", "--suite", "fast", "--out", str(tmp_path), "--price-date", "2027-01-01"]
    assert cli.main([*args, "--baseline", str(tmp_path / "none.json")]) == 0
