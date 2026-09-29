"""``app/insights/rules.yaml`` (versioned configuration; invariant 13) and a school's effective
rule settings (defaults + the school's overrides within bounds). Pure."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Indicator = Literal["attendance", "behaviour", "course"]
RuleKey = Literal[
    "attendance_streak", "attendance_rate", "course_low", "course_decline", "behaviour_concerns"
]
RULE_KEYS: tuple[RuleKey, ...] = (
    "attendance_streak",
    "attendance_rate",
    "course_low",
    "course_decline",
    "behaviour_concerns",
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Bounds(_Model):
    default: int
    min: int = Field(ge=1)
    max: int = Field(le=100)

    @model_validator(mode="after")
    def _ordered(self) -> Bounds:
        if not self.min <= self.default <= self.max:
            raise ValueError("threshold bounds must satisfy min <= default <= max")
        return self


class RuleSpec(_Model):
    indicator: Indicator
    can_disable: bool
    threshold: Bounds
    window: int | None = Field(default=None, ge=7, le=90)
    min_days: int | None = Field(default=None, ge=1, le=60)


class FlagRules(_Model):
    due_days: int = Field(ge=1, le=30)


class NoteRules(_Model):
    categories: tuple[str, ...] = Field(min_length=1)
    max_chars: int = Field(ge=50, le=500)
    max_backdate_days: int = Field(ge=0, le=365)


class ActionRules(_Model):
    kinds: tuple[str, ...] = Field(min_length=1)
    max_note_chars: int = Field(ge=50, le=1000)


class EvaluationRules(_Model):
    history_days: int = Field(ge=30, le=400)


class TimelineRules(_Model):
    attendance_months: int = Field(ge=1, le=24)


class RetentionRules(_Model):
    notes_days: int = Field(ge=365, le=3650)
    closed_flags_days: int = Field(ge=365, le=3650)


class InsightsConfig(_Model):
    version: int = Field(ge=1)
    flags: FlagRules
    rules: dict[RuleKey, RuleSpec]
    notes: NoteRules
    actions: ActionRules
    close_reasons: tuple[str, ...] = Field(min_length=1)
    erase_reasons: tuple[str, ...] = Field(min_length=1)
    evaluation: EvaluationRules
    timeline: TimelineRules
    retention: RetentionRules

    @model_validator(mode="after")
    def _complete(self) -> InsightsConfig:
        if set(self.rules) != set(RULE_KEYS):
            raise ValueError("rules.yaml must configure every rule exactly once")
        if not self.rules["attendance_streak"].threshold.min >= 2:
            raise ValueError("a single absence is never a streak")
        return self


@lru_cache(maxsize=1)
def load_config() -> InsightsConfig:
    raw = yaml.safe_load(resources.files("app.insights").joinpath("rules.yaml").read_text("utf-8"))
    return InsightsConfig.model_validate(raw)


@dataclass(frozen=True, slots=True)
class RuleSetting:
    """One rule as it applies in a school: the school's threshold (within bounds) and switch."""

    key: RuleKey
    indicator: Indicator
    enabled: bool
    threshold: int
    spec: RuleSpec


def effective(
    overrides: Mapping[str, Any], cfg: InsightsConfig | None = None
) -> dict[RuleKey, RuleSetting]:
    """Defaults with the school's stored overrides applied; out-of-bounds or unknown values
    (e.g. after the bounds changed in a newer release) are clamped or ignored, never trusted."""
    cfg = cfg or load_config()
    out: dict[RuleKey, RuleSetting] = {}
    for key in RULE_KEYS:
        spec = cfg.rules[key]
        raw = overrides.get(key)
        own: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        threshold = spec.threshold.default
        value = own.get("threshold")
        if isinstance(value, int) and not isinstance(value, bool):
            threshold = min(max(value, spec.threshold.min), spec.threshold.max)
        enabled = True
        if spec.can_disable and own.get("enabled") is False:
            enabled = False
        out[key] = RuleSetting(key, spec.indicator, enabled, threshold, spec)
    return out


__all__ = [
    "RULE_KEYS",
    "Bounds",
    "Indicator",
    "InsightsConfig",
    "RuleKey",
    "RuleSetting",
    "RuleSpec",
    "effective",
    "load_config",
]
