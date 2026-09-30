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
        # ADR-0033: the cheap Ask conversation roles.
        "followups": 400,
        "summary": 600,
        "memory_screen": 150,
        "query_rewrite": 200,
        "eval_judge": 2000,
    }


def test_FR_KB_011_budget_is_converted_at_the_billing_rate() -> None:
    assert llm.load_llm_config().budget.usd_inr_rate == Decimal("84.00")


def test_opus_5_5_cannot_have_thinking_disabled() -> None:
    data = raw()
    data["roles"]["eval_judge"]["thinking"] = "disabled"
    with pytest.raises(ValidationError, match="cannot disable thinking"):
        llm.LlmConfig.model_validate(data)


def test_effort_only_on_models_that_take_it() -> None:
    data = raw()
    data["roles"]["metadata"]["effort"] = "low"
    with pytest.raises(ValidationError, match="effort"):
        llm.LlmConfig.model_validate(data)


def test_answer_role_keeps_thinking_disabled_for_tool_use_replay() -> None:
    data = raw()
    data["roles"]["answer"]["thinking"] = "provider_default"
    with pytest.raises(ValidationError, match=r"roles\.answer"):
        llm.LlmConfig.model_validate(data)


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
