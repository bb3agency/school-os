"""AI unit economics in models.yaml (FA-02): dated prices, candidate models, light-model routing.

NFR-CST-004: a price schedule (an introductory price, then the January 2027 price) and the
gateway's metering and reservations use the price in force on the call date. FR-KB-047: candidate
answer models are priced for estimates only and no role may select one. FR-KB-048..050: the
router stays off; the routing decision sends only clear single-tool questions to the light role.
"""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.authz.kv import InMemoryKV
from app.knowledge.config import llm
from app.knowledge.config._base import CONFIG_DIR
from app.knowledge.config.tools import load_tools_config
from app.knowledge.gateway.budget import (
    BudgetGuard,
    InMemorySpendLedger,
    StaticAiPolicy,
    TenantAiSettings,
)
from app.knowledge.routing import choose_answer_role

CANDIDATES = ("gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.1-flash-lite")


def raw() -> dict[str, Any]:
    data = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return copy.deepcopy(data)


def scheduled(data: dict[str, Any]) -> dict[str, Any]:
    """The selected answer model with a test schedule: half price until 2027, then list."""
    entry = data["prices"]["gemini-3.5-flash"]
    entry["schedule"] = [
        {
            "effective_from": date(2027, 1, 1),
            "input_usd_per_mtok": entry["input_usd_per_mtok"],
            "output_usd_per_mtok": entry["output_usd_per_mtok"],
        }
    ]
    entry["input_usd_per_mtok"], entry["output_usd_per_mtok"] = "0.80", "4.00"
    return data


# --- NFR-CST-004: prices with an effective-date schedule ------------------------------------


def test_NFR_CST_004_price_in_force_on_the_call_date() -> None:
    config = llm.load_llm_config()
    intro = config.price_on("gemini-3.8-flash", date(2026, 12, 31))
    january = config.price_on("gemini-3.8-flash", date(2027, 1, 1))
    assert (intro.input_usd_per_mtok, intro.output_usd_per_mtok) == (
        Decimal("0.825"),
        Decimal("4.125"),
    )
    assert (january.input_usd_per_mtok, january.output_usd_per_mtok) == (
        Decimal("1.65"),
        Decimal("8.25"),
    )
    # A model without a schedule has one price whatever the date.
    flash = config.prices["gemini-3.5-flash"]
    assert config.price_on("gemini-3.5-flash", date(2030, 1, 1)).output_usd_per_mtok == (
        flash.output_usd_per_mtok
    )


@pytest.mark.parametrize("model", ["gemini-3.6-flash", "gemini-3.8-flash"])
def test_NFR_CST_004_intro_price_ends_on_31_december_2026(model: str) -> None:
    steps = llm.load_llm_config().prices[model].schedule
    assert [s.effective_from for s in steps] == [date(2027, 1, 1)]


def test_NFR_CST_004_schedule_must_ascend() -> None:
    data = raw()
    data["prices"]["gemini-3.8-flash"]["schedule"] = [
        {"effective_from": "2027-06-01", "input_usd_per_mtok": "2", "output_usd_per_mtok": "9"},
        {"effective_from": "2027-01-01", "input_usd_per_mtok": "1", "output_usd_per_mtok": "8"},
    ]
    with pytest.raises(ValidationError, match="ascending"):
        llm.LlmConfig.model_validate(data)


def test_NFR_CST_004_unverified_price_needs_a_source() -> None:
    data = raw()
    del data["prices"]["gemini-3.8-flash"]["source"]
    with pytest.raises(ValidationError, match="source"):
        llm.LlmConfig.model_validate(data)


def _guard(config: llm.LlmConfig, at: datetime) -> BudgetGuard:
    return BudgetGuard(
        config,
        StaticAiPolicy(TenantAiSettings(True, Decimal(1000))),
        InMemorySpendLedger(),
        InMemoryKV(),
        now=lambda: at,
    )


def test_NFR_CST_004_reservation_and_metering_use_the_call_date_price() -> None:
    config = llm.LlmConfig.model_validate(scheduled(raw()))
    role = config.roles["answer"]
    december = _guard(config, datetime(2026, 12, 31, 23, 0, tzinfo=UTC))
    january = _guard(config, datetime(2027, 1, 1, 0, 30, tzinfo=UTC))
    assert december.price(role.model).output_usd_per_mtok == Decimal("4.00")
    assert january.price(role.model).output_usd_per_mtok == Decimal("9.90")
    assert january.estimate_usd(role) > december.estimate_usd(role)


