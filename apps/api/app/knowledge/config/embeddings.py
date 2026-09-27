"""``embeddings.yaml``: embeddings candidates, the selection and storage shape (ADR-0006).

Owned by the embeddings package (K2) after the skeleton. ``selected`` stays empty until the
ADR-0006 evaluation (Recall@10 on the Telugu/English/mixed set, storage cost, latency,
residency) picks a candidate; until then only the offline fake provider runs. The selected
candidate's dimensions must equal the ``kb.document_chunks.embedding`` column, so switching
models or dimensions is a re-embedding migration, never a config edit alone (docs/06 §4.6).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from pydantic import Field, model_validator

from app.knowledge.config._base import (
    CONFIG_DIR,
    KEY_PATTERN,
    MODEL_ID_PATTERN,
    ConfigModel,
    read_yaml,
)

PATH: Final = CONFIG_DIR / "embeddings.yaml"
Dimensions = Literal[256, 512, 1024, 2048]


class Storage(ConfigModel):
    dimensions: Dimensions
    """Must match ``kb.document_chunks.embedding`` and ``kb.embedding_cache.embedding``."""
    precision: Literal["halfvec", "vector"]


class SelectionRule(ConfigModel):
    """ADR-0006: prefer the smaller dimension if its recall loss is under this many points."""

    prefer_dimensions: Dimensions
    max_recall_loss_points: float = Field(ge=0, le=100)


class Candidate(ConfigModel):
    provider: Literal["voyage", "self_hosted"]
    """``voyage``: network provider (sub-processor outside India, disclosed; gateway only).
    ``self_hosted``: open-source multilingual model in ap-south-1 (residency, no per-call cost)."""
    model: str | None = Field(default=None, pattern=MODEL_ID_PATTERN)
    """Set by the evaluation; a candidate without a model cannot be selected."""
    dimensions: Dimensions


class EmbeddingsConfig(ConfigModel):
    version: int = Field(ge=1)
    selected: str | None = Field(default=None, pattern=KEY_PATTERN)
    storage: Storage
    query_cache_ttl_s: int = Field(ge=0, le=3600)
    """docs/06 §12: query embeddings are cached for 10 minutes."""
    selection_rule: SelectionRule
    candidates: dict[str, Candidate]

    @model_validator(mode="after")
    def _selection(self) -> EmbeddingsConfig:
        bad = [key for key in self.candidates if not re.fullmatch(KEY_PATTERN, key)]
        if bad:
            raise ValueError(f"candidate names must be keys: {bad}")
        if self.selected is None:
            return self
        candidate = self.candidates.get(self.selected)
        if candidate is None:
            raise ValueError(f"selected {self.selected!r} is not one of the candidates")
        if candidate.model is None:
            raise ValueError(f"selected candidate {self.selected!r} has no model")
        if candidate.dimensions != self.storage.dimensions:
            raise ValueError(
                "selected candidate's dimensions differ from storage.dimensions: change both "
                "through a re-embedding migration (docs/06 §4.6)"
            )
        return self

    @property
    def selected_candidate(self) -> Candidate | None:
        return None if self.selected is None else self.candidates[self.selected]


def load_embeddings_config(path: Path | None = None) -> EmbeddingsConfig:
    return _load(path or PATH)


@lru_cache(maxsize=4)
def _load(path: Path) -> EmbeddingsConfig:
    return EmbeddingsConfig.model_validate(read_yaml(path))
