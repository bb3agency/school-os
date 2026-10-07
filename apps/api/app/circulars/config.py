"""``app/circulars/config.yaml`` (versioned configuration; invariant 13). Pure."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReadingRules(_Model):
    max_attempts: int = Field(ge=1, le=10)


class ReminderRules(_Model):
    days_before_due: int = Field(ge=0, le=30)


class TaskRules(_Model):
    max_title_chars: int = Field(ge=20, le=200)
    max_details_chars: int = Field(ge=100, le=2000)
    default_title_from_circular: str = Field(min_length=1, max_length=200)
    """Neutral title of a task confirmed from a circular suggestion (DL-08)."""


class NoticeRules(_Model):
    max_title_chars: int = Field(ge=20, le=300)
    max_body_chars: int = Field(ge=200, le=5000)
    render_keep_days: int = Field(ge=1, le=6)
    """Below the 7-day bucket rule for exports/ (docs/05 §13)."""
    image_width_px: int = Field(ge=480, le=2000)


class CircularsRules(_Model):
    version: int = Field(ge=1)
    reading: ReadingRules
    reminders: ReminderRules
    tasks: TaskRules
    notices: NoticeRules


@lru_cache(maxsize=1)
def rules() -> CircularsRules:
    raw = yaml.safe_load(
        resources.files("app.circulars").joinpath("config.yaml").read_text("utf-8")
    )
    return CircularsRules.model_validate(raw)


__all__ = ["CircularsRules", "NoticeRules", "ReminderRules", "TaskRules", "rules"]
