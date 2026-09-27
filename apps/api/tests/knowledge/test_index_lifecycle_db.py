"""The SQL index adapters and the documents lifecycle hooks on the real database.

- ``SqlChunkStore`` behind the real ingestion pipeline: chunks land in ``kb.document_chunks``
  with the ACL copies, hidden until promoted; a new version replaces the searchable one and
  flags verified answers citing the document (FR-KB-030); ACL changes rewrite the copies.
- ``SqlEmbeddingCache``: document vectors are cached per school and never served to another.
- Lifecycle (FR-DOC-005, FR-DOC-007, docs/06 §4.8): archiving a document hides every chunk from
  retrieval, unarchiving brings the current version back, deleting it removes its chunks in the
  same transaction and flags verified answers citing it.
- The backfill command (dry run by default; ``--apply`` enqueues ``kb.version.ready`` and
  audits per school).

Synthetic data only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.knowledge import backfill, composition
from app.knowledge import repository as repo
from app.knowledge.embeddings import content_sha256
from app.knowledge.ingestion import hooks
from app.knowledge.ingestion.extract import DOCX_MIME
from app.knowledge.store import SqlEmbeddingCache

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

BODY = "Annual day rehearsal is on 14/11/2026 in the auditorium.\nParents are welcome."


@pytest.fixture
def runtime(world: Any) -> Iterator[Any]:
    rt, _ = K.install_runtime()
    yield rt
    composition.set_runtime(None)


def _search(api: Any, who: Any, query: str) -> set[str]:
    res = api.call(who, "POST", "/api/v1/knowledge/search", json={"query": query})
    assert res.status_code == 200, res.text
    return {r["document_id"] for r in res.json()["data"]}


def _verified_citing(admin: Engine, school: Any, doc: uuid.UUID) -> uuid.UUID:
    vid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.verified_answers (id, tenant_id, question_canonical, language, "
                "answer_text, citations, verified_by, verified_at) VALUES (:i, :t, 'Q', 'en', "
                "'A', CAST(:c AS jsonb), :u, now())"
            ),
            {
                "i": vid,
                "t": school.tenant_id,
                "c": f'[{{"source": "sos://doc/{doc}/v1#p1", "cited_text": "rehearsal"}}]',
                "u": school.people["owner"].membership_id,
            },
        )
    return vid


def _status(admin: Engine, vid: uuid.UUID) -> str:
    with admin.connect() as c:
        return str(
            c.execute(
                text("SELECT status FROM kb.verified_answers WHERE id = :i"), {"i": vid}
            ).scalar_one()
        )


def test_FR_KB_002_sql_store_writes_acl_copies_and_promotes(
    world: Any, admin_engine: Engine, runtime: Any
) -> None:
    a = world.a
    doc, version = K.text_document(
        admin_engine, a, BODY, acl=[("section", str(a.ids["section_9a"])), ("role", "principal")]
    )
    rows = K.chunk_rows(admin_engine, doc)
    assert rows
    assert all(r["is_latest"] and r["version_id"] == version for r in rows)
    assert all(r["acl_roles"] == ["principal"] for r in rows)
    assert all(r["acl_sections"] == [a.ids["section_9a"]] for r in rows)
    # Re-running ingestion rewrites, never duplicates (idempotent).
    assert K.pipeline().ingest(a.tenant_id, doc, version) == "indexed"
    assert len(K.chunk_rows(admin_engine, doc)) == len(rows)


def test_FR_KB_030_new_version_replaces_the_searchable_one_and_flags_verified_answers(
    world: Any, admin_engine: Engine, runtime: Any
) -> None:
    a = world.a
    doc, v1 = K.text_document(admin_engine, a, BODY, acl=[("role", "owner")])
    vid = _verified_citing(admin_engine, a, doc)
    data = K.S.docx(K.S.p("Annual day rehearsal moves to 21/11/2026."))
    v2 = uuid.uuid4()
    key = f"t/{a.tenant_id}/docs/{doc}/v2/original.docx"
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
                "(:v, :t, :d, 2, :k, :h, :m, :n, 'ready', :u)"
            ),
            {
                "v": v2,
                "t": a.tenant_id,
                "d": doc,
                "k": key,
                "h": hashlib.sha256(data).digest(),
                "m": DOCX_MIME,
                "n": len(data),
                "u": a.people["owner"].user_id,
            },
        )
        c.execute(
            text("UPDATE kb.documents SET current_version_id = :v WHERE id = :d"),
            {"v": v2, "d": doc},
        )
    K.D.memory_store().put(key, data, DOCX_MIME)
    assert K.pipeline().ingest(a.tenant_id, doc, v2) == "indexed"
    latest = {r["version_id"] for r in K.chunk_rows(admin_engine, doc) if r["is_latest"]}
    assert latest == {v2}
    assert v1 not in latest
    assert _status(admin_engine, vid) == "needs_review"


def test_FR_DOC_005_archive_hides_and_unarchive_restores(
    world: Any, api: Any, admin_engine: Engine, runtime: Any
) -> None:
    a = world.a
    owner = a.people["owner"]
    doc, _ = K.text_document(admin_engine, a, BODY, acl=[("role", "owner")])
    assert str(doc) in _search(api, owner, "annual day rehearsal")

    version = K.D.document_version(admin_engine, doc)
    res = api.call(
        owner, "POST", f"/api/v1/documents/{doc}/archive", headers={"If-Match": f'W/"{version}"'}
    )
    assert res.status_code == 200, res.text
    assert not any(r["is_latest"] for r in K.chunk_rows(admin_engine, doc))
    assert str(doc) not in _search(api, owner, "annual day rehearsal")
    # Re-ingesting an archived document never makes it searchable again.
    current = K.chunk_rows(admin_engine, doc)[0]["version_id"]
    K.pipeline().ingest(a.tenant_id, doc, current)
    assert not any(r["is_latest"] for r in K.chunk_rows(admin_engine, doc))

    version = K.D.document_version(admin_engine, doc)
    res = api.call(
        owner, "POST", f"/api/v1/documents/{doc}/unarchive", headers={"If-Match": f'W/"{version}"'}
    )
    assert res.status_code == 200, res.text
    assert all(r["is_latest"] for r in K.chunk_rows(admin_engine, doc))
    assert str(doc) in _search(api, owner, "annual day rehearsal")


def test_FR_DOC_007_delete_removes_chunks_and_flags_verified_answers(
    world: Any, api: Any, admin_engine: Engine, runtime: Any
) -> None:
    a = world.a
    owner = a.people["owner"]
    doc, _ = K.text_document(admin_engine, a, BODY, acl=[("role", "owner")])
    vid = _verified_citing(admin_engine, a, doc)
    res = api.call(owner, "DELETE", f"/api/v1/documents/{doc}")
    assert res.status_code == 204, res.text
    assert K.chunk_rows(admin_engine, doc) == []
    assert _status(admin_engine, vid) == "needs_review"


def test_ADR_0006_embedding_cache_is_per_school(world: Any, runtime: Any) -> None:
    cache = SqlEmbeddingCache()
    digest = content_sha256("synthetic cached text")
    vector = [0.0] * 1024
    vector[3] = 1.0
    with tenant_session(world.a.tenant_id) as s:
        cache.put_many(s, world.a.tenant_id, "fake-embed-test", {digest: vector})
        assert digest in cache.get_many(s, world.a.tenant_id, "fake-embed-test", [digest])
    with tenant_session(world.b.tenant_id) as s:
        assert cache.get_many(s, world.b.tenant_id, "fake-embed-test", [digest]) == {}
        with pytest.raises(RuntimeError, match="fail closed"):
            cache.get_many(s, world.a.tenant_id, "fake-embed-test", [digest])


def test_backfill_dry_run_changes_nothing_and_apply_enqueues_and_audits(
    world: Any, admin_engine: Engine, runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = world.b
    doc, version = K.text_document(admin_engine, b, BODY, acl=[("role", "owner")], ingest=False)

    def events() -> list[dict[str, Any]]:
        return [
            e
            for e in K.D.outbox_events(admin_engine, b.tenant_id, hooks.READY_EVENT)
            if e["document_id"] == str(doc)
        ]

    dry = backfill.backfill_school(b.tenant_id, apply=False)
    assert dry.documents >= 1
    assert dry.enqueued == 0
    assert events() == []

    applied = backfill.backfill_school(b.tenant_id, apply=True)
    assert applied.enqueued == applied.documents
    assert events() == [{"document_id": str(doc), "version_id": str(version)}]
    audits = W.audit_events(admin_engine, b.tenant_id, "kb.backfill.enqueued")
    assert audits
    assert audits[-1]["summary"] == {"documents": applied.documents}

    monkeypatch.setattr(
        backfill, "get_settings", lambda: runtime.settings.model_copy(update={"kb_enabled": False})
    )
    assert backfill.main(["--tenant", str(b.tenant_id), "--apply"]) == backfill.EXIT_REFUSED


def test_repository_latest_page_texts_reads_only_searchable_chunks(
    world: Any, admin_engine: Engine, runtime: Any
) -> None:
    a = world.a
    doc, version = K.text_document(admin_engine, a, BODY, acl=[("role", "owner")])
    with tenant_session(a.tenant_id) as s:
        assert any("rehearsal" in t for t in repo.latest_page_texts(s, version, 1))
        repo.demote_document(s, doc)
        assert repo.latest_page_texts(s, version, 1) == []
