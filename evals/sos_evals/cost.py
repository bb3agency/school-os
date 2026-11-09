"""Cost per answer from recorded token counts and the ``models.yaml`` price list (FA-02).

NFR-CST-003..005, FR-KB-046. Every :class:`~sos_evals.adapters.ModelCall` a question made is
priced at the list price in force on its call date (``prices.<model>`` with its ``schedule``;
NFR-CST-004), with prompt-cache tokens at the provider's cache multipliers, exactly as the
gateway meters it (``app/knowledge/gateway/metering.py``). The harness reads ``models.yaml``
itself (it never imports the application) so the prices stay in one file (invariant 13).

``cost_per_answer_usd`` is the mean cost of a billable answer (a written answer that is not a
refusal; ADR-0038 decision 4), the same quantity as ``models.yaml``
``budget.bundle.cost_per_answer_usd``; questions that bill nothing (refusals, search-only) are
covered by the bundle budget's headroom and show in ``cost_per_question_usd`` (every question's
calls over every question; the eval sets over-weight refusals, so it is not a production mix).
``--price-date`` prices the same token counts at another date's prices, for example 2027-01-01
when introductory prices end.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from sos_evals.adapters import AskResult, ModelCall
from sos_evals.datasets import EVALS_DIR

MODELS_FILE = EVALS_DIR.parent / "apps" / "api" / "app" / "knowledge" / "config" / "models.yaml"
_MTOK = Decimal(1_000_000)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class Price:
    effective_from: date | None
    """None: the flat price, in force until the first scheduled step."""
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal


@dataclass(frozen=True)
class PriceBook:
    """The parts of ``models.yaml`` that price a call."""

    prices: Mapping[str, tuple[Price, ...]]
    providers: Mapping[str, str]
    """Model -> provider (``capabilities``; candidates without one count as ``gemini``)."""
    unverified: frozenset[str]
    role_models: Mapping[str, str]
    cache_multipliers: Mapping[str, tuple[Decimal, Decimal]]
    """Provider -> (write, read) multiples of the input price."""
    usd_inr_rate: Decimal
    budgeted_cost_per_answer_usd: Decimal
    """``budget.bundle.cost_per_answer_usd``: the estimate bundle budgets are derived from."""

    def price_on(self, model: str, day: date) -> Price:
        steps = self.prices.get(model)
        if steps is None:
            raise ValueError(f"models.yaml has no price for model {model!r}")
        current = steps[0]
        for step in steps[1:]:
            if step.effective_from is not None and step.effective_from <= day:
                current = step
        return current

    def model_of(self, call: ModelCall) -> str:
        if call.model is not None:
            return call.model
        model = self.role_models.get(call.role)
        if model is None:
            raise ValueError(f"models.yaml has no role {call.role!r}")
        return model

    def call_cost(self, call: ModelCall, day: date) -> Decimal:
        model = self.model_of(call)
        price = self.price_on(model, day)
        write, read = self.cache_multipliers[self.providers.get(model, "gemini")]
        inp, out = price.input_usd_per_mtok, price.output_usd_per_mtok
        return (
            call.input_tokens * inp
            + call.cache_write_tokens * inp * write
            + call.cache_read_tokens * inp * read
            + call.output_tokens * out
        ) / _MTOK


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def parse_price_book(data: Mapping[str, Any]) -> PriceBook:
    prices: dict[str, tuple[Price, ...]] = {}
    unverified = set()
    for model, entry in data["prices"].items():
        steps = [Price(None, _dec(entry["input_usd_per_mtok"]), _dec(entry["output_usd_per_mtok"]))]
        steps += [
            Price(
                _date(s["effective_from"]),
                _dec(s["input_usd_per_mtok"]),
                _dec(s["output_usd_per_mtok"]),
            )
            for s in entry.get("schedule", ())
        ]
        prices[model] = tuple(steps)
        if entry.get("verified", True) is False:
            unverified.add(model)
    anthropic = data["cache_price_multipliers"]
    gemini = data["gemini"]["cache_price_multipliers"]
    budget = data["budget"]
    return PriceBook(
        prices=prices,
        providers={m: c["provider"] for m, c in data.get("capabilities", {}).items()},
        unverified=frozenset(unverified),
        role_models={r: c["model"] for r, c in data["roles"].items()},
        cache_multipliers={
            "anthropic": (_dec(anthropic["write"]), _dec(anthropic["read"])),
            "gemini": (_dec(gemini["write"]), _dec(gemini["read"])),
        },
        usd_inr_rate=_dec(budget["usd_inr_rate"]),
        budgeted_cost_per_answer_usd=_dec(budget["bundle"]["cost_per_answer_usd"]),
    )


def load_price_book(path: Path = MODELS_FILE) -> PriceBook:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected a mapping at the top level")
    return parse_price_book(data)


class CostMetrics(_Model):
    """Merged into :class:`sos_evals.runner.Metrics`. None when no call was recorded."""

    cost_questions: int = 0
    cost_answers: int = 0
    """Billable answers: written (``full``) answers that were not refusals."""
    cost_calls: int = 0
    cost_per_answer_usd: float | None = None
    cost_per_answer_inr: float | None = None
    cost_per_question_usd: float | None = None


class CostReport(_Model):
    """What the report prints next to the metric (NFR-CST-005)."""

    price_date: str
    """An ISO date (``--price-date``) or ``call date``."""
    usd_inr_rate: float
    budgeted_cost_per_answer_usd: float
    total_usd: float
    by_model: dict[str, float]
    """Model -> USD over the run."""
    calls_by_role: dict[str, int]
    unverified_models: tuple[str, ...]
    """Models priced with a ``verified: false`` price (the estimate is provisional)."""


def billable(answer: AskResult) -> bool:
    return not answer.refused and answer.mode == "full"


def measure(
    answers: Sequence[AskResult],
    book: PriceBook,
    *,
    price_date: date | None = None,
    today: date | None = None,
) -> tuple[CostMetrics, CostReport | None]:
    """Cost of the run's questions. ``price_date`` overrides every call date (a projection);
    otherwise each call's ``called_on``, else ``today`` (the run date, UTC)."""
    run_day = today or datetime.now(UTC).date()
    calls = [call for answer in answers for call in answer.calls]
    answered = sum(1 for a in answers if billable(a))
    if not calls:
        return CostMetrics(cost_questions=len(answers), cost_answers=answered), None
    total = answered_cost = Decimal(0)
    by_model: dict[str, Decimal] = defaultdict(Decimal)
    by_role: dict[str, int] = defaultdict(int)
    for answer in answers:
        for call in answer.calls:
            day = price_date or call.called_on or run_day
            cost = book.call_cost(call, day)
            total += cost
            answered_cost += cost if billable(answer) else 0
            by_model[book.model_of(call)] += cost
            by_role[call.role] += 1
    per_answer = answered_cost / answered if answered else None
    metrics = CostMetrics(
        cost_questions=len(answers),
        cost_answers=answered,
        cost_calls=len(calls),
        cost_per_answer_usd=round(float(per_answer), 6) if per_answer is not None else None,
        cost_per_answer_inr=round(float(per_answer * book.usd_inr_rate), 4)
        if per_answer is not None
        else None,
        cost_per_question_usd=round(float(total / len(answers)), 6),
    )
    report = CostReport(
        price_date=price_date.isoformat() if price_date else "call date",
        usd_inr_rate=float(book.usd_inr_rate),
        budgeted_cost_per_answer_usd=float(book.budgeted_cost_per_answer_usd),
        total_usd=round(float(total), 6),
        by_model={m: round(float(v), 6) for m, v in sorted(by_model.items())},
        calls_by_role=dict(sorted(by_role.items())),
        unverified_models=tuple(sorted(m for m in by_model if m in book.unverified)),
    )
    return metrics, report


def report_lines(metrics: Any, report: CostReport | None) -> list[str]:
    """The cost section of the markdown report (one summary line, then the breakdown)."""
    if report is None or metrics.cost_per_answer_usd is None:
        return []
    lines = [
        f"- Cost per answer: ${metrics.cost_per_answer_usd:.4f} "
        f"(Rs {metrics.cost_per_answer_inr:.2f} at {report.usd_inr_rate}) over "
        f"{metrics.cost_answers} billable answers of {metrics.cost_questions} questions, "
        f"{metrics.cost_calls} model calls; prices in force on: {report.price_date}; "
        f"budgeted ${report.budgeted_cost_per_answer_usd:.4f}",
        f"- Cost per question: ${metrics.cost_per_question_usd:.4f}; run total "
        f"${report.total_usd:.4f}",
        "- Calls by role: " + ", ".join(f"{r} {n}" for r, n in report.calls_by_role.items()),
        "- Spend by model: " + ", ".join(f"{m} ${v:.4f}" for m, v in report.by_model.items()),
    ]
    if report.unverified_models:
        lines.append(
            "- Provisional: unverified prices (verified: false in models.yaml) for "
            + ", ".join(report.unverified_models)
        )
    return lines
