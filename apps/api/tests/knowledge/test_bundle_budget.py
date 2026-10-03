"""A school's monthly AI budget derived from its AI answer bundle (FR-KB-011, NFR-CST-001).

Owner decision 2026-10-03 (ADR-0038 amendment): the budget is the bundle's included answers x an
estimated cost per answer, plus headroom for overage, all in ``models.yaml`` ``budget.bundle``.
Spend stays metered in USD and the budget stays a cost cap (FR-KB-011): above it Ask answers
search-only. A school without a bundle keeps its own ``ai_monthly_budget_inr`` setting.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.knowledge.config.llm import load_llm_config
from app.knowledge.gateway.budget import bundle_budget_inr

# The owner's bundles (0041_billing_catalogue; docs/16 §5.6).
BUNDLES = {"ai-lite": 300, "ai-standard": 1000, "ai-high": 3000}


def test_FR_KB_011_bundle_budget_rule_is_config() -> None:
    b = load_llm_config().budget
    assert b.bundle.cost_per_answer_usd > 0
    assert 0 <= b.bundle.overage_headroom_fraction <= 2


@pytest.mark.parametrize("answers", sorted(BUNDLES.values()))
def test_FR_KB_011_budget_is_answers_x_cost_plus_headroom(answers: int) -> None:
    cfg = load_llm_config()
    b = cfg.budget
    expected_usd = (
        answers
        * b.bundle.cost_per_answer_usd
        * (1 + Decimal(str(b.bundle.overage_headroom_fraction)))
    )
    got = bundle_budget_inr(cfg, answers)
    assert got == (expected_usd * b.usd_inr_rate).quantize(Decimal(1))
    # The guard converts back at the same rate: the USD budget is the expected one, to a rupee.
    assert abs(got / b.usd_inr_rate - expected_usd) < Decimal(1) / b.usd_inr_rate


def test_FR_KB_011_bigger_bundles_get_bigger_budgets() -> None:
    cfg = load_llm_config()
    lite, standard, high = (bundle_budget_inr(cfg, BUNDLES[c]) for c in BUNDLES)
    assert 0 < lite < standard < high


def test_FR_KB_011_included_answers_fit_with_headroom_left() -> None:
    """With the configured cost per answer the bundle's own answers use at most the share of
    the budget below the headroom, so overage answers (billed at the overage rate) still fit."""
    cfg = load_llm_config()
    b = cfg.budget
    for answers in BUNDLES.values():
        used_by_quota = answers * b.bundle.cost_per_answer_usd * b.usd_inr_rate
        assert used_by_quota < bundle_budget_inr(cfg, answers)


def test_FR_KB_011_no_answers_no_budget() -> None:
    with pytest.raises(ValueError, match="positive"):
        bundle_budget_inr(load_llm_config(), 0)
