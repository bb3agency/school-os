"""Synthetic data for the retrieval tests (docs/06 §6; synthetic only, invariant 11).

Loaded by each ``test_retrieval_*.py`` through :func:`load` (no shared conftest, so the other M2
packages can add theirs). Seeding uses the admin engine (test setup only); every assertion reads
through ``sos_app`` in a ``tenant_session``, exactly like production, so RLS applies.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.base import Executable
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.elements import ClauseElement

from app.knowledge.domain import AclKeys, RetrievalQuery, SearchFilters
from app.knowledge.models import EMBEDDING_DIMENSIONS, vector_literal

DIM = EMBEDDING_DIMENSIONS
MODEL = "synthetic-embed-1"


def topic_vector(topic: str, jitter: float = 0.0, seed: int = 0) -> list[float]:
    """A unit vector pointing mostly at ``topic`` (deterministic; nearby for small jitter)."""
    rng = random.Random(f"{topic}:{seed}")  # synthetic test data, not security relevant
    base = int.from_bytes(hashlib.sha256(topic.encode()).digest()[:4], "big") % DIM
    vec = [rng.uniform(-jitter, jitter) for _ in range(DIM)]
    vec[base] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec]


def query(
    text_: str,
    topic: str,
    *,
    k: int = 50,
    filters: SearchFilters | None = None,
    prefer_latest: bool = False,
) -> RetrievalQuery:
    return RetrievalQuery(
        texts=(text_,),
        vectors=(tuple(topic_vector(topic)),),
        k=k,
        filters=filters or SearchFilters(),
        prefer_latest=prefer_latest,
    )


def keys(
    *,
    roles: Iterable[str] = (),
    sections: Iterable[uuid.UUID] = (),
    classes: Iterable[uuid.UUID] = (),
    membership: uuid.UUID | None = None,
    school_wide: bool = False,
    sees_all: bool = False,
    read_sensitive: bool = False,
) -> AclKeys:
    return AclKeys(
        roles=frozenset(roles),
        section_ids=frozenset(sections),
        class_ids=frozenset(classes),
        membership_id=membership or uuid.uuid4(),
        school_wide=school_wide,
        sees_all=sees_all,
        read_sensitive=read_sensitive,
    )


@dataclass
class Doc:
    tenant_id: uuid.UUID
    document_id: uuid.UUID
    versions: list[uuid.UUID] = field(default_factory=list)
    chunks: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)
    """version id -> chunk ids"""

    @property
    def latest_chunks(self) -> list[uuid.UUID]:
        return self.chunks[self.versions[-1]]


def make_tenant(admin: Engine) -> uuid.UUID:
    tid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text("INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, :n, 'active')"),
            {"i": tid, "c": f"kbr-{tid.hex[:12]}", "n": "Synthetic Retrieval School"},
        )
    return tid


def make_document(
    admin: Engine,
    tenant_id: uuid.UUID,
    *,
    title: str = "Synthetic circular",
    doc_type: str = "circular",
    sensitivity: str = "C1",
    issued_on: dt.date | None = None,
) -> Doc:
    doc = Doc(tenant_id, uuid.uuid4())
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, issued_on, "
                "sensitivity, created_by) VALUES (:i, :t, 'circular', :dt, :ti, :io, :s, :u)"
            ),
            {
                "i": doc.document_id,
                "t": tenant_id,
                "dt": doc_type,
                "ti": title,
                "io": issued_on,
                "s": sensitivity,
                "u": uuid.uuid4(),
            },
        )
    return doc


def add_version(admin: Engine, doc: Doc) -> uuid.UUID:
    vid = uuid.uuid4()
    n = len(doc.versions) + 1
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
                "(:i, :t, :d, :n, :k, :h, 'application/pdf', 100, 'ready', :u)"
            ),
            {
                "i": vid,
                "t": doc.tenant_id,
                "d": doc.document_id,
                "n": n,
                "k": f"t/{doc.tenant_id}/docs/{doc.document_id}/v{n}/original.pdf",
                "h": hashlib.sha256(vid.bytes).digest(),
                "u": uuid.uuid4(),
            },
        )
    doc.versions.append(vid)
    doc.chunks[vid] = []
    return vid


def add_chunks(
    admin: Engine,
    doc: Doc,
    contents: Sequence[str],
    *,
    topic: str,
    header: str = "",
    latest: bool = True,
    acl_roles: Sequence[str] = (),
    acl_sections: Sequence[uuid.UUID] = (),
    acl_classes: Sequence[uuid.UUID] = (),
    acl_memberships: Sequence[uuid.UUID] = (),
    pages: Sequence[int] | None = None,
    jitter: float = 0.05,
) -> list[uuid.UUID]:
    """Insert chunks for the document's newest version (admin: bypasses RLS for setup)."""
    vid = doc.versions[-1]
    with admin.begin() as c:
        meta = c.execute(
            text("SELECT doc_type, sensitivity, issued_on FROM kb.documents WHERE id = :d"),
            {"d": doc.document_id},
        ).one()
        if latest:
            c.execute(
                text("UPDATE kb.document_chunks SET is_latest = false WHERE document_id = :d"),
                {"d": doc.document_id},
            )
        ids = []
        start = len(doc.chunks[vid])
        for i, content in enumerate(contents):
            cid = uuid.uuid4()
            page = pages[i] if pages is not None else i + 1
            c.execute(
                text(
                    "INSERT INTO kb.document_chunks (id, tenant_id, document_id, version_id, "
                    "chunk_no, page_from, page_to, context_header, content, token_count, "
                    "embedding, embedding_model, doc_type, issued_on, sensitivity, acl_roles, "
                    "acl_sections, acl_classes, acl_memberships, is_latest) VALUES "
                    "(:i, :t, :d, :v, :n, :p, :p, :h, :c, 10, CAST(:e AS halfvec(1024)), :m, "
                    ":dt, :io, :s, :ar, :asec, :acl, :am, :l)"
                ),
                {
                    "i": cid,
                    "t": doc.tenant_id,
                    "d": doc.document_id,
                    "v": vid,
                    "n": start + i,
                    "p": page,
                    "h": header,
                    "c": content,
                    "e": vector_literal(topic_vector(topic, jitter, seed=start + i)),
                    "m": MODEL,
                    "dt": meta.doc_type,
                    "io": meta.issued_on,
                    "s": meta.sensitivity,
                    "ar": list(acl_roles),
                    "asec": list(acl_sections),
                    "acl": list(acl_classes),
                    "am": list(acl_memberships),
                    "l": latest,
                },
            )
            ids.append(cid)
    doc.chunks[vid].extend(ids)
    return ids


