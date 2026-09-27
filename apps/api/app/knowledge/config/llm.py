"""``models.yaml``: model roles, prices, limits and budget thresholds (ADR-0005; docs/06 §12).

Owned by the gateway package (K4) after the skeleton. The gateway reads model IDs only from here
(``kb.answer_model`` = ``roles.answer.model`` and so on; invariant 13) and must re-check them
against the provider's current documentation when they change; a change needs ``make eval``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, get_args

from pydantic import Field, model_validator

from app.knowledge.config._base import CONFIG_DIR, MODEL_ID_PATTERN, ConfigModel, read_yaml
from app.knowledge.domain import ModelRole

PATH: Final = CONFIG_DIR / "models.yaml"
ROLES: Final = frozenset(get_args(ModelRole))


class RoleConfig(ConfigModel):
    model: str = Field(pattern=MODEL_ID_PATTERN)
    offline_only: bool = False
    """Never used for product traffic (the eval judge)."""


class ModelPrice(ConfigModel):
    """List price per million tokens, for spend per tenant and feature (NFR-CST-001)."""

    input_usd_per_mtok: Decimal = Field(gt=0)
    output_usd_per_mtok: Decimal = Field(gt=0)


class Limits(ConfigModel):
    max_tool_rounds: int = Field(ge=1, le=3)
    """ADR-0008 and docs/06 §5: at most 3 tool rounds per answer (SEC-020)."""
    tool_result_context_tokens: int = Field(ge=1000, le=12_000)
    """docs/06 §12: at most 12k tokens of tool results per answer."""
    first_token_p95_ms: int = Field(ge=100, le=3000)
    """FR-KB-008: first token <= 3 s p95."""
    full_answer_p95_ms: int = Field(ge=100, le=10_000)
    """FR-KB-008: completion <= 10 s p95."""


class Budget(ConfigModel):
    """Per-tenant monthly budget thresholds (FR-KB-011, NFR-CST-001)."""

    alert_fraction: float = Field(gt=0, lt=1)
    degrade_fraction: float = Field(gt=0, le=1)
    """At this share of the budget, Ask degrades to search-only until reset or top-up."""

    @model_validator(mode="after")
    def _alert_first(self) -> Budget:
        if not self.alert_fraction < self.degrade_fraction:
            raise ValueError("alert_fraction must be below degrade_fraction")
        return self


class AnswerChecks(ConfigModel):
    max_uncited_factual_fraction: float = Field(ge=0, le=1)
    """docs/06 §9 rule 3: above this share of uncited factual sentences, answer search-only."""


class LlmConfig(ConfigModel):
    version: int = Field(ge=1)
    provider: Literal["anthropic"]
    """ADR-0005. Another provider needs an evaluation, a privacy review and an ADR."""
    prices_checked_on: date
    roles: dict[ModelRole, RoleConfig]
    prices: dict[str, ModelPrice]
    limits: Limits
    budget: Budget
    answer_checks: AnswerChecks

    @model_validator(mode="after")
    def _consistent(self) -> LlmConfig:
        missing = sorted(ROLES - set(self.roles))
        if missing:
            raise ValueError(f"roles missing: {missing}")
        for role, config in self.roles.items():
            if config.model not in self.prices:
                raise ValueError(f"roles.{role}: no price for model {config.model}")
            if config.offline_only != (role == "eval_judge"):
                raise ValueError(f"roles.{role}: offline_only is only (and always) the eval judge")
        return self


def load_llm_config(path: Path | None = None) -> LlmConfig:
    return _load(path or PATH)


@lru_cache(maxsize=4)
def _load(path: Path) -> LlmConfig:
    return LlmConfig.model_validate(read_yaml(path))
