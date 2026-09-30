"""Which :class:`~app.knowledge.interfaces.Reranker` runs, if any (docs/06 §6; FR-KB-001).

- ``rerank.provider: off`` (the default; ``SOS_KB_RERANK`` overrides per environment): None,
  retrieval keeps the RRF order.
- ``SOS_KB_PROVIDER_MODE=fake`` (local/CI; refused in staging/prod by ``Settings``): the offline
  :class:`~app.knowledge.rerank.fake.FakeReranker`, whichever provider is named, so no passage
  leaves the process.
- ``live``: the named provider's factory (the composition root passes network factories from
  ``knowledge/gateway``, e.g. ``{"voyage": build_voyage_reranker}``). A provider without an
  implementation (``vertex`` today) or without an evaluated model refuses with
  :class:`RerankerNotConfiguredError`, so a live deployment cannot start on a guess.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.core.config import KnowledgeProviderMode
from app.knowledge.config.retrieval import Rerank
from app.knowledge.interfaces import Reranker
from app.knowledge.rerank.fake import FakeReranker

RerankerFactory = Callable[[Rerank], Reranker]


class RerankerNotConfiguredError(RuntimeError):
    """Reranking is on in live mode but no usable provider is configured (fail closed)."""


def select_reranker(
    mode: KnowledgeProviderMode,
    config: Rerank,
    *,
    network: Mapping[str, RerankerFactory],
) -> Reranker | None:
    if not config.enabled:
        return None
    if mode is KnowledgeProviderMode.FAKE:
        return FakeReranker()
    factory = network.get(config.provider)
    if factory is None:
        raise RerankerNotConfiguredError(
            f"no implementation for rerank provider {config.provider!r}"
        )
    return factory(config)


__all__ = ["RerankerFactory", "RerankerNotConfiguredError", "select_reranker"]
