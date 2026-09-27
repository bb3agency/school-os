"""``models.yaml``: model roles, prices, limits and budget thresholds (ADR-0005; docs/06 §12).

Owned by the gateway package (K4). The gateway reads model IDs, output caps, thinking settings,
client timeouts/retries/circuit breaker, the per-tenant rate limit and the budget thresholds only
from here (``kb.answer_model`` = ``roles.answer.model`` and so on; invariant 13) and must re-check
them against the provider's current documentation when they change; a change needs ``make eval``.
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


ThinkingMode = Literal["disabled", "provider_default"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class RoleConfig(ConfigModel):
    model: str = Field(pattern=MODEL_ID_PATTERN)
    offline_only: bool = False
    """Never used for product traffic (the eval judge)."""
    max_output_tokens: int = Field(ge=1, le=8000)
    """Hard cap on generated tokens per call (docs/07 LLM10; SEC-020)."""
    thinking: ThinkingMode
    """``disabled`` sends ``{"type": "disabled"}``; ``provider_default`` omits the parameter."""
    effort: Effort | None = None
    """``output_config.effort``; only for models whose capabilities allow it."""


class ModelCapabilities(ConfigModel):
    """What a model accepts; a role asking for more is refused at load time (a 400 otherwise)."""

    can_disable_thinking: bool
    forced_tool_choice: bool
    """``tool_choice`` ``any``/``tool``. The gateway never forces a tool; recorded for reviewers."""
    effort: bool


class ModelPrice(ConfigModel):
    """List price per million tokens, for spend per tenant and feature (NFR-CST-001)."""

    input_usd_per_mtok: Decimal = Field(gt=0)
    output_usd_per_mtok: Decimal = Field(gt=0)


class CachePriceMultipliers(ConfigModel):
    """Prompt-cache token prices as multiples of the input price (5-minute cache entries)."""

    write: Decimal = Field(gt=0, le=10)
    read: Decimal = Field(gt=0, le=1)


class Limits(ConfigModel):
    max_tool_rounds: int = Field(ge=1, le=3)
    """ADR-0008 and docs/06 §5: at most 3 tool rounds per answer (SEC-020)."""
    tool_result_context_tokens: int = Field(ge=1000, le=12_000)
    """docs/06 §12: at most 12k tokens of tool results per answer."""
    chars_per_token_estimate: int = Field(ge=1, le=8)
    """Conservative characters-per-token used to check the tool-result context budget."""
    first_token_p95_ms: int = Field(ge=100, le=3000)
    """FR-KB-008: first token <= 3 s p95."""
    full_answer_p95_ms: int = Field(ge=100, le=10_000)
    """FR-KB-008: completion <= 10 s p95."""


class ClientConfig(ConfigModel):
    """Provider client behaviour (ADR-0005; NFR-AVL-004): timeout, retries, circuit breaker."""

    api_base_url: str = Field(pattern=r"^https://[a-z0-9.\-]+(:[0-9]{1,5})?$")
    request_timeout_s: float = Field(gt=0, le=600)
    max_retries: int = Field(ge=0, le=5)
    """Retries after the first attempt, only on 429, 5xx and 529 overloaded."""
    backoff_base_s: float = Field(gt=0, le=10)
    backoff_max_s: float = Field(gt=0, le=60)
    circuit_failure_threshold: int = Field(ge=1, le=100)
    circuit_open_s: float = Field(gt=0, le=3600)

    @model_validator(mode="after")
    def _backoff_order(self) -> ClientConfig:
        if self.backoff_base_s > self.backoff_max_s:
            raise ValueError("backoff_base_s must not exceed backoff_max_s")
        return self


class RateLimit(ConfigModel):
    requests_per_minute_per_tenant: int = Field(ge=1, le=10_000)
    """Provider calls per tenant and feature per minute (docs/07 T15; each tool round counts)."""


class Budget(ConfigModel):
    """Per-tenant monthly budget thresholds (FR-KB-011, NFR-CST-001)."""

    alert_fraction: float = Field(gt=0, lt=1)
    degrade_fraction: float = Field(gt=0, le=1)
    """At this share of the budget, Ask degrades to search-only until reset or top-up."""
    usd_inr_rate: Decimal = Field(gt=0)
    """Converts metered USD spend to the school's INR budget (same rate as platform billing)."""

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
    cache_price_multipliers: CachePriceMultipliers
    capabilities: dict[str, ModelCapabilities]
    limits: Limits
    client: ClientConfig
    rate_limit: RateLimit
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
        for role, config in self.roles.items():
            caps = self.capabilities.get(config.model)
            if caps is None:
                raise ValueError(f"roles.{role}: no capabilities for model {config.model}")
            if config.thinking == "disabled" and not caps.can_disable_thinking:
                raise ValueError(f"roles.{role}: {config.model} cannot disable thinking")
            if config.effort is not None and not caps.effort:
                raise ValueError(f"roles.{role}: {config.model} does not take an effort level")
        if self.roles["answer"].thinking != "disabled":
            # A replayed tool-use turn has no thinking blocks (domain.ModelTurn carries none).
            raise ValueError("roles.answer: thinking must be disabled for the tool-use loop")
        return self


def load_llm_config(path: Path | None = None) -> LlmConfig:
    return _load(path or PATH)


@lru_cache(maxsize=4)
def _load(path: Path) -> LlmConfig:
    return LlmConfig.model_validate(read_yaml(path))
