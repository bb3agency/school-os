"""``app/brief/config.yaml`` (versioned configuration; invariant 13). Pure."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Schedule(_Model):
    week_starts_on: Literal["monday"]


class Delivery(_Model):
    in_app_default: bool
    email_default: bool


class BriefConfig(_Model):
    version: int = Field(ge=1)
    schedule: Schedule
    small_cell_min: int = Field(ge=2, le=20)
    deadline_days: int = Field(ge=1, le=60)
    max_profiles: int = Field(ge=1, le=10)
    keep_weeks: int = Field(ge=4, le=520)
    history_limit: int = Field(ge=1, le=200)
    delivery: Delivery


@lru_cache(maxsize=1)
def load_config() -> BriefConfig:
    raw = yaml.safe_load(resources.files("app.brief").joinpath("config.yaml").read_text("utf-8"))
    return BriefConfig.model_validate(raw)


__all__ = ["BriefConfig", "Delivery", "Schedule", "load_config"]
