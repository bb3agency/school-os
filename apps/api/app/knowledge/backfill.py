"""Operator command: queue ingestion for documents that were ready before knowledge was on.

    python -m app.knowledge.backfill [--tenant <school id> ...] [--apply]

Documents that passed the malware scan before ``SOS_KB_ENABLED`` was switched on never produced
a ``kb.version.ready`` event (the ingestion hooks enqueue only while it is on). For each school
(all active schools, or the ones named) this finds every ACTIVE document whose current version
is ``ready`` and whose purpose and sensitivity are indexed (``chunking.yaml`` ``extraction``),
and, with ``--apply``, enqueues ``kb.version.ready`` for it through the outbox in one
transaction per school, with one audit event ``kb.backfill.enqueued`` (actor ``system``; the
school's id and the count only). Without ``--apply`` (the default) it is a dry run: it prints
the counts and changes nothing.

Safe to repeat: ingestion is idempotent per version. Output carries school ids and counts only,
never titles or names (invariant 5). Refuses ``--apply`` while ``SOS_KB_ENABLED`` is off.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from app.audit import service as audit
from app.core.config import get_settings
from app.core.db import context_free_session, tenant_session
from app.core.logging import get_logger
from app.documents import service as documents
from app.knowledge.config.chunking import load_chunking_config
from app.knowledge.ingestion.documents_source import system_context
from app.knowledge.ingestion.hooks import READY_EVENT
from app.ops import service as ops
from app.tenancy import service as tenancy

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

PAGE: Final = 200
EXIT_REFUSED: Final = 2


@dataclass(frozen=True, slots=True)
class SchoolBackfill:
    tenant_id: uuid.UUID
    documents: int
    enqueued: int


def ready_versions(session: Session, tenant_id: uuid.UUID) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """``(document_id, version_id)`` of every indexable document of the school."""
    rules = load_chunking_config().extraction
    ctx = system_context(tenant_id)
    found: list[tuple[uuid.UUID, uuid.UUID]] = []
    before: uuid.UUID | None = None
    while True:
        page, before = documents.list_documents(
            session, ctx, limit=PAGE, before_id=before, status="active"
        )
        for doc in page:
            version = doc.current_version
            if (
                version is not None
                and version.status == "ready"
                and doc.purpose not in rules.excluded_purposes
                and doc.sensitivity in rules.indexed_sensitivities
            ):
                found.append((doc.id, version.id))
        if before is None:
            return found


def backfill_school(tenant_id: uuid.UUID, *, apply: bool) -> SchoolBackfill:
    with tenant_session(tenant_id) as s:
        found = ready_versions(s, tenant_id)
        if apply and found:
            for document_id, version_id in found:
                ops.enqueue_event(
                    s, READY_EVENT, {"document_id": document_id, "version_id": version_id}
                )
            audit.record(
                s,
                action="kb.backfill.enqueued",
                resource_type="tenant",
                resource_id=tenant_id,
                summary={"documents": len(found)},
                actor_type="system",
            )
    enqueued = len(found) if apply else 0
    log.info(
        "knowledge.backfill.school",
        tenant_id=tenant_id,
        count=len(found),
        outcome="enqueued" if apply else "dry_run",
    )
    return SchoolBackfill(tenant_id, len(found), enqueued)


def school_ids(named: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    if named:
        return list(dict.fromkeys(named))
    with context_free_session() as s:
        return tenancy.list_tenant_ids(s)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.knowledge.backfill", description=__doc__)
    parser.add_argument("--tenant", type=uuid.UUID, action="append", default=[])
    parser.add_argument(
        "--apply", action="store_true", help="enqueue ingestion (default: dry run, no changes)"
    )
    args = parser.parse_args(argv)
    if args.apply and not get_settings().kb_enabled:
        sys.stderr.write("refused: SOS_KB_ENABLED is off; switch knowledge on first\n")
        return EXIT_REFUSED
    total = 0
    for tenant_id in school_ids(args.tenant):
        result = backfill_school(tenant_id, apply=args.apply)
        total += result.documents
        verb = "enqueued" if args.apply else "would enqueue"
        sys.stdout.write(f"{tenant_id}: {verb} {result.documents} document(s)\n")
    mode = "applied" if args.apply else "dry run (use --apply to enqueue)"
    sys.stdout.write(f"total: {total} document(s); {mode}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())


__all__ = ["SchoolBackfill", "backfill_school", "main", "ready_versions", "school_ids"]
