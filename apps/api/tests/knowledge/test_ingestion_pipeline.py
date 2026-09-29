"""Ingestion pipeline end to end with fakes (docs/06 §4; FR-DOC-006, FR-DOC-007, FR-KB-002,
invariants 1, 4, 5).

A ready version is extracted, redacted, chunked, embedded and indexed with the document's ACL
copies; re-ingesting is idempotent; a new version supersedes the old one; ACL changes and
deletions reach the chunks; excluded or unready documents are never indexed; nothing
Aadhaar-like reaches the embedder or the index, and no document text reaches the logs.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.redaction import contains_full_aadhaar
from app.knowledge.ingestion import pipeline as pipeline_module
from app.knowledge.ingestion.documents_source import DocumentsServiceSource, system_context
from app.knowledge.ingestion.extract import TEXT_MIME
from app.knowledge.ingestion.memory import InMemoryChunkStore
from app.knowledge.ingestion.pipeline import DocumentIngestionPipeline, embedding_text
from app.knowledge.ingestion.ports import ChunkAcl, ChunkStore, DocumentSource
from app.knowledge.interfaces import IngestionPipeline


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


S = _load("sos_test_ingestion_support", Path(__file__).with_name("ingestion_support.py"))
DOC = uuid.UUID("0190e000-0000-7000-8000-0000000000d1")
OTHER_DOC = uuid.UUID("0190e000-0000-7000-8000-0000000000d2")
V1, V2 = S.version(1), S.version(2)


def seeded(w: Any, *, tenant: uuid.UUID = S.TENANT_A, doc: uuid.UUID = DOC, **kw: Any) -> None:
    versions = kw.pop("versions", [V1])
    files = kw.pop("files", {1: S.circular_docx()})
    w.source.put(tenant, S.facts(doc, versions, **kw), files)


def rows(w: Any, doc: uuid.UUID = DOC, tenant: uuid.UUID = S.TENANT_A) -> list[Any]:
    return [r for r in w.store.rows if r.document_id == doc and r.tenant_id == tenant]


# --- happy path -----------------------------------------------------------------------------------


def test_FR_KB_002_ready_docx_is_indexed_with_acl_copies_and_filters() -> None:
    w = S.world()
    acl = [("role", "principal"), ("section", str(S.SECTION_6A)), ("class", str(S.CLASS_7))]
    seeded(w, acl=acl)
    assert w.pipeline.ingest(S.TENANT_A, DOC, V1.id) == "indexed"
    stored = rows(w)
    assert len(stored) >= 3
    first = stored[0]
    assert first.acl == ChunkAcl(
        roles=("principal",), sections=(S.SECTION_6A,), classes=(S.CLASS_7,)
    )
    assert first.filters.doc_type == "circular"
    assert first.filters.sensitivity == "C1"
    assert first.embedding_model == "fake-embed-v1"
    assert all(r.is_latest and r.version_id == V1.id and r.version_no == 1 for r in stored)
    assert [r.item.chunk.chunk_no for r in stored] == list(range(1, len(stored) + 1))
    # Headings, table and pages survive into the chunks.
    chunks = [r.item.chunk for r in stored]
    assert any(c.heading_path[-1:] == ("2. Seating",) and c.is_table for c in chunks)
    assert chunks[-1].page_to == 2
    assert chunks[0].context_header.startswith(
        "[Circular] Half-yearly exam circular · DEO Guntur · 12 Aug 2026"
    )
    # Each vector is the embedding of header + content, made for "document" input.
    (tenant, texts, input_type), *_ = w.embedder.calls
    assert (tenant, input_type) == (S.TENANT_A, "document")
    assert texts == [embedding_text(c) for c in chunks]
    assert all(len(r.item.embedding) == 4 for r in stored)
    # Every transaction was the tenant's own (invariant 1).
    assert set(w.sessions.opened) == {S.TENANT_A}


def test_FR_CIR_001_indexed_hooks_are_injected_not_taken_from_the_process_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pipeline calls only the indexed hooks it was built with: modules registering in the
    process-wide ``INDEXED_HOOKS`` (the circulars module on import) reach the production
    pipeline through the composition root, never a pipeline built over other stores."""
    calls: list[tuple[uuid.UUID, uuid.UUID, str]] = []

    def hook(_session: Any, document_id: uuid.UUID, version_id: uuid.UUID, doc_type: str) -> None:
        calls.append((document_id, version_id, doc_type))

    def exploding(*_: Any) -> None:
        raise AssertionError("a registered hook ran in a pipeline that was not given it")

    monkeypatch.setattr(pipeline_module, "INDEXED_HOOKS", [exploding])
    w = S.world()
    seeded(w)
    assert w.pipeline.ingest(S.TENANT_A, DOC, V1.id) == "indexed"

    hooked = DocumentIngestionPipeline(
        source=w.source,
        store=InMemoryChunkStore(),
        embedder=w.embedder,
        config=S.CONFIG,
        session_factory=w.pipeline._session,
        indexed_hooks=[hook],
    )
    assert hooked.ingest(S.TENANT_A, DOC, V1.id) == "indexed"
    assert calls == [(DOC, V1.id, "circular")]


