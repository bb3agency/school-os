"""Index-writing functions ingestion calls (docs/06 §4.7-4.8; FR-KB-001, FR-KB-002, SEC-018).

Replace is idempotent and hidden until promotion; promotion flips a document to one version in
one statement; ACL refresh rewrites every chunk's copies; deletion and verified-answer flagging;
the per-tenant embedding cache. All through ``sos_app`` in a tenant session (RLS applies).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.core.db import tenant_session
from app.knowledge import repository as repo
from app.knowledge.domain import Chunk


def _load() -> ModuleType:
    name = "sos_test_kb_retrieval_support"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("retrieval_support.py")
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


R = _load()
pytestmark = pytest.mark.db

FACETS = repo.DocumentFacets(doc_type="circular", sensitivity="C1", issued_on=dt.date(2026, 8, 12))


def _chunks(n: int, tag: str) -> list[repo.EmbeddedChunk]:
    return [
        repo.EmbeddedChunk(
            chunk=Chunk(
                chunk_no=i,
                content=f"synthetic {tag} chunk {i}",
                context_header="[Circular] Synthetic DEO",
                heading_path=("Sub", f"§{i}"),
                page_from=i + 1,
                page_to=i + 1,
                token_count=4,
                language="en",
            ),
            embedding=R.topic_vector(f"{tag}-{i}"),
        )
        for i in range(n)
    ]


@pytest.fixture
def world(admin_engine: Engine, app_engine: Engine) -> Iterator[Any]:
    a, b = R.make_tenant(admin_engine), R.make_tenant(admin_engine)
    doc = R.make_document(admin_engine, a)
    R.add_version(admin_engine, doc)
    R.add_version(admin_engine, doc)
    other = R.make_document(admin_engine, b)
    R.add_version(admin_engine, other)
    yield a, b, doc, other
    R.delete_tenant_data(admin_engine, [a, b])


def _rows(tenant: uuid.UUID, document_id: uuid.UUID) -> list[Any]:
    with tenant_session(tenant) as s:
        return list(
            s.execute(
                text(
                    "SELECT version_id, chunk_no, is_latest, acl_roles, acl_sections, "
                    "acl_memberships, doc_type, sensitivity, issued_on, heading_path, "
                    "embedding_model FROM kb.document_chunks WHERE document_id = :d "
                    "ORDER BY version_id, chunk_no"
                ),
                {"d": document_id},
            ).all()
        )


def test_FR_KB_001_replace_is_idempotent_and_hidden_until_promoted(world: Any) -> None:
    a, _, doc, _ = world
    v1 = doc.versions[0]
    acl = repo.ChunkAcl(roles=frozenset({"teacher"}))
    for _ in range(2):  # a re-run replaces, never duplicates
        with tenant_session(a) as s:
            n = repo.replace_version_chunks(
                s,
                document_id=doc.document_id,
                version_id=v1,
                chunks=_chunks(3, "v1"),
                embedding_model=R.MODEL,
                facets=FACETS,
                acl=acl,
            )
        assert n == 3
    rows = _rows(a, doc.document_id)
    assert [r.chunk_no for r in rows] == [0, 1, 2]
    assert not any(r.is_latest for r in rows)
    assert rows[0].acl_roles == ["teacher"]
    assert rows[0].heading_path == ["Sub", "§0"]
    assert rows[0].issued_on == dt.date(2026, 8, 12)


def test_FR_KB_002_promote_flips_the_document_to_one_version(world: Any) -> None:
    a, _, doc, _ = world
    v1, v2 = doc.versions
    with tenant_session(a) as s:
        for v, tag in ((v1, "v1"), (v2, "v2")):
            repo.replace_version_chunks(
                s,
                document_id=doc.document_id,
                version_id=v,
                chunks=_chunks(2, tag),
                embedding_model=R.MODEL,
                facets=FACETS,
                acl=repo.ChunkAcl(),
            )
        assert repo.promote_version(s, document_id=doc.document_id, version_id=v1) == 2
    with tenant_session(a) as s:
        assert repo.promote_version(s, document_id=doc.document_id, version_id=v2) == 2
    latest = {(r.version_id, r.is_latest) for r in _rows(a, doc.document_id)}
    assert latest == {(v1, False), (v2, True)}
    with tenant_session(a) as s:
        assert repo.demote_document(s, doc.document_id) == 2
    assert not any(r.is_latest for r in _rows(a, doc.document_id))


def test_SEC_018_refresh_acl_rewrites_every_chunk(world: Any) -> None:
    a, _, doc, _ = world
    member = uuid.uuid4()
    section = uuid.uuid4()
    with tenant_session(a) as s:
        for v in doc.versions:
            repo.replace_version_chunks(
                s,
                document_id=doc.document_id,
                version_id=v,
                chunks=_chunks(2, str(v)),
                embedding_model=R.MODEL,
                facets=FACETS,
                acl=repo.ChunkAcl(roles=frozenset({"teacher"})),
            )
        n = repo.refresh_acl(
            s,
            document_id=doc.document_id,
            acl=repo.ChunkAcl(sections=frozenset({section}), memberships=frozenset({member})),
            facets=repo.DocumentFacets(doc_type="policy", sensitivity="C2"),
        )
    assert n == 4
    for r in _rows(a, doc.document_id):
        assert r.acl_roles == []
        assert r.acl_sections == [section]
        assert r.acl_memberships == [member]
        assert (r.doc_type, r.sensitivity, r.issued_on) == ("policy", "C2", None)


def test_delete_version_and_document_chunks(world: Any) -> None:
    a, _, doc, _ = world
    v1, v2 = doc.versions
    with tenant_session(a) as s:
        for v in (v1, v2):
            repo.replace_version_chunks(
                s,
                document_id=doc.document_id,
                version_id=v,
                chunks=_chunks(2, str(v)),
                embedding_model=R.MODEL,
                facets=FACETS,
                acl=repo.ChunkAcl(),
            )
        assert repo.delete_version_chunks(s, v1) == 2
        assert repo.delete_document_chunks(s, doc.document_id) == 2
    assert _rows(a, doc.document_id) == []


def test_ADR_0013_cannot_write_chunks_for_another_schools_version(world: Any) -> None:
    a, _, _, other = world
    with pytest.raises(IntegrityError), tenant_session(a) as s:
        repo.replace_version_chunks(
            s,
            document_id=other.document_id,
            version_id=other.versions[0],
            chunks=_chunks(1, "x"),
            embedding_model=R.MODEL,
            facets=FACETS,
            acl=repo.ChunkAcl(),
        )


def test_other_schools_chunks_are_untouched_by_writes(world: Any, admin_engine: Engine) -> None:
    a, b, _, other = world
    R.add_chunks(admin_engine, other, ["synthetic b"], topic="b", acl_roles=["teacher"])
    with tenant_session(a) as s:
        assert repo.delete_document_chunks(s, other.document_id) == 0
        assert repo.refresh_acl(s, document_id=other.document_id, acl=repo.ChunkAcl()) == 0
        assert repo.demote_document(s, other.document_id) == 0
    assert [r.acl_roles for r in _rows(b, other.document_id)] == [["teacher"]]


def test_replace_refuses_bad_input(world: Any) -> None:
    a, _, doc, _ = world
    dup = _chunks(2, "d")
    dup[1] = repo.EmbeddedChunk(chunk=dup[0].chunk, embedding=dup[1].embedding)
    short = [repo.EmbeddedChunk(chunk=_chunks(1, "s")[0].chunk, embedding=[0.1, 0.2])]
    for chunks in (dup, short):
        with pytest.raises(ValueError, match=r"unique|dimensions"), tenant_session(a) as s:
            repo.replace_version_chunks(
                s,
                document_id=doc.document_id,
                version_id=doc.versions[0],
                chunks=chunks,
                embedding_model=R.MODEL,
                facets=FACETS,
                acl=repo.ChunkAcl(),
            )


def test_docs_06_4_8_deleting_a_document_flags_citing_verified_answers(
    world: Any, admin_engine: Engine
) -> None:
    a, _, doc, _ = world
    unrelated = uuid.uuid4()

    def answer(source_doc: uuid.UUID) -> uuid.UUID:
        vid = uuid.uuid4()
        with tenant_session(a) as s:
            s.execute(
                text(
                    "INSERT INTO kb.verified_answers (id, tenant_id, question_canonical, "
                    "language, answer_text, citations, verified_by, verified_at) VALUES "
                    "(:i, :t, 'q', 'en', 'a', CAST(:c AS jsonb), :u, now())"
                ),
                {
                    "i": vid,
                    "t": a,
                    "c": json.dumps(
                        [{"source": f"sos://doc/{source_doc}/v1#p2", "cited_text": "a"}]
                    ),
                    "u": uuid.uuid4(),
                },
            )
        return vid

    citing, other = answer(doc.document_id), answer(unrelated)
    with tenant_session(a) as s:
        assert repo.flag_verified_answers_citing(s, doc.document_id) == 1
        status: dict[uuid.UUID, str] = dict(
            s.execute(
                text("SELECT id, status FROM kb.verified_answers WHERE tenant_id = :t"), {"t": a}
            ).all()
        )
    assert status == {citing: "needs_review", other: "active"}


def test_ADR_0006_embedding_cache_is_per_tenant(world: Any) -> None:
    a, b, _, _ = world
    h1, h2 = hashlib.sha256(b"one").digest(), hashlib.sha256(b"two").digest()
    v1 = R.topic_vector("one")
    with tenant_session(a) as s:
        assert (
            repo.put_cached_embeddings(s, model=R.MODEL, input_type="query", entries={h1: v1}) == 1
        )
        assert (
            repo.put_cached_embeddings(s, model=R.MODEL, input_type="query", entries={h1: v1}) == 0
        )
        got = repo.get_cached_embeddings(s, model=R.MODEL, input_type="query", digests=[h1, h2])
    assert set(got) == {h1}
    assert got[h1] == pytest.approx(v1, abs=1e-3)  # halfvec precision
    with tenant_session(b) as s:
        assert repo.get_cached_embeddings(s, model=R.MODEL, input_type="query", digests=[h1]) == {}
    with tenant_session(a) as s:
        assert (
            repo.get_cached_embeddings(s, model=R.MODEL, input_type="document", digests=[h1]) == {}
        )
        future = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1)
        assert repo.purge_embedding_cache(s, input_type="query", older_than=future) == 1
