"""Accountant and management report configuration (``reports.yaml``; FM-04; FR-FEE-061..075;
invariant 13).

Pure: reads and validates the versioned file next to this module; no database.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_PATH: Final = Path(__file__).with_name("reports.yaml")


class ReportsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    max_range_days: int = Field(ge=1, le=366)
    view_max_rows: int = Field(ge=10, le=5000)
    download_max_rows: int = Field(ge=100, le=100000)
    ageing_days: tuple[int, ...] = Field(min_length=1, max_length=5)
    series_missing_shown: int = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def _ascending(self) -> ReportsConfig:
        days = self.ageing_days
        if any(d < 1 for d in days) or list(days) != sorted(set(days)):
            raise ValueError("ageing_days must be positive and strictly ascending")
        return self


@lru_cache(maxsize=1)
def load_config() -> ReportsConfig:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return ReportsConfig.model_validate(raw)


__all__ = ["CONFIG_PATH", "ReportsConfig", "load_config"]