# --- FR-KB-047: candidate answer models, never selected -------------------------------------


def test_FR_KB_047_candidates_are_priced_unverified_and_unselected() -> None:
    config = llm.load_llm_config()
    for model in CANDIDATES:
        price = config.prices[model]
        assert price.candidate
        assert not price.verified
        assert price.source
    selected = {r.model for r in config.roles.values()}
    selected |= {r.fallback.model for r in config.roles.values() if r.fallback}
    assert selected.isdisjoint(CANDIDATES)
    assert not any(config.prices[m].candidate for m in selected)


def test_FR_KB_047_a_role_cannot_select_a_candidate() -> None:
    data = raw()
    data["roles"]["answer"]["model"] = "gemini-3.8-flash"
    data["capabilities"]["gemini-3.8-flash"] = dict(data["capabilities"]["gemini-3.5-flash"])
    with pytest.raises(ValidationError, match="candidate"):
        llm.LlmConfig.model_validate(data)


# --- FR-KB-048..050: light-model routing, off by default ------------------------------------


def test_FR_KB_048_routing_is_off_and_every_question_keeps_the_answer_role() -> None:
    routing = llm.load_llm_config().routing
    assert routing.enabled is False
    decision = choose_answer_role(routing, "How many students are in Class 7?", has_history=False)
    assert (decision.role, decision.reason) == ("answer", "disabled")


def test_FR_KB_049_intents_name_whitelisted_tools() -> None:
    routing = llm.load_llm_config().routing
    assert routing.single_tool_intents
    assert set(routing.single_tool_intents) <= set(load_tools_config().tools)
    assert routing.light_role == "router"
    assert routing.max_tool_rounds == 1


def enabled() -> llm.AnswerRouting:
    return llm.load_llm_config().routing.model_copy(update={"enabled": True})


@pytest.mark.parametrize(
    ("question", "tool"),
    [
        ("How many students are in Class 7?", "count_students"),
        ("how  many GIRLS in class 9 B", "count_students"),
        ("What\u2019s due this week?", "list_due_tasks"),
        ("Any pending tasks for the office?", "list_due_tasks"),
        # FR-KB-097: the D86 tools that answer a question alone.
        ("How many Class 10 students are SSC-ready?", "get_readiness_summary"),
        ("How many of my students are ready for UDISE+?", "get_readiness_summary"),
        ("Which sections had attendance below 75% this week?", "get_attendance_summary"),
        ("When does our fire NOC expire?", "list_compliance_status"),
        ("Which of our certificates have expired?", "list_compliance_status"),
    ],
)
def test_FR_KB_049_single_tool_questions_go_to_the_light_role(question: str, tool: str) -> None:
    decision = choose_answer_role(enabled(), question, has_history=False)
    assert (decision.role, decision.reason, decision.tool) == ("router", "single_tool", tool)
    assert decision.max_tool_rounds == 1


@pytest.mark.parametrize(
    ("question", "history", "reason"),
    [
        ("When is the SSC exam fee deadline?", False, "no_intent"),
        ("How many students and what is due this week?", False, "several_intents"),
        ("How many students are in Class 7?", True, "has_history"),
        ("How many students " + "in the school records " * 10 + "?", False, "too_long"),
        ("How many students 7వ తరగతి?", False, "telugu_script"),
        ("Somehow many students...", False, "no_intent"),
    ],
)
def test_FR_KB_050_anything_unclear_keeps_the_answer_role(
    question: str, history: bool, reason: str
) -> None:
    decision = choose_answer_role(enabled(), question, has_history=history)
    assert (decision.role, decision.reason, decision.tool) == ("answer", reason, None)


def test_FR_KB_049_enabled_without_intents_is_refused() -> None:
    with pytest.raises(ValidationError, match="single_tool_intents"):
        llm.AnswerRouting(enabled=True)


def test_FR_KB_049_phrases_must_be_lower_case() -> None:
    with pytest.raises(ValidationError, match="lower-case"):
        llm.AnswerRouting(single_tool_intents={"count_students": ("How many",)})
