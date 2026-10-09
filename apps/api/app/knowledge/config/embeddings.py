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


class Batching(ConfigModel):
    """How ``TenantEmbedder`` splits texts into provider requests (K2)."""

    max_texts: int = Field(ge=1, le=1000)
    """Texts per request (Voyage accepts up to 1000)."""
    max_chars: int = Field(ge=1000, le=2_000_000)
    """Characters per request: a conservative proxy for the provider's token limit per request
    (Telugu uses more tokens per character than English). A single longer text is sent alone."""


class Retry(ConfigModel):
    """Transient failures (timeouts, 429, 5xx): exponential backoff, capped, with jitter."""

    max_attempts: int = Field(ge=1, le=10)
    base_delay_s: float = Field(gt=0, le=10)
    max_delay_s: float = Field(gt=0, le=120)

    @model_validator(mode="after")
    def _order(self) -> Retry:
        if self.max_delay_s < self.base_delay_s:
            raise ValueError("retry.max_delay_s must be >= retry.base_delay_s")
        return self


class VoyageHttp(ConfigModel):
    """The Voyage embeddings endpoint (network provider in ``knowledge/gateway``)."""

    endpoint: str = Field(pattern=r"^https://[a-z0-9.\-]+(:[0-9]+)?/[A-Za-z0-9/._\-]*$")
    connect_timeout_s: float = Field(gt=0, le=30)
    read_timeout_s: float = Field(gt=0, le=300)


class Fake(ConfigModel):
    """The deterministic offline provider (local/CI, eval stubs); never selected by evaluation."""

    model: str = Field(pattern=MODEL_ID_PATTERN)
    """Stored as ``embedding_model``, so fake vectors are never mistaken for a real model's."""


class EmbeddingsConfig(ConfigModel):
    version: int = Field(ge=1)
    selected: str | None = Field(default=None, pattern=KEY_PATTERN)
    storage: Storage
    query_cache_ttl_s: int = Field(ge=0, le=3600)
    """docs/06 §12: query embeddings are cached for 10 minutes."""
    query_cache_max_entries: int = Field(ge=0, le=100_000)
    """Per process; bounds memory (about 8 KiB per 1024-dim entry)."""
    orphan_vector_grace_hours: int = Field(ge=1, le=168)
    """Daily sweep (docs/08 §7 erasure chain): a cached document vector that no chunk uses is
    deleted once it is older than this. The grace covers an ingestion between embedding and
    writing its chunks."""
    batching: Batching
    retry: Retry
    voyage: VoyageHttp
    fake: Fake
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