def test_PRV_013_synthetic_aadhaar_is_masked_before_chunking_and_embedding() -> None:
    w = S.world()
    seeded(w)
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    contents = [r.item.chunk.content for r in rows(w)]
    assert any(S.AADHAAR_MASK in c for c in contents)
    for text in [*contents, *w.embedder.texts]:
        assert not contains_full_aadhaar(text)
        assert S.SYNTHETIC_AADHAAR not in text.replace(" ", "")


def test_PRV_013_aadhaar_in_the_title_never_reaches_header_or_filters() -> None:
    w = S.world()
    seeded(w, title=f"Correction for {S.AADHAAR_SPACED}")
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    for r in rows(w):
        assert not contains_full_aadhaar(r.item.chunk.context_header)
        assert not contains_full_aadhaar(r.filters.title)


def test_plain_text_is_indexed() -> None:
    w = S.world()
    seeded(w, versions=[S.version(1, mime=TEXT_MIME)], files={1: b"Holiday on Friday."})
    assert w.pipeline.ingest(S.TENANT_A, DOC, V1.id) == "indexed"
    assert [r.item.chunk.content for r in rows(w)] == ["Holiday on Friday."]


# --- idempotency and versions ---------------------------------------------------------------------


def test_FR_DOC_006_reingesting_a_version_is_idempotent() -> None:
    w = S.world()
    seeded(w)
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    first = list(w.store.rows)
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    assert w.store.rows == first


def test_FR_DOC_006_new_version_supersedes_and_late_old_version_stays_superseded() -> None:
    w = S.world()
    seeded(w)
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    newer = S.docx(S.p("Revised timings: exams now start at 09:30."))
    seeded(w, versions=[V1, V2], files={1: S.circular_docx(), 2: newer})
    w.pipeline.ingest(S.TENANT_A, DOC, V2.id)
    latest = {r.version_id for r in rows(w) if r.is_latest}
    assert latest == {V2.id}
    assert any(r.version_id == V1.id for r in rows(w))  # kept for version history
    # A retry of the old version arriving late does not make it current again.
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    assert {r.version_id for r in rows(w) if r.is_latest} == {V2.id}


def test_PRV_016_chunks_of_discarded_versions_are_dropped() -> None:
    w = S.world()
    seeded(w)
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    discarded = S.version(1, status="quarantined")
    seeded(w, versions=[discarded, V2], files={2: S.docx(S.p("Redacted copy text."))})
    w.pipeline.ingest(S.TENANT_A, DOC, V2.id)
    assert {r.version_id for r in rows(w)} == {V2.id}


def test_unsupported_current_version_stops_older_chunks_being_latest() -> None:
    w = S.world()
    seeded(w)
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    # XLSX is not indexed yet (docs/06 §4.2); PDF has been since ADR-0027.
    xlsx = S.version(2, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    seeded(w, versions=[V1, xlsx], files={1: S.circular_docx(), 2: b"PK"})
    assert w.pipeline.ingest(S.TENANT_A, DOC, xlsx.id) == "unsupported_type"
    assert not any(r.is_latest for r in rows(w))
    assert w.source.reads == 1  # the XLSX bytes were never read


def test_corrupt_file_fails_with_a_code_and_indexes_nothing() -> None:
    w = S.world()
    seeded(w, files={1: b"PK\x03\x04 not really a docx"})
    assert w.pipeline.ingest(S.TENANT_A, DOC, V1.id) == "corrupt"
    assert rows(w) == []
    assert w.embedder.calls == []


# --- not indexed ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kw", [{"purpose": "evidence"}, {"purpose": "import_file"}, {"sensitivity": "C3"}]
)
def test_PRV_student_records_are_never_indexed(kw: dict[str, Any]) -> None:
    w = S.world()
    seeded(w)
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    assert rows(w)
    seeded(w, **kw)
    assert w.pipeline.ingest(S.TENANT_A, DOC, V1.id) == "excluded"
    assert rows(w) == []
    assert w.source.reads == 1


@pytest.mark.parametrize("status", ["queued", "scanning", "quarantined", "failed"])
def test_FR_DOC_002_unscanned_or_quarantined_versions_are_not_read(status: str) -> None:
    w = S.world()
    seeded(w, versions=[S.version(1, status=status)])
    assert w.pipeline.ingest(S.TENANT_A, DOC, V1.id) == "not_ready"
    assert w.source.reads == 0
    assert rows(w) == []


def test_missing_document_or_version_is_skipped() -> None:
    w = S.world()
    assert w.pipeline.ingest(S.TENANT_A, DOC, V1.id) == "missing"
    seeded(w)
    assert w.pipeline.ingest(S.TENANT_A, DOC, uuid.UUID(int=99)) == "missing"
    assert rows(w) == []


