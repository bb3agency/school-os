"""The erasure chain: nothing derived from a deleted document can still be retrieved or cited
(FR-DOC-007, FR-KB-030, PRV-009, PRV-010, PRV-016; docs/06 §4.8, docs/08 §7 "Erasure chain").

For each way a document or version stops existing (a person deletes it, a retention job deletes
it, PRV-016 discards a version, an ingestion job queued or running when it went), every
derivative is checked on the real database with the offline fake model:

- chunks with their embeddings and contextual summaries (``kb.document_chunks``);
- the per-school embedding cache (``kb.embedding_cache``, keyed by text digest, so no foreign
  key reaches it: its entries are removed explicitly);
- the exact-repeat answer cache (``kb.queries.cache_invalidated_at``; a repeat is answered
  afresh, "not found in school records");
- verified answers citing it (``needs_review``: never used as a source until a person re-checks);
- conversation history: the earlier answer and its citation are withheld;
- search: nothing is returned.

Each test uses a fresh synthetic school so "not found" means the deleted document was the only
possible source. Offboarding is covered by ``tests/tenancy/test_offboarding_purge.py`` (zero rows
in every tenant table, every file discarded) and Vertex context caches never holding document
text by ``test_gateway_gemini.py`` (structured calls such as ``contextualize`` have no static
prefix). Synthetic data only.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.core.db import tenant_session
from app.documents import service as documents
from app.knowledge import composition, tasks
from app.knowledge.config.llm import load_llm_config
from app.knowledge.embeddings import content_sha256
from app.knowledge.gateway.fake import NOT_FOUND_EN
from app.knowledge.ingestion.documents_source import DocumentsServiceSource
from app.knowledge.ingestion.pipeline import INDEXED_HOOKS, DocumentIngestionPipeline
from app.knowledge.store import SqlChunkStore

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


K = _load("sos_test_ask_support", Path(__file__).with_name("ask_support.py"))
W = K.W
world = W.world
api = W.api

BODY = (
    "Sub: Ibex trek for classes VIII to X\n"
    "The Ibex trek starts on 09/01/2027 from the school gate at 06:30.\n"
    "Each student brings a water bottle and a cap."
)
QUESTION = "When does the Ibex trek start?"


@pytest.fixture
def runtime(world: Any) -> Iterator[Any]:
    """The runtime on the fake provider with contextual chunks ON, so every derivative exists."""
    settings = get_settings().model_copy(
        update={"kb_enabled": True, "kb_contextual_chunks": "on", "telugu_enabled": False}
    )
    rt = composition.build_runtime(settings, transport=K.FakeTransport(record=True))
    composition.set_runtime(rt)
    K.SW.configure_keyring()
    yield rt
    composition.set_runtime(None)


@pytest.fixture
def school(admin_engine: Engine, runtime: Any) -> Any:
    """A fresh school whose only document is the one under test."""
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin_engine, s.tenant_id, ["owner"])
    K.enable_ai(admin_engine, s.tenant_id)
    return s


def _document(admin: Engine, school: Any, *, ingest: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    doc, version = K.text_document(
        admin, school, BODY, title="Ibex trek circular", acl=K.ALL_ROLES_ACL, ingest=ingest
    )
    return doc, version


def _scalar(admin: Engine, sql: str, **params: Any) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def _chunks(admin: Engine, doc: uuid.UUID) -> int:
    return int(
        _scalar(admin, "SELECT count(*) FROM kb.document_chunks WHERE document_id = :d", d=doc)
    )


def _contexts(admin: Engine, doc: uuid.UUID) -> int:
    return int(
        _scalar(
            admin,
            "SELECT count(*) FROM kb.document_chunks "
            "WHERE document_id = :d AND chunk_context <> ''",
            d=doc,
        )
    )


def _cached_vectors(admin: Engine, tenant_id: uuid.UUID) -> int:
    return int(
        _scalar(
            admin,
            "SELECT count(*) FROM kb.embedding_cache WHERE tenant_id = :t "
            "AND input_type = 'document'",
            t=tenant_id,
        )
    )


def _verified_citing(admin: Engine, school: Any, doc: uuid.UUID) -> uuid.UUID:
    vid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.verified_answers (id, tenant_id, question_canonical, language, "
                "answer_text, citations, verified_by, verified_at) VALUES (:i, :t, "
                "'When does the Ibex trek start?', 'en', 'On 09/01/2027.', CAST(:c AS jsonb), "
                ":u, now())"
            ),
            {
                "i": vid,
                "t": school.tenant_id,
                "c": f'[{{"source": "sos://doc/{doc}/v1#p1", "cited_text": "Ibex trek"}}]',
                "u": school.people["owner"].membership_id,
            },
        )
    return vid


def _verified_status(admin: Engine, vid: uuid.UUID) -> str:
    return str(_scalar(admin, "SELECT status FROM kb.verified_answers WHERE id = :i", i=vid))


def _ask(api: Any, who: Any, question: str) -> list[tuple[str, dict[str, Any]]]:
    res = api.call(who, "POST", "/api/v1/knowledge/ask", json={"question": question})
    assert res.status_code == 200, res.text
    events: list[tuple[str, dict[str, Any]]] = K.parse_sse(res.text)
    return events


def _final(events: Sequence[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    return next(d for e, d in events if e == "final")


def _cited(events: Sequence[tuple[str, dict[str, Any]]]) -> list[str]:
    return [str(d["source"]) for e, d in events if e == "citation"]


def _search(api: Any, who: Any) -> list[str]:
    res = api.call(who, "POST", "/api/v1/knowledge/search", json={"query": "Ibex trek"})
    assert res.status_code == 200, res.text
    return [str(r["document_id"]) for r in res.json()["data"]]


class Before:
    """Everything derived from the document exists and a question cites it."""

    def __init__(self, admin: Engine, api: Any, school: Any, doc: uuid.UUID) -> None:
        owner = school.people["owner"]
        assert _chunks(admin, doc) > 0
        assert _contexts(admin, doc) > 0  # contextual summaries were written
        assert _cached_vectors(admin, school.tenant_id) > 0
        assert _search(api, owner) == [str(doc)]
        self.verified = _verified_citing(admin, school, doc)
        events = _ask(api, owner, QUESTION)
        assert any(s.startswith(f"sos://doc/{doc}/") for s in _cited(events))
        self.query_id = events[0][1]["query_id"]
        self.conversation_id = events[0][1]["conversation_id"]


def _assert_erased(admin: Engine, api: Any, school: Any, doc: uuid.UUID, before: Before) -> None:
    owner = school.people["owner"]
    # Chunks, their embeddings and contextual summaries (one row each).
    assert _chunks(admin, doc) == 0
    # The embedding cache holds no vector of the document's text.
    assert _cached_vectors(admin, school.tenant_id) == 0
    # Verified answers citing it are held for a person's review (never a source meanwhile).
    assert _verified_status(admin, before.verified) == "needs_review"
    # The cached answer is never reused.
    invalidated = _scalar(
        admin, "SELECT cache_invalidated_at FROM kb.queries WHERE id = :i", i=before.query_id
    )
    assert invalidated is not None
    # Search and Ask find nothing; the repeat is answered afresh, "not found".
    assert _search(api, owner) == []
    events = _ask(api, owner, QUESTION)
    assert events[0][1]["cached"] is False
    assert _cited(events) == []
    final = _final(events)
    assert final["status"] == "not_found"
    assert final["text"] in (NOT_FOUND_EN, load_llm_config().answer_checks.not_found.en)
    assert "09/01/2027" not in final["text"]
    # Conversation history withholds the earlier answer and its citation.
    res = api.call(owner, "GET", f"/api/v1/knowledge/conversations/{before.conversation_id}")
    assert res.status_code == 200, res.text
    (message,) = res.json()["messages"]
    assert message["answer_withheld"] is True
    assert message["answer"] is None
    assert all(c["withheld"] and c["snippet"] is None for c in message["citations"])
    assert "Ibex" not in res.text.replace(QUESTION, "")


def test_FR_DOC_007_a_person_deleting_a_document_erases_every_derivative(
    admin_engine: Engine, api: Any, school: Any
) -> None:
    doc, _ = _document(admin_engine, school)
    before = Before(admin_engine, api, school, doc)
    res = api.call(school.people["owner"], "DELETE", f"/api/v1/documents/{doc}")
    assert res.status_code == 204, res.text
    _assert_erased(admin_engine, api, school, doc, before)
    # Invariant 7: audited with the delete.
    deleted = W.audit_events(admin_engine, school.tenant_id, "document.deleted")
    assert [str(e["resource_id"]) for e in deleted] == [str(doc)]


def test_PRV_009_a_retention_deletion_erases_every_derivative(
    admin_engine: Engine, api: Any, school: Any
) -> None:
    doc, _ = _document(admin_engine, school)
    before = Before(admin_engine, api, school, doc)
    with tenant_session(school.tenant_id) as s:
        assert documents.delete_for_retention(s, doc, reason="records_sheet_read") is True
    _assert_erased(admin_engine, api, school, doc, before)
    (event,) = W.audit_events(admin_engine, school.tenant_id, "document.deleted")
    assert event["summary"]["reason"] == "records_sheet_read"


def test_PRV_016_a_discarded_version_is_erased_from_the_index_at_once(
    admin_engine: Engine, api: Any, school: Any
) -> None:
    doc, _ = _document(admin_engine, school)
    before = Before(admin_engine, api, school, doc)
    with tenant_session(school.tenant_id) as s:
        assert documents.discard_version(s, doc, 1, "aadhaar_unredactable") is True
    _assert_erased(admin_engine, api, school, doc, before)
    assert W.audit_events(admin_engine, school.tenant_id, "document.version_discarded")


def test_FR_DOC_007_an_ingestion_job_queued_before_the_delete_indexes_nothing(
    admin_engine: Engine, school: Any
) -> None:
    doc, version = _document(admin_engine, school, ingest=False)
    with tenant_session(school.tenant_id) as s:
        documents.delete_for_retention(s, doc, reason="records_sheet_read")
    assert K.pipeline().ingest(school.tenant_id, doc, version) == "missing"
    assert _chunks(admin_engine, doc) == 0
    assert _cached_vectors(admin_engine, school.tenant_id) == 0


class _DeletingEmbedder:
    """Embeds, then the document is deleted before the ingestion writes (a delete committed
    while the job was running between its transactions)."""

    def __init__(self, inner: Any, tenant_id: uuid.UUID, doc: uuid.UUID) -> None:
        self._inner, self._tenant_id, self._doc = inner, tenant_id, doc

    @property
    def model(self) -> str:
        return str(self._inner.model)

    def embed(self, session: Any, tenant_id: uuid.UUID, texts: Any, input_type: Any) -> Any:
        vectors = self._inner.embed(session, tenant_id, texts, input_type)
        with tenant_session(self._tenant_id) as s:
            documents.delete_for_retention(s, self._doc, reason="records_sheet_read")
        return vectors


def test_FR_DOC_007_an_ingestion_running_during_the_delete_keeps_no_vectors(
    admin_engine: Engine, school: Any, runtime: Any
) -> None:
    doc, version = _document(admin_engine, school, ingest=False)
    pipeline = DocumentIngestionPipeline(
        source=DocumentsServiceSource(),
        store=SqlChunkStore(),
        embedder=_DeletingEmbedder(runtime.embedder, school.tenant_id, doc),
        indexed_hooks=INDEXED_HOOKS,
        contextualizer=runtime.contextualizer,
    )
    assert pipeline.ingest(school.tenant_id, doc, version) == "missing"
    assert _chunks(admin_engine, doc) == 0
    assert _cached_vectors(admin_engine, school.tenant_id) == 0


def test_FR_KB_002_a_document_that_may_no_longer_be_indexed_keeps_no_vectors(
    admin_engine: Engine, school: Any
) -> None:
    """Restricted (C3) documents are never indexed (docs/06 §4.9): when one becomes C3, the
    next ingestion removes its chunks and their cached vectors."""
    doc, version = _document(admin_engine, school)
    assert _cached_vectors(admin_engine, school.tenant_id) > 0
    with admin_engine.begin() as c:
        c.execute(text("UPDATE kb.documents SET sensitivity = 'C3' WHERE id = :d"), {"d": doc})
    assert K.pipeline().ingest(school.tenant_id, doc, version) == "excluded"
    assert _chunks(admin_engine, doc) == 0
    assert _cached_vectors(admin_engine, school.tenant_id) == 0


def _cache_row(admin: Engine, tenant_id: uuid.UUID, digest: bytes, *, hours_old: int) -> None:
    vector = "[" + ",".join(["0"] * 1023 + ["1"]) + "]"
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.embedding_cache (tenant_id, model, input_type, content_sha256, "
                "embedding, created_at) VALUES (:t, :m, 'document', :d, CAST(:v AS halfvec), "
                "now() - make_interval(hours => :h))"
            ),
            {"t": tenant_id, "m": "fake-embed-test", "d": digest, "v": vector, "h": hours_old},
        )


def test_PRV_009_the_daily_sweep_removes_vectors_no_chunk_uses(
    admin_engine: Engine, school: Any
) -> None:
    """Vectors cached before deletions removed them (or left by a job that died between
    embedding and writing) are swept once older than the grace period; a vector a chunk still
    uses, and a fresh one an ingestion may be about to use, are kept."""
    _document(admin_engine, school)
    live = _cached_vectors(admin_engine, school.tenant_id)
    assert live > 0
    old, new = content_sha256("gone old"), content_sha256("gone new")
    # The document's own vectors are made old: a chunk still uses them, so they are kept.
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE kb.embedding_cache SET created_at = now() - interval '30 days' "
                "WHERE tenant_id = :t"
            ),
            {"t": school.tenant_id},
        )
    _cache_row(admin_engine, school.tenant_id, old, hours_old=48)
    _cache_row(admin_engine, school.tenant_id, new, hours_old=0)
    result = tasks.purge_orphan_vectors_all()
    assert result["failed"] == 0
    assert result["purged"] >= 1
    assert _cached_vectors(admin_engine, school.tenant_id) == live + 1
    left = _scalar(
        admin_engine,
        "SELECT count(*) FROM kb.embedding_cache WHERE tenant_id = :t AND content_sha256 = :d",
        t=school.tenant_id,
        d=old,
    )
    assert left == 0
