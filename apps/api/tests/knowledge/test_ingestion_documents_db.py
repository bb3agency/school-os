"""Ingestion against the real documents module and database (docs/06 §4; FR-DOC-002, FR-KB-002,
FR-OPS-004, invariants 1 and 4).

``DocumentsServiceSource`` reads a synthetic DOCX through ``documents.service`` only (ready
versions, SHA-256 checked, RLS by the worker's ``tenant_session``); another school cannot reach
it; a version turning ``ready`` enqueues ``kb.version.ready`` in the scan's transaction when the
knowledge feature is on. The chunk store is the in-memory one (the SQL store belongs to the
retrieval package), bound to the real session's tenant context.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import uuid
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.redaction import contains_full_aadhaar
from app.documents import service as documents
from app.documents.scanning import ScanResult, Verdict
from app.knowledge.ingestion import hooks
from app.knowledge.ingestion.documents_source import DocumentsServiceSource
from app.knowledge.ingestion.extract import DOCX_MIME
from app.knowledge.ingestion.memory import InMemoryChunkStore
from app.knowledge.ingestion.pipeline import DocumentIngestionPipeline
from app.knowledge.ingestion.ports import ChunkAcl

pytestmark = pytest.mark.db


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


TESTS = Path(__file__).resolve().parents[1]
S = _load("sos_test_ingestion_support", Path(__file__).with_name("ingestion_support.py"))
D = _load("sos_test_documents_support", TESTS / "documents" / "support.py")
world = D.load_world().world


class SessionTenantStore(InMemoryChunkStore):
    """The in-memory store, keyed by the tenant context of the real session (RLS stand-in)."""

    @staticmethod
    def _tenant(session: Any) -> uuid.UUID:
        value = session.execute(text("SELECT core.current_tenant()")).scalar_one()
        if value is None:
            raise RuntimeError("no tenant context (fail closed)")
        return uuid.UUID(str(value))


class CleanScanner:
    engine = "test-clean"

    def scan(self, chunks: Iterable[bytes]) -> ScanResult:
        for _ in chunks:
            pass
        return ScanResult(Verdict.CLEAN, self.engine)


def docx_document(
    admin: Engine,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    *,
    status: str = "ready",
    acl: list[tuple[str, str]] | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """A synthetic DOCX circular with one version (as the documents module stores it)."""
    store = D.memory_store()
    data = S.circular_docx()
    doc_id, version_id = uuid.uuid4(), uuid.uuid4()
    key = f"t/{tenant_id}/docs/{doc_id}/v1/original.docx"
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, issuer, "
                "sensitivity, current_version_id, created_by) VALUES "
                "(:d, :t, 'circular', 'circular', 'Synthetic exam circular', 'DEO Guntur', 'C1', "
                ":v, :u)"
            ),
            {"d": doc_id, "t": tenant_id, "v": version_id, "u": created_by},
        )
        c.execute(
            text(
                "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
                "(:v, :t, :d, 1, :k, :h, :m, :n, :st, :u)"
            ),
            {
                "v": version_id,
                "t": tenant_id,
                "d": doc_id,
                "k": key,
                "h": hashlib.sha256(data).digest(),
                "m": DOCX_MIME,
                "n": len(data),
                "st": status,
                "u": created_by,
            },
        )
        for ptype, ref in acl or []:
            c.execute(
                text(
                    "INSERT INTO kb.document_acl (tenant_id, document_id, principal_type, "
                    "principal_ref) VALUES (:t, :d, :pt, :r)"
                ),
                {"t": tenant_id, "d": doc_id, "pt": ptype, "r": ref},
            )
    store.put(key, data, DOCX_MIME)
    return doc_id, version_id


def pipeline(store: InMemoryChunkStore) -> DocumentIngestionPipeline:
    return DocumentIngestionPipeline(
        source=DocumentsServiceSource(), store=store, embedder=S.FakeEmbedder(), config=S.CONFIG
    )


def test_FR_DOC_002_ready_docx_is_read_through_documents_service_and_indexed(
    world: Any, admin_engine: Engine
) -> None:
    a = world.a
    owner = a.people["owner"]
    doc_id, version_id = docx_document(
        admin_engine, a.tenant_id, owner.user_id, acl=[("role", "office_admin")]
    )
    store = SessionTenantStore()
    assert pipeline(store).ingest(a.tenant_id, doc_id, version_id) == "indexed"
    assert store.rows
    assert {r.acl for r in store.rows} == {ChunkAcl(roles=("office_admin",))}
    assert {r.tenant_id for r in store.rows} == {a.tenant_id}
    assert all(not contains_full_aadhaar(r.item.chunk.content) for r in store.rows)
    assert all(r.filters.title == "Synthetic exam circular" for r in store.rows)

    # ACL change in the documents module reaches every chunk.
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb.document_acl WHERE document_id = :d"), {"d": doc_id})
    pipeline(store).refresh_acl(a.tenant_id, doc_id)
    assert {r.acl for r in store.rows} == {ChunkAcl()}


def test_invariant_1_another_school_cannot_ingest_the_document(
    world: Any, admin_engine: Engine
) -> None:
    doc_id, version_id = docx_document(
        admin_engine, world.a.tenant_id, world.a.people["owner"].user_id
    )
    store = SessionTenantStore()
    assert pipeline(store).ingest(world.b.tenant_id, doc_id, version_id) == "missing"
    assert store.rows == []


def test_FR_DOC_002_unscanned_version_is_not_read(world: Any, admin_engine: Engine) -> None:
    doc_id, version_id = docx_document(
        admin_engine, world.a.tenant_id, world.a.people["owner"].user_id, status="queued"
    )
    store = SessionTenantStore()
    assert pipeline(store).ingest(world.a.tenant_id, doc_id, version_id) == "not_ready"
    assert store.rows == []


@pytest.mark.parametrize("enabled", [True, False])
def test_FR_OPS_004_scan_to_ready_enqueues_ingestion_when_enabled(
    world: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    monkeypatch.setattr(hooks, "get_settings", lambda: SimpleNamespace(kb_enabled=enabled))
    hooks.install()
    a = world.a
    doc_id, version_id = docx_document(
        admin_engine, a.tenant_id, a.people["owner"].user_id, status="queued"
    )
    verdict = documents.scan_version(
        a.tenant_id, doc_id, version_id, scanner=CleanScanner(), store=D.memory_store()
    )
    assert verdict == "ready"
    events = [
        e
        for e in D.outbox_events(admin_engine, a.tenant_id, hooks.READY_EVENT)
        if e["document_id"] == str(doc_id)
    ]
    expected = [{"document_id": str(doc_id), "version_id": str(version_id)}] if enabled else []
    assert events == expected