def delete_tenant_data(admin: Engine, tenant_ids: Iterable[uuid.UUID]) -> None:
    ids = list(tenant_ids)
    with admin.begin() as c:
        for table in (
            "kb.verified_answers",
            "kb.queries",
            "kb.embedding_cache",
            "kb.document_chunks",
        ):
            c.execute(text(f"DELETE FROM {table} WHERE tenant_id = ANY(:t)"), {"t": ids})
        c.execute(
            text("UPDATE kb.documents SET current_version_id = NULL WHERE tenant_id = ANY(:t)"),
            {"t": ids},
        )
        c.execute(text("DELETE FROM kb.documents WHERE tenant_id = ANY(:t)"), {"t": ids})
        c.execute(text("DELETE FROM core.tenants WHERE id = ANY(:t)"), {"t": ids})


# --- EXPLAIN ----------------------------------------------------------------------------------


class Explain(Executable, ClauseElement):
    """``EXPLAIN (FORMAT JSON) <statement>`` with the statement's own bind processing."""

    inherit_cache = False

    def __init__(self, statement: ClauseElement, analyze: bool = False) -> None:
        self.statement = statement
        self.analyze = analyze


@compiles(Explain, "postgresql")
def _explain(element: Explain, compiler: SQLCompiler, **kw: Any) -> str:
    options = "ANALYZE, FORMAT JSON" if element.analyze else "FORMAT JSON"
    return f"EXPLAIN ({options}) " + compiler.process(element.statement, **kw)


def plan_nodes(plan: Any) -> list[dict[str, Any]]:
    """Flatten an EXPLAIN (FORMAT JSON) result into its plan nodes."""
    root = plan[0]["Plan"] if isinstance(plan, list) else plan["Plan"]
    out: list[dict[str, Any]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(node.get("Plans", []))
    return out
