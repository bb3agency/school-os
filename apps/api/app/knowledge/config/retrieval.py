"""``retrieval.yaml``: hybrid retrieval settings (docs/06 §6; FR-KB-001).

Owned by the retrieval package (K3) after the skeleton. Boost factors, decay and rerank settings
are added here by K3 once evaluation tunes them (``make eval``); nothing is tuned by preference.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from pydantic import Field, model_validator

from app.knowledge.config._base import CONFIG_DIR, ConfigModel, read_yaml

PATH: Final = CONFIG_DIR / "retrieval.yaml"


class Fusion(ConfigModel):
    method: Literal["rrf"]
    k: int = Field(ge=1, le=1000)
    """Reciprocal Rank Fusion constant (docs/06 §6: k = 60)."""


class Branch(ConfigModel):
    limit: int = Field(ge=1, le=1000)


class FullTextBranch(Branch):
    ts_config: Literal["simple"]
    """No stemming: Telugu and code-mixed text (docs/06 §11)."""


class Branches(ConfigModel):
    vector: Branch
    full_text: FullTextBranch
    trigram: Branch


class Diversity(ConfigModel):
    max_chunks_per_document: int = Field(ge=1, le=50)
    merge_adjacent_same_page: bool


class Boosts(ConfigModel):
    recency_when_latest_intent: bool
    verified_answers: bool


class Toggle(ConfigModel):
    enabled: bool


class RetrievalConfig(ConfigModel):
    version: int = Field(ge=1)
    fusion: Fusion
    final_k: int = Field(ge=1, le=100)
    branches: Branches
    hnsw_ef_search: int = Field(ge=1, le=1000)
    """``SET LOCAL hnsw.ef_search`` per query (docs/06 §6; tune by evaluation)."""
    diversity: Diversity
    boosts: Boosts
    rerank: Toggle
    translated_query_fusion: Toggle

    @model_validator(mode="after")
    def _k_fits(self) -> RetrievalConfig:
        widest = max(b.limit for b in (self.branches.vector, self.branches.full_text))
        if self.final_k > widest:
            raise ValueError("final_k exceeds every candidate list's limit")
        return self


def load_retrieval_config(path: Path | None = None) -> RetrievalConfig:
    return _load(path or PATH)


@lru_cache(maxsize=4)
def _load(path: Path) -> RetrievalConfig:
    return RetrievalConfig.model_validate(read_yaml(path))
