"""``retrieval.yaml``: hybrid retrieval settings (docs/06 §6; FR-KB-001).

Owned by the retrieval package (K3). Boost factors and decay ship NEUTRAL (factor 1.0: no effect)
until evaluation tunes them (``make eval``); nothing is tuned by preference. The ACL predicate is
code (``knowledge.retrieval.acl``), never configuration (invariant 8).

Contextual retrieval and reranking (docs/06 §4.11, §6 as built; PO approval 2026-09-30) ship
OFF: ``contextual_chunks`` and ``rerank.provider``. The only per-environment switches are the
``SOS_KB_CONTEXTUAL_CHUNKS`` and ``SOS_KB_RERANK`` settings (``RetrievalConfig.with_overrides``);
sizes, budgets and model IDs always come from this file (invariant 13).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from pydantic import Field, field_validator, model_validator

from app.knowledge.config._base import CONFIG_DIR, MODEL_ID_PATTERN, ConfigModel, read_yaml

PATH: Final = CONFIG_DIR / "retrieval.yaml"

OnOff = Literal["off", "on"]
RerankProvider = Literal["off", "voyage", "vertex"]
"""``vertex`` (Vertex AI ranking) is a named candidate without an implementation yet: selecting
it in live mode refuses to start (docs/06 §6 as built)."""


def _on_off(value: object) -> object:
    """YAML 1.1 reads a bare ``off``/``on`` as a boolean; accept both spellings."""
    if value is True:
        return "on"
    if value is False:
        return "off"
    return value


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


class VoyageRerank(ConfigModel):
    endpoint: str = Field(pattern=r"^https://[a-z0-9.\-]+(:[0-9]{1,5})?(/[A-Za-z0-9._\-]+)*$")
    model: str | None = Field(default=None, pattern=MODEL_ID_PATTERN)
    """Chosen by evaluation (docs/06 §13.6); live mode refuses to start while it is null."""
    connect_timeout_s: float = Field(gt=0, le=30)


class Rerank(ConfigModel):
    provider: RerankProvider
    candidates: int = Field(ge=2, le=150)
    """Fused, ACL-filtered candidates whose text is sent to the reranker."""
    keep: int = Field(ge=1, le=100)
    """Passages kept after reranking (the caller's k still caps it)."""
    latency_budget_ms: int = Field(ge=50, le=10_000)
    """Provider timeout; a slower or failed call keeps the RRF order."""
    max_passage_chars: int = Field(ge=200, le=20_000)
    include_context: bool
    """Send the context header and chunk context before the passage text."""
    voyage: VoyageRerank

    @field_validator("provider", mode="before")
    @classmethod
    def _provider(cls, value: object) -> object:
        return _on_off(value)

    @model_validator(mode="after")
    def _sizes(self) -> Rerank:
        if self.keep > self.candidates:
            raise ValueError("rerank.keep exceeds rerank.candidates")
        return self

    @property
    def enabled(self) -> bool:
        return self.provider != "off"


class RetrievalConfig(ConfigModel):
    version: int = Field(ge=1)
    fusion: Fusion
    final_k: int = Field(ge=1, le=100)
    branches: Branches
    hnsw_ef_search: int = Field(ge=1, le=1000)
    """``SET LOCAL hnsw.ef_search`` per query (docs/06 §6; tune by evaluation)."""
    diversity: Diversity
    boosts: Boosts
    contextual_chunks: OnOff
    """``on``: chunk contexts are made at ingestion and searched (docs/06 §4.11)."""
    rerank: Rerank
    translated_query_fusion: Toggle

    @field_validator("contextual_chunks", mode="before")
    @classmethod
    def _contextual(cls, value: object) -> object:
        return _on_off(value)

    @property
    def contextual(self) -> bool:
        return self.contextual_chunks == "on"

    @model_validator(mode="after")
    def _k_fits(self) -> RetrievalConfig:
        widest = max(b.limit for b in (self.branches.vector, self.branches.full_text))
        if self.final_k > widest:
            raise ValueError("final_k exceeds every candidate list's limit")
        if self.hnsw_ef_search < self.branches.vector.limit:
            raise ValueError("hnsw_ef_search must be at least the vector branch limit")
        lists = (self.branches.vector, self.branches.full_text, self.branches.trigram)
        if self.rerank.candidates > sum(b.limit for b in lists):
            raise ValueError("rerank.candidates exceeds the candidate lists")
        return self

    def with_overrides(
        self, *, contextual_chunks: OnOff | None = None, rerank: RerankProvider | None = None
    ) -> RetrievalConfig:
        """Apply the per-environment switches (``SOS_KB_CONTEXTUAL_CHUNKS``, ``SOS_KB_RERANK``);
        None keeps the file's value."""
        update: dict[str, object] = {}
        if contextual_chunks is not None:
            update["contextual_chunks"] = contextual_chunks
        if rerank is not None:
            update["rerank"] = self.rerank.model_copy(update={"provider": rerank})
        return self.model_copy(update=update) if update else self


def load_retrieval_config(path: Path | None = None) -> RetrievalConfig:
    return _load(path or PATH)


@lru_cache(maxsize=4)
def _load(path: Path) -> RetrievalConfig:
    return RetrievalConfig.model_validate(read_yaml(path))
