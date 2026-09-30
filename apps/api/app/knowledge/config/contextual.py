"""``contextual.yaml``: contextual chunk headers at ingestion (docs/06 §4.11; FR-KB-001).

Owned by the ingestion package. Whether contexts are made and used is ``retrieval.yaml``
``contextual_chunks`` (with the ``SOS_KB_CONTEXTUAL_CHUNKS`` override); the model and output cap
are the ``contextualize`` role in ``models.yaml``; the prompt is a versioned file (invariant 13).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import Field, model_validator

from app.knowledge.config._base import CONFIG_DIR, KEY_PATTERN, ConfigModel, read_yaml

PATH: Final = CONFIG_DIR / "contextual.yaml"


class PromptRef(ConfigModel):
    id: str = Field(pattern=KEY_PATTERN)
    version: int = Field(ge=1, le=9999)

    @property
    def label(self) -> str:
        """``<id>.v<version>``: stored in ``kb.document_chunks.context_prompt``."""
        return f"{self.id}.v{self.version}"


class Backfill(ConfigModel):
    every_minutes: int = Field(ge=5, le=24 * 60)
    documents_per_school_per_run: int = Field(ge=1, le=1000)
    documents_per_run: int = Field(ge=1, le=10_000)


class ContextualConfig(ConfigModel):
    version: int = Field(ge=1)
    prompt: PromptRef
    chunks_per_call: int = Field(ge=1, le=20)
    max_document_chars: int = Field(ge=2000, le=400_000)
    min_chars: int = Field(ge=1, le=200)
    max_chars: int = Field(ge=50, le=1000)
    """Also the database limit's order of magnitude (``chunk_context`` <= 1000 characters)."""
    generic_words: frozenset[str]
    backfill: Backfill

    @model_validator(mode="after")
    def _consistent(self) -> ContextualConfig:
        if self.min_chars >= self.max_chars:
            raise ValueError("min_chars must be below max_chars")
        if any(w != w.casefold() or not w.strip() for w in self.generic_words):
            raise ValueError("generic_words must be lower case and non-empty")
        if self.backfill.documents_per_school_per_run > self.backfill.documents_per_run:
            raise ValueError("documents_per_school_per_run exceeds documents_per_run")
        return self


def load_contextual_config(path: Path | None = None) -> ContextualConfig:
    return _load(path or PATH)


@lru_cache(maxsize=4)
def _load(path: Path) -> ContextualConfig:
    return ContextualConfig.model_validate(read_yaml(path))


__all__ = ["Backfill", "ContextualConfig", "PromptRef", "load_contextual_config"]
