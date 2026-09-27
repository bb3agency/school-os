"""Where the worker tasks get their :class:`IngestionPipeline` (set by the composition root).

The concrete :class:`ChunkStore` (retrieval package) and :class:`TenantEmbedder` (embeddings
package) are chosen in ``knowledge.service``, which calls :func:`configure` once, e.g.::

    configure(lambda: DocumentIngestionPipeline(
        source=DocumentsServiceSource(), store=<SQL chunk store>, embedder=<tenant embedder>))

Until then a task fails loudly (:class:`PipelineNotConfigured`) instead of dropping work.
"""

from __future__ import annotations

from collections.abc import Callable

from app.knowledge.ingestion.pipeline import DocumentIngestionPipeline


class PipelineNotConfigured(RuntimeError):
    pass


_factory: Callable[[], DocumentIngestionPipeline] | None = None


def configure(factory: Callable[[], DocumentIngestionPipeline] | None) -> None:
    """Set (or, with None, clear) the pipeline factory."""
    global _factory  # noqa: PLW0603 - process-wide composition, set once at start-up
    _factory = factory


def pipeline() -> DocumentIngestionPipeline:
    if _factory is None:
        raise PipelineNotConfigured("knowledge ingestion is not wired (knowledge.service)")
    return _factory()


__all__ = ["PipelineNotConfigured", "configure", "pipeline"]
