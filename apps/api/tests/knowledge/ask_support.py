"""Synthetic set-up for the "Ask the school" database and API tests (synthetic data only).

Loaded by path (``--import-mode=importlib``). Builds on ``tests/api/world.py`` (schools A and B,
one member per role, class teacher scoped to 9A, teacher to class X) and
``tests/students/student_world.py`` (students through the real service):

- :func:`install_runtime`: the knowledge runtime with ``SOS_KB_ENABLED`` on, the offline fake
  provider (recording every request body), the fake embeddings provider and the real SQL
  stores, policy and metering ledger.
- :func:`enable_ai`: the per-school ``kb.ask.enabled`` flag (a school override row; the
  control plane owns flags, this is test set-up through the admin engine).
- :func:`text_document`: a plain-text document stored like the documents module stores it,
  then ingested through the REAL pipeline into ``kb.document_chunks`` (SQL chunk store,
  caching tenant embedder).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.knowledge import composition
from app.knowledge.gateway.fake import FakeTransport
from app.knowledge.ingestion.documents_source import DocumentsServiceSource
from app.knowledge.ingestion.extract import DOCX_MIME
from app.knowledge.ingestion.pipeline import DocumentIngestionPipeline
from app.knowledge.store import SqlChunkStore

TESTS = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


W = _load("sos_test_api_world", TESTS / "api" / "world.py")
D = _load("sos_test_documents_support", TESTS / "documents" / "support.py")
SW = _load("sos_test_student_world", TESTS / "students" / "student_world.py")
S = _load("sos_test_ingestion_support", TESTS / "knowledge" / "ingestion_support.py")

ASK_FLAG = "kb.ask.enabled"


def install_runtime(**overrides: Any) -> tuple[composition.Runtime, FakeTransport]:
    transport = overrides.pop("transport", None) or FakeTransport(record=True)
    settings = get_settings().model_copy(update={"kb_enabled": True})
    rt = composition.build_runtime(settings, transport=transport, **overrides)
    composition.set_runtime(rt)
    SW.configure_keyring()
    return rt, transport


def enable_ai(admin: Engine, tenant_id: uuid.UUID, *, enabled: bool = True) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO platform.feature_flags (id, key, tenant_id, enabled) "
                "VALUES (:i, :k, :t, :e) ON CONFLICT (key, tenant_id) "
                "DO UPDATE SET enabled = EXCLUDED.enabled"
            ),
            {"i": uuid.uuid4(), "k": ASK_FLAG, "t": tenant_id, "e": enabled},
        )
    rt = composition.runtime()
    if rt.policy is not None:
        rt.policy.forget()


def text_document(
    admin: Engine,
    school: Any,
    body: str,
    *,
    title: str = "Synthetic circular",
    acl: list[tuple[str, str]] | None = None,
    doc_type: str = "circular",
    sensitivity: str = "C1",
    ingest: bool = True,
) -> tuple[uuid.UUID, uuid.UUID]:
    """A ready DOCX document, one paragraph per line (admin insert + object in the memory
    store, as the documents module stores it), then indexed through the real pipeline."""
    store = D.memory_store()
    data = S.docx("".join(S.p(line) for line in body.splitlines()))
    doc_id, version_id = uuid.uuid4(), uuid.uuid4()
    key = f"t/{school.tenant_id}/docs/{doc_id}/v1/original.docx"
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, sensitivity, "
                "current_version_id, created_by) VALUES (:d, :t, 'circular', :dt, :ti, :s, :v, :u)"
            ),
            {
                "d": doc_id,
                "t": school.tenant_id,
                "dt": doc_type,
                "ti": title,
                "s": sensitivity,
                "v": version_id,
                "u": school.people["owner"].user_id,
            },
        )
        c.execute(
            text(
                "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
                "(:v, :t, :d, 1, :k, :h, :m, :n, 'ready', :u)"
            ),
            {
                "v": version_id,
                "t": school.tenant_id,
                "d": doc_id,
                "k": key,
                "h": hashlib.sha256(data).digest(),
                "m": DOCX_MIME,
                "n": len(data),
                "u": school.people["owner"].user_id,
            },
        )
        for ptype, ref in acl or []:
            c.execute(
                text(
                    "INSERT INTO kb.document_acl (tenant_id, document_id, principal_type, "
                    "principal_ref) VALUES (:t, :d, :pt, :r)"
                ),
                {"t": school.tenant_id, "d": doc_id, "pt": ptype, "r": ref},
            )
    store.put(key, data, DOCX_MIME)
    if ingest:
        assert pipeline().ingest(school.tenant_id, doc_id, version_id) == "indexed"
    return doc_id, version_id


def pipeline() -> DocumentIngestionPipeline:
    return DocumentIngestionPipeline(
        source=DocumentsServiceSource(),
        store=SqlChunkStore(),
        embedder=composition.runtime().embedder,
    )


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def ask(api: Any, who: Any, question: str) -> tuple[Any, list[tuple[str, dict[str, Any]]]]:
    res = api.call(
        who,
        "POST",
        "/api/v1/knowledge/ask",
        json={"question": question, "session_id": str(uuid.uuid4())},
    )
    events = parse_sse(res.text) if res.status_code == 200 else []
    return res, events


def query_row(admin: Engine, school: Any, person: Any) -> uuid.UUID:
    """A logged question of ``person`` (ids and dummy ciphertext only; feedback fixtures)."""
    qid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.queries (id, tenant_id, session_id, user_id, question_ciphertext, "
                "question_hmac, key_version, mode, status) VALUES (:i, :t, :s, :u, :q, :h, 1, "
                "'full', 'answered')"
            ),
            {
                "i": qid,
                "t": school.tenant_id,
                "s": uuid.uuid4(),
                "u": person.user_id,
                "q": b"synthetic-ciphertext",
                "h": hashlib.sha256(qid.bytes).digest(),
            },
        )
    return qid


ALL_ROLES_ACL = [
    ("role", r)
    for r in (
        "owner",
        "principal",
        "office_admin",
        "office_staff",
        "accountant",
        "exam_coordinator",
        "class_teacher",
        "teacher",
        "auditor_readonly",
    )
]
SHARED_TEXT = "Parent-teacher meeting is on 18/10/2026 at 10:00 in the school hall."


def shared_document(admin: Engine, school: Any) -> uuid.UUID:
    """An indexed circular every role of ``school`` can read (one per process)."""
    if "kb_shared_document" not in school.ids:
        school.ids["kb_shared_document"] = text_document(
            admin, school, SHARED_TEXT, title="Parent-teacher meeting", acl=ALL_ROLES_ACL
        )[0]
    value: uuid.UUID = school.ids["kb_shared_document"]
    return value


def chunk_rows(admin: Engine, document_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT version_id, is_latest, acl_roles, acl_sections FROM kb.document_chunks "
                "WHERE document_id = :d ORDER BY chunk_no"
            ),
            {"d": document_id},
        )
        return [dict(r._mapping) for r in rows]


__all__ = [
    "ALL_ROLES_ACL",
    "ASK_FLAG",
    "SHARED_TEXT",
    "SW",
    "D",
    "W",
    "ask",
    "chunk_rows",
    "enable_ai",
    "install_runtime",
    "parse_sse",
    "pipeline",
    "query_row",
    "shared_document",
    "text_document",
]
