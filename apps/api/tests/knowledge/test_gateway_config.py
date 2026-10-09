"""Gateway settings in models.yaml (invariant 13; docs/06 §12 as built; K4).

The lead's 2026-09-27 values are pinned (a change is a reviewed config change with ``make
eval``), and the loader refuses role settings a model would reject with a 400: turning thinking
off on a model that cannot (Opus 5.5), an effort level on a model without one (Haiku 4.5), or
thinking in the answer role (replayed tool-use turns carry no thinking blocks).
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.knowledge.config import llm
from app.knowledge.config._base import CONFIG_DIR


def raw() -> dict[str, Any]:
    data = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return copy.deepcopy(data)


def test_NFR_AVL_004_client_values_chosen_by_the_lead() -> None:
    client = llm.load_llm_config().client
    assert client.api_base_url == "https://api.anthropic.com"
    assert client.request_timeout_s == 60
    assert client.max_retries == 2
    assert client.circuit_failure_threshold == 5
    assert client.circuit_open_s == 60
    assert llm.load_llm_config().rate_limit.requests_per_minute_per_tenant == 30


def test_SEC_020_output_caps_per_role() -> None:
    caps = {role: c.max_output_tokens for role, c in llm.load_llm_config().roles.items()}
    assert caps == {
        "answer": 1500,
        "router": 300,
        "metadata": 500,
        "translation": 1500,
        "extraction": 2000,
        "circular": 2000,
        "notice": 1200,
        "contextualize": 2000,
        # ADR-0034: the cheap Ask conversation roles.
        "followups": 400,
        "summary": 600,
        "memory_screen": 150,
        "query_rewrite": 200,
        "eval_judge": 2000,
    }


def test_FR_KB_011_budget_is_converted_at_the_billing_rate() -> None:
    # FBIL USD/INR reference rate of 28 Sep 2026 (docs/16 §11, §19 Q17; reviewed 2026-10-03).
    assert llm.load_llm_config().budget.usd_inr_rate == Decimal("95.97")


def test_opus_5_5_cannot_have_thinking_disabled() -> None:
    data = raw()
    assert data["roles"]["eval_judge"]["fallback"]["model"] == "claude-opus-5-5"
    data["roles"]["eval_judge"]["fallback"]["thinking"] = "disabled"
    with pytest.raises(ValidationError, match="cannot disable thinking"):
        llm.LlmConfig.model_validate(data)


def test_gemini_3_cannot_have_thinking_disabled() -> None:
    data = raw()
    role = data["roles"]["eval_judge"]
    role["thinking"] = "disabled"
    del role["thinking_level"]
    with pytest.raises(ValidationError, match="cannot disable thinking"):
        llm.LlmConfig.model_validate(data)


def test_effort_only_on_models_that_take_it() -> None:
    data = raw()
    data["roles"]["metadata"]["effort"] = "low"
    with pytest.raises(ValidationError, match="effort"):
        llm.LlmConfig.model_validate(data)
    data = raw()
    data["roles"]["metadata"]["fallback"]["effort"] = "low"  # Haiku 4.5 has no effort level
    with pytest.raises(ValidationError, match="effort"):
        llm.LlmConfig.model_validate(data)


def test_answer_role_keeps_thinking_disabled_for_tool_use_replay() -> None:
    """Anthropic: a replayed tool-use turn carries no thinking blocks, so no thinking."""
    data = raw()
    data["roles"]["answer"]["fallback"]["thinking"] = "provider_default"
    with pytest.raises(ValidationError, match=r"roles\.answer: thinking must be disabled"):
        llm.LlmConfig.model_validate(data)


def test_answer_role_may_think_only_on_a_model_that_replays_thought_signatures() -> None:
    """Gemini: thinking in the tool loop is allowed only because thought signatures are
    replayed (ToolCall.signature); a model without them must disable thinking."""
    data = raw()
    assert data["roles"]["answer"]["thinking"] == "level"
    llm.LlmConfig.model_validate(data)
    model = data["roles"]["answer"]["model"]
    data["capabilities"][model]["replays_thought_signatures"] = False
    with pytest.raises(ValidationError, match=r"roles\.answer: thinking must be disabled"):
        llm.LlmConfig.model_validate(data)


def test_ADR_0033_a_role_model_must_belong_to_its_provider() -> None:
    data = raw()
    data["roles"]["answer"]["model"] = "claude-sonnet-5"  # provider stays gemini
    with pytest.raises(ValidationError, match="is a anthropic model, not gemini"):
        llm.LlmConfig.model_validate(data)


def test_ADR_0033_a_role_without_provider_gets_the_default_provider() -> None:
    data = raw()
    role = data["roles"]["router"]
    del role["provider"]
    assert llm.LlmConfig.model_validate(data).roles["router"].provider == "gemini"


def test_ADR_0033_thinking_levels_only_where_the_model_takes_them() -> None:
    data = raw()
    data["roles"]["router"]["thinking_level"] = "minimal"  # unverified on Vertex: not listed
    with pytest.raises(ValidationError, match="thinking level minimal"):
        llm.LlmConfig.model_validate(data)
    data = raw()
    data["roles"]["router"]["fallback"]["thinking"] = "level"
    data["roles"]["router"]["fallback"]["thinking_level"] = "low"
    with pytest.raises(ValidationError, match="gemini setting"):
        llm.LlmConfig.model_validate(data)


def test_ADR_0033_only_the_offline_judge_may_leave_the_india_region() -> None:
    config = llm.load_llm_config()
    assert [r for r, c in config.roles.items() if c.location is not None] == ["eval_judge"]
    data = raw()
    data["roles"]["answer"]["location"] = "global"
    with pytest.raises(ValidationError, match="only an offline role may set location"):
        llm.LlmConfig.model_validate(data)


def test_ADR_0033_images_only_for_a_vision_role() -> None:
    config = llm.load_llm_config()
    assert [r for r, c in config.roles.items() if c.accepts_images] == ["extraction"]
    data = raw()
    model = data["roles"]["extraction"]["model"]
    data["capabilities"][model]["vision"] = False
    with pytest.raises(ValidationError, match="does not take images"):
        llm.LlmConfig.model_validate(data)


def test_ADR_0033_every_role_has_an_evaluated_anthropic_fallback() -> None:
    """The switch-back is config only: the pre-ADR-0033 models and settings, unchanged."""
    fallback = llm.load_llm_config().use_fallback()
    assert {r: (c.provider, c.model, c.thinking) for r, c in fallback.roles.items()} == {
        "answer": ("anthropic", "claude-sonnet-5", "disabled"),
        "router": ("anthropic", "claude-haiku-4-5-20251001", "disabled"),
        "metadata": ("anthropic", "claude-haiku-4-5-20251001", "disabled"),
        "translation": ("anthropic", "claude-haiku-4-5-20251001", "disabled"),
        "extraction": ("anthropic", "claude-haiku-4-5-20251001", "disabled"),
        "circular": ("anthropic", "claude-haiku-4-5-20251001", "disabled"),
        "notice": ("anthropic", "claude-sonnet-5", "disabled"),
        # ADR-0035 contextual chunk headers: the model the feature was built and tested with.
        "contextualize": ("anthropic", "claude-haiku-4-5-20251001", "disabled"),
        # ADR-0034 conversation roles: the models the backend was built and tested with.
        "followups": ("anthropic", "claude-haiku-4-5-20251001", "disabled"),
        "summary": ("anthropic", "claude-haiku-4-5-20251001", "disabled"),
        "memory_screen": ("anthropic", "claude-haiku-4-5-20251001", "disabled"),
        "query_rewrite": ("anthropic", "claude-haiku-4-5-20251001", "disabled"),
        "eval_judge": ("anthropic", "claude-opus-5-5", "provider_default"),
    }
    assert fallback.roles["eval_judge"].effort == "low"
    assert fallback.roles["eval_judge"].location is None
    one = llm.load_llm_config().use_fallback(["notice"])
    assert one.roles["notice"].provider == "anthropic"
    assert one.roles["answer"].provider == "gemini"


def test_ADR_0033_the_judge_is_stronger_than_the_answer_model() -> None:
    """Avoid self-grading as far as possible: a different, higher-priced model grades."""
    config = llm.load_llm_config()
    judge, answer = config.roles["eval_judge"].model, config.roles["answer"].model
    assert judge != answer
    assert config.prices[judge].output_usd_per_mtok > config.prices[answer].output_usd_per_mtok


def test_every_role_model_has_capabilities() -> None:
    data = raw()
    del data["capabilities"]["claude-sonnet-5"]
    with pytest.raises(ValidationError, match="capabilities"):
        llm.LlmConfig.model_validate(data)


@pytest.mark.parametrize("url", ["http://api.anthropic.com", "https://evil.example/path", ""])
def test_invariant_10_endpoint_is_a_plain_https_origin(url: str) -> None:
    data = raw()
    data["client"]["api_base_url"] = url
    with pytest.raises(ValidationError, match="api_base_url"):
        llm.LlmConfig.model_validate(data)


@pytest.mark.parametrize("tokens", [0, 8001])
def test_SEC_020_output_cap_is_bounded(tokens: int) -> None:
    data = raw()
    data["roles"]["answer"]["max_output_tokens"] = tokens
    with pytest.raises(ValidationError, match="max_output_tokens"):
        llm.LlmConfig.model_validate(data)
