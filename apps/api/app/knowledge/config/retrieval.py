"""``retrieval.yaml``: hybrid retrieval settings (docs/06 §6; FR-KB-001).

Owned by the retrieval package (K3). Boost factors and decay ship NEUTRAL (factor 1.0: no effect)
until evaluation tunes them (``make eval``); nothing is tuned by preference. The ACL predicate is
code (``knowledge.retrieval.acl``), never configuration (invariant 8).
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


class VectorBranch(Branch):
    iterative_scan: Literal["off", "relaxed_order", "strict_order"]
    """``SET LOCAL hnsw.iterative_scan`` (pgvector >= 0.8): keep scanning the shared HNSW graph
    until ``limit`` rows pass the tenant/ACL filter (docs/05 §11)."""
    max_scan_tuples: int = Field(ge=100, le=1_000_000)
    """``SET LOCAL hnsw.max_scan_tuples``: upper bound on an iterative scan."""


class FullTextBranch(Branch):
    ts_config: Literal["simple"]
    """No stemming: Telugu and code-mixed text (docs/06 §11)."""
    match: Literal["any_term", "websearch"]
    """``any_term``: OR of the query's lexemes, ranked by ``ts_rank_cd`` (a question rarely
    contains every word of the answer); ``websearch``: ``websearch_to_tsquery`` (AND)."""


class TrigramBranch(Branch):
    word_similarity_threshold: float = Field(gt=0.0, le=1.0)
    """``SET LOCAL pg_trgm.word_similarity_threshold`` for ``:q <% context_header``."""


class Branches(ConfigModel):
    vector: VectorBranch
    full_text: FullTextBranch
    trigram: TrigramBranch


class Diversity(ConfigModel):
    max_chunks_per_document: int = Field(ge=1, le=50)
    merge_adjacent_same_page: bool


class Boosts(ConfigModel):
    recency_when_latest_intent: bool
    recency_max_factor: float = Field(ge=1.0, le=10.0)
    """Multiplier for the newest ``issued_on`` among the candidates; 1.0 = neutral."""
    recency_half_life_days: int = Field(ge=1, le=3650)
    """The extra weight halves for every this many days older than the newest candidate."""
    verified_answers: bool
    verified_answer_factor: float = Field(ge=1.0, le=10.0)
    """Multiplier for ``doc_type = 'verified_answer'`` chunks; 1.0 = neutral."""


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
        if self.hnsw_ef_search < self.branches.vector.limit:
            raise ValueError("hnsw_ef_search must be at least the vector branch limit")
        return self


def load_retrieval_config(path: Path | None = None) -> RetrievalConfig:
    return _load(path or PATH)


@lru_cache(maxsize=4)
def _load(path: Path) -> RetrievalConfig:
    return RetrievalConfig.model_validate(read_yaml(path))
