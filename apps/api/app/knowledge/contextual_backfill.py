"""Backfill of contextual chunk headers (docs/06 §4.11; FR-KB-001, FR-KB-011).

Chunks get a context only while ``contextual_chunks`` is on, and only when the gateway accepts
the call. Chunks indexed before the switch was turned on (``context_status = 'none'``) or while a
school's AI budget was used up, its AI switched off or the provider down (``deferred``) are
re-indexed here, through the normal ingestion path (:meth:`DocumentIngestionPipeline.ingest`,
idempotent per version: extraction, chunking, contexts for the chunks that lack one, embeddings
from the per-tenant cache where unchanged, the same ACL-checked write).

Rate limits (``contextual.yaml`` ``backfill``): at most ``documents_per_school_per_run`` per
school and ``documents_per_run`` in total per run; a school whose document comes back with
chunks still ``deferred`` (budget, switch, provider) is skipped for the rest of the run. The
gateway's own per-school rate limit and monthly budget still apply to every call. Does nothing
while contextual chunks are off. Counts and ids only in the result and the logs (invariant 5).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.db import context_free_session, tenant_session
from app.core.logging import get_logger
from app.knowledge import repository as repo
from app.knowledge.config.contextual import Backfill
from app.tenancy import service as tenancy

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.knowledge.ingestion.pipeline import DocumentIngestionPipeline

log = get_logger(__name__)

SessionFactory = Callable[[uuid.UUID], AbstractContextManager["Session"]]


@dataclass(frozen=True, slots=True)
class BackfillResult:
    tenants: int
    documents: int
    stopped: int
    """Schools skipped for the rest of the run (chunks still deferred: budget, switch, outage)."""
    failed: int

    def as_dict(self) -> dict[str, int]:
        return {
            "tenants": self.tenants,
            "documents": self.documents,
            "stopped": self.stopped,
            "failed": self.failed,
        }


def _tenants() -> list[uuid.UUID]:
    with context_free_session() as s:
        return tenancy.list_tenant_ids(s, ("active",))


def run(
    pipeline: DocumentIngestionPipeline,
    limits: Backfill,
    *,
    tenant_ids: Sequence[uuid.UUID] | None = None,
    session_factory: SessionFactory = tenant_session,
) -> BackfillResult:
    """One backfill run over the given schools (default: every active school)."""
    schools = list(tenant_ids) if tenant_ids is not None else _tenants()
    done = stopped = failed = 0
    for tenant_id in schools:
        room = min(limits.documents_per_school_per_run, limits.documents_per_run - done)
        if room <= 0:
            break
        with session_factory(tenant_id) as s:
            pending = repo.documents_needing_context(s, limit=room)
        for document_id, version_id in pending:
            try:
                pipeline.ingest(tenant_id, document_id, version_id)
            except Exception as exc:  # this document only; retried on the next run
                failed += 1
                log.warning(
                    "knowledge.contextual.backfill_failed",
                    tenant_id=tenant_id,
                    resource_type="document",
                    resource_id=document_id,
                    error_type=type(exc).__name__,
                )
                continue
            done += 1
            with session_factory(tenant_id) as s:
                still = repo.documents_needing_context(s, limit=room)
            if (document_id, version_id) in still:
                stopped += 1  # budget used up, AI off or provider down: try next run
                break
    result = BackfillResult(len(schools), done, stopped, failed)
    log.info(
        "knowledge.contextual.backfilled", count=done, outcome="stopped" if stopped else "done"
    )
    return result


__all__ = ["BackfillResult", "run"]
