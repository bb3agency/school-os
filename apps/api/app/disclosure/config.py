"""Disclosure settings (``config.yaml`` next to this module; ADR-0051). Pure module."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

import yaml
from pydantic import BaseModel, ConfigDict, Field


class DisclosureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    views_keep_days: int = Field(ge=1, le=366)
    download_url_ttl_s: int = Field(ge=30, le=300)
    files_keep_days: int = Field(ge=1, le=6)
    history_limit: int = Field(ge=1, le=1000)


@lru_cache(maxsize=1)
def rules() -> DisclosureConfig:
    text = resources.files("app.disclosure").joinpath("config.yaml").read_text("utf-8")
    return DisclosureConfig.model_validate(yaml.safe_load(text))


__all__ = ["DisclosureConfig", "rules"]
