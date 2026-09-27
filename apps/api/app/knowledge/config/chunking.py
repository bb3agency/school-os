"""``chunking.yaml``: extraction limits and structure-aware chunk sizes (docs/06 §4.2-4.5).

Owned by the chunking/ingestion package (K5). ``extraction`` is read by ``ingestion`` (which
files are indexed and how large they may be); everything else by ``chunking``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from pydantic import Field, model_validator

from app.knowledge.config._base import CONFIG_DIR, ConfigModel, read_yaml

PATH: Final = CONFIG_DIR / "chunking.yaml"

Sensitivity = Literal["C1", "C2", "C3"]


class TokenRange(ConfigModel):
    min: int = Field(ge=1, le=8000)
    max: int = Field(ge=1, le=8000)

    @model_validator(mode="after")
    def _ordered(self) -> TokenRange:
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


class TokenEstimate(ConfigModel):
    """How many tokens a word is assumed to cost (no tokenizer dependency; deterministic)."""

    latin_chars_per_token: float = Field(gt=0, le=16)
    """ASCII letters, digits and punctuation per token."""
    other_graphemes_per_token: float = Field(gt=0, le=16)
    """Grapheme clusters (e.g. Telugu aksharas) of any other script per token."""


class ExtractionConfig(ConfigModel):
    supported_mime_types: tuple[str, ...] = Field(min_length=1)
    excluded_purposes: tuple[str, ...]
    """``kb.documents.purpose`` values that are never indexed (student records, not knowledge)."""
    indexed_sensitivities: tuple[Sensitivity, ...] = Field(min_length=1)
    max_xml_bytes: int = Field(ge=1024, le=512 * 1024 * 1024)
    max_text_chars: int = Field(ge=1000, le=50_000_000)

    @model_validator(mode="after")
    def _safe(self) -> ExtractionConfig:
        for purpose in ("evidence", "import_file"):
            if purpose not in self.excluded_purposes:
                raise ValueError(f"excluded_purposes must keep {purpose!r} (student records)")
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
    token_estimate: TokenEstimate
    language_dominant_share: float = Field(gt=0.5, le=1)
    """A block is ``te``/``en`` when that script has this share of its letters, else ``mixed``."""
    extraction: ExtractionConfig

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
