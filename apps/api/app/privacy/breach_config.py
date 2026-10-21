"""Breach register configuration and drafts (``breach_templates.yaml``; ADR-0050; FR-DPDP-034..036).

Pure module (no database or web): clocks, category labels and the app copies of the breach
runbook's templates B (families) and C (Board outline), filled by :func:`fill`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal[
    "identity",
    "contact",
    "aadhaar_partial",
    "academic",
    "attendance",
    "financial",
    "documents",
    "insights",
    "staff",
    "other",
]
CATEGORIES: Final[tuple[str, ...]] = (
    "identity",
    "contact",
    "aadhaar_partial",
    "academic",
    "attendance",
    "financial",
    "documents",
    "insights",
    "staff",
    "other",
)
DraftKey = Literal["family_notice", "board_outline"]
ReminderKey = Literal["board_24h", "report_60h"]
PLACEHOLDER: Final = re.compile(r"\{\{([a-z_]+)\}\}")
NOT_SET: Final = "[not set]"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Reminders(_Model):
    board_24h: int = Field(ge=1, le=72)
    report_60h: int = Field(ge=1, le=72)


class Clocks(_Model):
    board_notice_hours: int = Field(ge=1, le=72)
    board_report_hours: int = Field(ge=1, le=240)
    reminders: Reminders


class Template(_Model):
    title: str = Field(min_length=5, max_length=200)
    body: str = Field(min_length=20, max_length=4000)


class BreachConfig(_Model):
    template_version: str = Field(min_length=3, max_length=40)
    clocks: Clocks
    sweep_interval_seconds: int = Field(ge=10, le=3600)
    max_affected: int = Field(ge=1, le=20000)
    category_labels: dict[str, str]
    templates: dict[DraftKey, Template]

    @field_validator("category_labels")
    @classmethod
    def _all_categories(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != set(CATEGORIES):
            raise ValueError("category_labels must name exactly the category codes")
        return value


def parse_config(raw: object) -> BreachConfig:
    return BreachConfig.model_validate(raw)


@lru_cache(maxsize=1)
def load_config() -> BreachConfig:
    raw = yaml.safe_load(
        resources.files("app.privacy").joinpath("breach_templates.yaml").read_text("utf-8")
    )
    return parse_config(raw)


def fill(text: str, values: Mapping[str, str | None]) -> str:
    """Replace ``{{name}}`` placeholders; a value the school has not set prints ``[not set]``
    and blocks nothing (FR-DPDP-002 convention). Unknown placeholders print ``[not set]`` too."""

    def one(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return value if value else NOT_SET

    return PLACEHOLDER.sub(one, text)


__all__ = [
    "CATEGORIES",
    "NOT_SET",
    "BreachConfig",
    "Category",
    "DraftKey",
    "ReminderKey",
    "fill",
    "load_config",
]
