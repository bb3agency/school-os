"""``gaps.yaml``: limits of the knowledge-gap report (FA-06; FR-KB-081..FR-KB-095; docs/06 §8.1).

Invariant 13: retention, look-back, batch size and the redaction rule for stored questions are
read from here. Pure (no database).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import Field

from app.knowledge.config._base import CONFIG_DIR, ConfigModel, read_yaml

PATH: Final = CONFIG_DIR / "gaps.yaml"


class GapsConfig(ConfigModel):
    version: int = Field(ge=1)
    verified: bool
    source: str = Field(min_length=10)
    retention_days: int = Field(ge=1, le=365)
    lookback_hours: int = Field(ge=1, le=24 * 30)
    collect_every_minutes: int = Field(ge=5, le=24 * 60)
    batch_size: int = Field(ge=10, le=5000)
    max_question_chars: int = Field(ge=40, le=1000)
    mask_digit_runs_from: int = Field(ge=1, le=12)


def parse_gaps_config(data: dict[str, object]) -> GapsConfig:
    return GapsConfig.model_validate(data)


@lru_cache(maxsize=1)
def load_gaps_config(path: Path = PATH) -> GapsConfig:
    return parse_gaps_config(read_yaml(path))


__all__ = ["PATH", "GapsConfig", "load_gaps_config", "parse_gaps_config"]
