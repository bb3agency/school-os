"""Which ``EmbeddingsProvider`` runs: ``SOS_KB_PROVIDER_MODE`` plus ``embeddings.yaml``.

- ``fake`` (local/CI; refused in staging/prod by ``Settings``): the offline
  :class:`~app.knowledge.embeddings.fake.FakeEmbeddingsProvider`, whatever is selected.
- ``live``: the candidate named by ``selected``. Nothing selected (the ADR-0006 evaluation has
  not run), a candidate without a model, a dimension other than the storage column, or a
  provider kind without an implementation all refuse with
  :class:`EmbeddingsNotConfiguredError`, so a live deployment cannot start on a guess.

Network providers live in ``knowledge/gateway`` (the only place with outbound model traffic),
which this package may not import; the composition root (``knowledge.service``) passes their
factories in ``network``, keyed by ``Candidate.provider`` (e.g. ``{"voyage": ...}``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.core.config import KnowledgeProviderMode
from app.knowledge.config.embeddings import Candidate, EmbeddingsConfig
from app.knowledge.embeddings.fake import FakeEmbeddingsProvider
from app.knowledge.interfaces import EmbeddingsProvider

ProviderFactory = Callable[[Candidate], EmbeddingsProvider]


class EmbeddingsNotConfiguredError(RuntimeError):
    """Live embeddings were requested but no usable provider is configured (fail closed)."""


def select_embeddings_provider(
    mode: KnowledgeProviderMode,
    config: EmbeddingsConfig,
    *,
    network: Mapping[str, ProviderFactory],
) -> EmbeddingsProvider:
    if mode is KnowledgeProviderMode.FAKE:
        return FakeEmbeddingsProvider(model=config.fake.model, dimensions=config.storage.dimensions)
    candidate = config.selected_candidate
    if candidate is None or candidate.model is None:
        raise EmbeddingsNotConfiguredError(
            "live embeddings need embeddings.yaml `selected` with a model, chosen by the "
            "ADR-0006 evaluation"
        )
    if candidate.dimensions != config.storage.dimensions:  # also refused by the loader
        raise EmbeddingsNotConfiguredError("selected dimensions differ from storage")
    factory = network.get(candidate.provider)
    if factory is None:
        raise EmbeddingsNotConfiguredError(
            f"no implementation for embeddings provider {candidate.provider!r}"
        )
    provider = factory(candidate)
    if provider.dimensions != config.storage.dimensions or provider.model != candidate.model:
        raise EmbeddingsNotConfiguredError(
            f"provider {provider.name!r} does not match the selected candidate"
        )
    return provider
