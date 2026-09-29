"""``circulars.yaml``: prompts and limits for circular reading and notice drafting (M4).

docs/06 §4.10, §10.4-10.5; FR-CIR-002, FR-CIR-003, FR-NOTICE-001..004. Invariant 13: the prompt
id/version and every limit are read from here; the model and output cap from ``models.yaml``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import Field

from app.knowledge.config._base import CONFIG_DIR, KEY_PATTERN, ConfigModel, read_yaml

PATH: Final = CONFIG_DIR / "circulars.yaml"


class PromptRef(ConfigModel):
    id: str = Field(pattern=KEY_PATTERN)
    version: int = Field(ge=1, le=9999)


class ReadingConfig(ConfigModel):
    prompt: PromptRef
    max_passages: int = Field(ge=1, le=200)
    max_input_chars: int = Field(ge=1000, le=100_000)
    max_deadlines: int = Field(ge=1, le=50)
    max_title_chars: int = Field(ge=20, le=500)
    max_details_chars: int = Field(ge=50, le=2000)
    max_summary_chars: int = Field(ge=100, le=2000)
    max_field_chars: int = Field(ge=20, le=500)
    max_quote_chars: int = Field(ge=20, le=2000)
    snippet_chars: int = Field(ge=40, le=1000)


class NoticeConfig(ConfigModel):
    prompt: PromptRef
    max_input_chars: int = Field(ge=1000, le=50_000)
    max_staff_text_chars: int = Field(ge=200, le=20_000)
    max_title_chars: int = Field(ge=20, le=300)
    max_body_chars: int = Field(ge=200, le=5000)


class CircularsConfig(ConfigModel):
    version: int = Field(ge=1)
    reading: ReadingConfig
    notice: NoticeConfig


def load_circulars_config(path: Path | None = None) -> CircularsConfig:
    return _load(path or PATH)


@lru_cache(maxsize=4)
def _load(path: Path) -> CircularsConfig:
    return CircularsConfig.model_validate(read_yaml(path))


__all__ = [
    "CircularsConfig",
    "NoticeConfig",
    "PromptRef",
    "ReadingConfig",
    "load_circulars_config",
]