def test_FR_DOC_007_document_deleted_while_embedding_is_not_written() -> None:
    holder: dict[str, Any] = {}
    embedder = S.FakeEmbedder(before=lambda: holder["w"].source.delete(S.TENANT_A, DOC))
    w = S.world(embedder)
    holder["w"] = w
    seeded(w)
    assert w.pipeline.ingest(S.TENANT_A, DOC, V1.id) == "missing"
    assert rows(w) == []


def test_FR_KB_002_acl_changed_while_embedding_is_written_fresh() -> None:
    holder: dict[str, Any] = {}

    def change_acl() -> None:
        holder["w"].source.update(S.TENANT_A, DOC, acl=(("role", "principal"),))

    w = S.world(S.FakeEmbedder(before=change_acl))
    holder["w"] = w
    seeded(w)
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    assert {r.acl for r in rows(w)} == {ChunkAcl(roles=("principal",))}
    assert (S.TENANT_A, DOC) in w.store.locks


# --- ACL refresh and removal ----------------------------------------------------------------------


def test_FR_KB_002_acl_refresh_rewrites_the_copies_on_every_chunk() -> None:
    w = S.world()
    seeded(w)
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    member = uuid.UUID("0190f000-0000-7000-8000-0000000000aa")
    w.source.update(
        S.TENANT_A, DOC, acl=(("membership", str(member)), ("section", str(S.SECTION_6A)))
    )
    w.pipeline.refresh_acl(S.TENANT_A, DOC)
    assert {r.acl for r in rows(w)} == {ChunkAcl(sections=(S.SECTION_6A,), memberships=(member,))}


def test_FR_KB_002_empty_acl_is_copied_as_empty_never_widened() -> None:
    w = S.world()
    seeded(w)
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    w.source.update(S.TENANT_A, DOC, acl=())
    w.pipeline.refresh_acl(S.TENANT_A, DOC)
    assert {r.acl for r in rows(w)} == {ChunkAcl()}


def test_acl_refresh_of_a_deleted_or_excluded_document_removes_its_chunks() -> None:
    w = S.world()
    seeded(w)
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    w.source.update(S.TENANT_A, DOC, sensitivity="C3")
    w.pipeline.refresh_acl(S.TENANT_A, DOC)
    assert rows(w) == []


def test_FR_DOC_007_remove_document_deletes_only_that_documents_chunks() -> None:
    w = S.world()
    seeded(w)
    seeded(w, doc=OTHER_DOC)
    seeded(w, tenant=S.TENANT_B)
    for tenant, doc in ((S.TENANT_A, DOC), (S.TENANT_A, OTHER_DOC), (S.TENANT_B, DOC)):
        w.pipeline.ingest(tenant, doc, V1.id)
    w.pipeline.remove_document(S.TENANT_A, DOC)
    assert rows(w) == []
    assert rows(w, OTHER_DOC)
    assert rows(w, DOC, S.TENANT_B)  # same ids in another school are untouched (invariant 1)
    w.pipeline.remove_document(S.TENANT_A, DOC)  # idempotent


def test_acl_entries_of_unknown_type_are_refused() -> None:
    with pytest.raises(ValueError, match="principal type"):
        ChunkAcl.from_entries([("everyone", "x")])


# --- logs -----------------------------------------------------------------------------------------


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    setup_logging(get_settings(), stream=stream)
    yield stream
    setup_logging(get_settings())


def test_SEC_008_ingestion_logs_ids_and_counts_never_text(log_stream: io.StringIO) -> None:
    w = S.world()
    body = S.p(f"{S.SYNTHETIC_NAME} was admitted; reference {S.AADHAAR_SPACED}.")
    seeded(w, title=f"Admission of {S.SYNTHETIC_NAME}", files={1: S.docx(body)})
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    w.pipeline.refresh_acl(S.TENANT_A, DOC)
    seeded(w, files={1: b"PK\x03\x04 broken"})
    w.pipeline.ingest(S.TENANT_A, DOC, V1.id)
    w.pipeline.remove_document(S.TENANT_A, DOC)
    out = log_stream.getvalue()
    for event in (
        "knowledge.ingest.done",
        "knowledge.ingest.aadhaar_masked",
        "knowledge.acl.refreshed",
        "knowledge.ingest.failed",
        "knowledge.document.removed",
    ):
        assert event in out
    for secret in (S.SYNTHETIC_NAME, "Kommineni", "admitted", S.SYNTHETIC_AADHAAR, "Admission"):
        assert secret not in out


# --- contracts ------------------------------------------------------------------------------------


def test_implementations_satisfy_their_protocols() -> None:
    w = S.world()
    assert isinstance(w.pipeline, IngestionPipeline)
    assert isinstance(InMemoryChunkStore(), ChunkStore)
    assert isinstance(DocumentsServiceSource(), DocumentSource)
    assert isinstance(S.FakeSource(), DocumentSource)


def test_system_context_holds_only_document_read_permissions() -> None:
    ctx = system_context(S.TENANT_A)
    assert ctx.permissions == frozenset({"document.read", "document.manage_acl"})
    assert ctx.scope_for("document.read").school_wide
    assert not ctx.has("document.upload")
