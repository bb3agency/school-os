"""``chunking.yaml``: structure-aware chunk sizes (docs/06 §4.5).

Owned by the chunking/ingestion package (K5) after the skeleton.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import Field, model_validator

from app.knowledge.config._base import CONFIG_DIR, ConfigModel, read_yaml

PATH: Final = CONFIG_DIR / "chunking.yaml"


class TokenRange(ConfigModel):
    min: int = Field(ge=1, le=8000)
    max: int = Field(ge=1, le=8000)

    @model_validator(mode="after")
    def _ordered(self) -> TokenRange:
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


class ChunkingConfig(ConfigModel):
    version: int = Field(ge=1)
    target_tokens: TokenRange
    overlap_tokens: TokenRange
    table_max_tokens: int = Field(ge=1, le=8000)
    """Tables up to this size stay whole; larger ones split by row groups with the header."""
    contextual_header: bool
    block_markers: tuple[str, ...] = Field(min_length=1)
    """Lines starting with these open a new block (Indian government circulars)."""

    @model_validator(mode="after")
    def _consistent(self) -> ChunkingConfig:
        if self.overlap_tokens.max >= self.target_tokens.min:
            raise ValueError("overlap_tokens.max must be below target_tokens.min")
        if self.table_max_tokens < self.target_tokens.max:
            raise ValueError("table_max_tokens must be at least target_tokens.max")
        return self


def load_chunking_config(path: Path | None = None) -> ChunkingConfig:
    return _load(path or PATH)


@lru_cache(maxsize=4)
def _load(path: Path) -> ChunkingConfig:
    return ChunkingConfig.model_validate(read_yaml(path))
