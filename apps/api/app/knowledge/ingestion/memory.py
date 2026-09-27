"""In-memory :class:`ChunkStore` for tests and offline wiring (never used in staging/prod).

Behaves like the SQL store the retrieval package provides: rows keyed by tenant (the session's
tenant, standing in for RLS), replace-by-version, ``is_latest`` flip, ACL rewrite, deletes.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.knowledge.ingestion.ports import ChunkAcl, ChunkFilters, IndexedChunk, VersionIndex


@dataclass(frozen=True, slots=True)
class StoredChunk:
    tenant_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_no: int
    embedding_model: str
    filters: ChunkFilters
    acl: ChunkAcl
    is_latest: bool
    item: IndexedChunk


class InMemoryChunkStore:
    """``session`` must expose ``tenant_id`` (the fake sessions of the tests do)."""

    def __init__(self) -> None:
        self.rows: list[StoredChunk] = []
        self.locks: list[tuple[uuid.UUID, uuid.UUID]] = []

    @staticmethod
    def _tenant(session: Any) -> uuid.UUID:
        tenant_id = getattr(session, "tenant_id", None)
        if not isinstance(tenant_id, uuid.UUID):
            raise RuntimeError("no tenant context (fail closed)")
        return tenant_id

    def _mine(self, session: Any, document_id: uuid.UUID) -> list[StoredChunk]:
        tenant_id = self._tenant(session)
        return [r for r in self.rows if r.tenant_id == tenant_id and r.document_id == document_id]

    def lock_document(self, session: Any, document_id: uuid.UUID) -> None:
        self.locks.append((self._tenant(session), document_id))

    def replace_version(self, session: Any, index: VersionIndex) -> int:
        tenant_id = self._tenant(session)
        self.rows = [
            r
            for r in self.rows
            if not (r.tenant_id == tenant_id and r.version_id == index.version_id)
        ]
        for item in index.chunks:
            self.rows.append(
                StoredChunk(
                    tenant_id=tenant_id,
                    document_id=index.document_id,
                    version_id=index.version_id,
                    version_no=index.version_no,
                    embedding_model=index.embedding_model,
                    filters=index.filters,
                    acl=index.acl,
                    is_latest=index.is_latest,
                    item=item,
                )
            )
        return len(index.chunks)

    def _rewrite(self, session: Any, document_id: uuid.UUID, **changes: Any) -> int:
        mine = {id(r) for r in self._mine(session, document_id)}
        changed = 0
        rows = []
        for row in self.rows:
            new = replace(row, **changes) if id(row) in mine else row
            changed += new != row
            rows.append(new)
        self.rows = rows
        return changed

    def set_latest(self, session: Any, document_id: uuid.UUID, version_id: uuid.UUID) -> int:
        changed = 0
        for version in {r.version_id for r in self._mine(session, document_id)}:
            changed += self._rewrite_version(session, document_id, version, version == version_id)
        return changed

    def _rewrite_version(
        self, session: Any, document_id: uuid.UUID, version_id: uuid.UUID, latest: bool
    ) -> int:
        tenant_id = self._tenant(session)
        changed = 0
        rows = []
        for row in self.rows:
            hit = (
                row.tenant_id == tenant_id
                and row.document_id == document_id
                and row.version_id == version_id
                and row.is_latest != latest
            )
            rows.append(replace(row, is_latest=latest) if hit else row)
            changed += hit
        self.rows = rows
        return changed

    def update_acl(self, session: Any, document_id: uuid.UUID, acl: ChunkAcl) -> int:
        return self._rewrite(session, document_id, acl=acl)

    def delete_versions(
        self, session: Any, document_id: uuid.UUID, version_ids: Sequence[uuid.UUID]
    ) -> int:
        tenant_id = self._tenant(session)
        before = len(self.rows)
        self.rows = [
            r
            for r in self.rows
            if not (
                r.tenant_id == tenant_id
                and r.document_id == document_id
                and r.version_id in version_ids
            )
        ]
        return before - len(self.rows)

    def delete_document(self, session: Any, document_id: uuid.UUID) -> int:
        tenant_id = self._tenant(session)
        before = len(self.rows)
        self.rows = [
            r for r in self.rows if not (r.tenant_id == tenant_id and r.document_id == document_id)
        ]
        return before - len(self.rows)


__all__ = ["InMemoryChunkStore", "StoredChunk"]
