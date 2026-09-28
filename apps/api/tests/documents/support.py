"""Test support for documents: an in-memory object store and synthetic file builders.

Loaded with :func:`load` (pytest runs with ``--import-mode=importlib``), also by the security
suites. Synthetic content only.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import io
import sys
import urllib.parse
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

from app.documents import storage
from app.documents.storage import ObjectChanged, ObjectHead, OpenedObject, PresignedPost

EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-" + b"ANTIVIRUS-TEST-FILE!$H+H*"
PNG_TRAILER = b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"


def load_world() -> ModuleType:
    name = "sos_test_api_world"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "api" / "world.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


# --- synthetic files ------------------------------------------------------------------------


def pdf(nonce: str | None = None) -> bytes:
    body = f"1 0 obj << /Type /Catalog >> endobj % {nonce or uuid.uuid4().hex}\n".encode()
    return b"%PDF-1.7\n" + body + b"trailer << /Root 1 0 R >>\n%%EOF\n"


def png(nonce: str | None = None) -> bytes:
    header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 17
    return header + (nonce or uuid.uuid4().hex).encode() + PNG_TRAILER


def jpg(extra: bytes = b"") -> bytes:
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00" + extra + uuid.uuid4().bytes + b"\xff\xd9"
    )


def xlsx(nonce: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("_rels/.rels", "<Relationships/>")
        z.writestr("xl/workbook.xml", f"<workbook><!-- {nonce or uuid.uuid4().hex} --></workbook>")
    return buf.getvalue()


def docx(nonce: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("_rels/.rels", "<Relationships/>")
        z.writestr("word/document.xml", f"<document>{nonce or uuid.uuid4().hex}</document>")
    return buf.getvalue()


def csv_text(nonce: str | None = None) -> bytes:
    return (
        "admission_no,student,class\nA-001,Synthetic Student,IX\n"
        f"A-002,కృత్రిమ విద్యార్థి,IX\n# {nonce or uuid.uuid4().hex}\n"
    ).encode()


# --- in-memory object store -----------------------------------------------------------------


@dataclass
class StoredObj:
    data: bytes
    content_type: str
    lifecycle: str | None = None


@dataclass
class MemoryStore:
    """Implements ``app.documents.storage.ObjectStore`` and records presign calls."""

    objects: dict[str, StoredObj] = field(default_factory=dict)
    posts: list[dict[str, Any]] = field(default_factory=list)
    gets: list[dict[str, Any]] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    kms_key_id: str | None = None
    # Test hook: runs right before a conditional copy (simulates a concurrent re-upload).
    before_copy: Any = None

    @staticmethod
    def etag_of(data: bytes) -> str:
        return '"' + hashlib.md5(data, usedforsecurity=False).hexdigest() + '"'

    def presigned_post(
        self, *, key: str, content_type: str, max_bytes: int, expires_s: int
    ) -> PresignedPost:
        assert 1 <= expires_s <= storage.MAX_UPLOAD_URL_TTL_S
        self.posts.append(
            {"key": key, "content_type": content_type, "max": max_bytes, "ttl": expires_s}
        )
        return PresignedPost(
            url="https://s3.synthetic.test/sos-test-files",
            fields={"key": key, "Content-Type": content_type, "policy": "synthetic"},
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_s),
        )

    def browser_post(self, fields: dict[str, str], data: bytes, content_type: str) -> int:
        """Simulate S3 enforcing the POST policy recorded for ``fields['key']``."""
        policy = next(p for p in reversed(self.posts) if p["key"] == fields["key"])
        if content_type != policy["content_type"]:
            return 403
        if not 1 <= len(data) <= policy["max"]:
            return 400
        self.objects[fields["key"]] = StoredObj(data, content_type)
        return 204

    def presigned_get(
        self, *, key: str, content_type: str, filename: str, expires_s: int
    ) -> tuple[str, dt.datetime]:
        assert 1 <= expires_s <= storage.MAX_DOWNLOAD_URL_TTL_S
        disposition = storage.attachment_disposition(filename)
        self.gets.append(
            {"key": key, "ttl": expires_s, "disposition": disposition, "type": content_type}
        )
        query = urllib.parse.urlencode(
            {
                "X-Amz-Expires": expires_s,
                "response-content-disposition": disposition,
                "response-content-type": content_type,
            }
        )
        url = f"https://s3.synthetic.test/sos-test-files/{key}?{query}"
        return url, dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_s)

    def head(self, key: str) -> ObjectHead | None:
        obj = self.objects.get(key)
        if obj is None:
            return None
        return ObjectHead(len(obj.data), obj.content_type, "aws:kms" if self.kms_key_id else None)

    def read_range(self, key: str, start: int, length: int) -> bytes:
        return self.objects[key].data[start : start + length]

    def iter_chunks(self, key: str, chunk_bytes: int = storage.CHUNK_BYTES) -> Iterator[bytes]:
        data = self.objects[key].data
        for i in range(0, len(data), chunk_bytes):
            yield data[i : i + chunk_bytes]

    def open(self, key: str, chunk_bytes: int = storage.CHUNK_BYTES) -> OpenedObject:
        data = self.objects[key].data
        chunks = (data[i : i + chunk_bytes] for i in range(0, len(data), chunk_bytes))
        return OpenedObject(etag=self.etag_of(data), chunks=chunks)

    def copy(self, src: str, dst: str, *, if_match: str, content_type: str) -> None:
        if self.before_copy is not None:
            self.before_copy(src)
        obj = self.objects[src]
        if self.etag_of(obj.data) != if_match:
            raise ObjectChanged("source_changed")
        self.objects[dst] = StoredObj(obj.data, content_type)

    def put(
        self, key: str, data: bytes, content_type: str, *, lifecycle: str | None = None
    ) -> None:
        if lifecycle is not None and lifecycle not in storage.LIFECYCLE_TAG_VALUES:
            raise ValueError("unknown lifecycle tag value")
        self.objects[key] = StoredObj(data, content_type, lifecycle)

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def discard(self, key: str) -> None:
        if not key.startswith("t/") or ".." in key.split("/"):
            raise ValueError("refusing to discard outside a tenant prefix")
        self.discarded.append(key)
        self.objects.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        doomed = [k for k in self.objects if k.startswith(prefix)]
        for k in doomed:
            del self.objects[k]
        return len(doomed)


_STORE = MemoryStore()


def memory_store() -> MemoryStore:
    """Install (once) and return the process-wide in-memory store."""
    storage.set_object_store(_STORE)
    return _STORE


# --- direct fixtures (admin engine; for matrix/BOLA set-up) ---------------------------------


def make_intent(
    admin: Engine,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    data: bytes,
    *,
    purpose: str = "circular",
    content_type: str = "application/pdf",
    ext: str = "pdf",
    document_id: uuid.UUID | None = None,
    version_no: int | None = None,
    expires_in: dt.timedelta = dt.timedelta(minutes=30),
) -> uuid.UUID:
    """Insert an upload intent and put its object in the memory store (as if uploaded)."""
    store = memory_store()
    intent_id = uuid.uuid4()
    doc_id = document_id or uuid.uuid4()
    batch_id = uuid.uuid4() if purpose == "import_file" else None
    with admin.begin() as c:
        if version_no is None:
            version_no = 1
            if document_id is not None:
                version_no = 1 + int(
                    c.execute(
                        text(
                            "SELECT coalesce(max(version_no), 0) FROM kb.document_versions "
                            "WHERE document_id = :d"
                        ),
                        {"d": document_id},
                    ).scalar_one()
                )
        key = f"t/{tenant_id}/uploads/{intent_id}/original.{ext}"
        now = dt.datetime.now(dt.UTC)
        c.execute(
            text(
                "INSERT INTO kb.upload_intents (id, tenant_id, purpose, document_id, version_no, "
                "batch_id, object_key, declared_content_type, declared_size, max_bytes, "
                "created_by, created_at, expires_at) VALUES (:i, :t, :p, :d, :v, :b, :k, :ct, "
                ":s, :m, :u, :c, :x)"
            ),
            {
                "i": intent_id,
                "t": tenant_id,
                "p": purpose,
                "d": doc_id,
                "v": version_no,
                "b": batch_id,
                "k": key,
                "ct": content_type,
                "s": len(data),
                "m": 25 * 1024 * 1024,
                "u": user_id,
                "c": now,
                "x": now + expires_in,
            },
        )
    store.put(key, data, content_type)
    return intent_id


def make_document(
    admin: Engine,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    *,
    acl: list[tuple[str, str]] | None = None,
    status: str = "ready",
    sensitivity: str = "C2",
    purpose: str = "circular",
    doc_type: str = "circular",
    data: bytes | None = None,
    document_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert a document with one version (default ``ready``) and its object. ``document_id``
    pins the ID (e.g. a digit-heavy UUID, whose object key must still pass the outbox checks)."""
    store = memory_store()
    doc_id, version_id = document_id or uuid.uuid4(), uuid.uuid4()
    content = data or pdf()
    key = f"t/{tenant_id}/docs/{doc_id}/v1/original.pdf"
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, sensitivity, "
                "current_version_id, created_by) VALUES (:d, :t, :p, :dt, :ti, :s, :v, :u)"
            ),
            {
                "d": doc_id,
                "t": tenant_id,
                "p": purpose,
                "dt": doc_type,
                "ti": "Synthetic circular",
                "s": sensitivity,
                "v": version_id,
                "u": created_by,
            },
        )
        c.execute(
            text(
                "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
                "(:v, :t, :d, 1, :k, :h, 'application/pdf', :n, :st, :u)"
            ),
            {
                "v": version_id,
                "t": tenant_id,
                "d": doc_id,
                "k": key,
                "h": hashlib.sha256(content).digest(),
                "n": len(content),
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
    store.put(key, content, "application/pdf")
    return doc_id


def document_version(admin: Engine, document_id: uuid.UUID) -> int:
    with admin.connect() as c:
        return int(
            c.execute(
                text("SELECT version FROM kb.documents WHERE id = :d"), {"d": document_id}
            ).scalar_one()
        )


def outbox_events(admin: Engine, tenant_id: uuid.UUID, event_type: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT payload FROM ops.outbox WHERE tenant_id = :t AND event_type = :e "
                "ORDER BY created_at"
            ),
            {"t": tenant_id, "e": event_type},
        )
        return [dict(r[0]) for r in rows]


def service_document(tenant_id: uuid.UUID, person: Any) -> uuid.UUID:
    """Create a document through the real service path (upload -> S3 -> register) as a
    school-wide ``owner`` of ``tenant_id`` (for cross-tenant fixtures)."""
    from app.authz.context import Scopes, UserContext
    from app.core.db import tenant_session
    from app.documents import service
    from app.documents.schemas import DocumentCreate, UploadCreate

    store = memory_store()
    ctx = UserContext(
        user_id=person.user_id,
        tenant_id=tenant_id,
        membership_id=person.membership_id,
        roles=frozenset({"owner"}),
        permissions=frozenset({"document.upload", "document.read", "document.manage_acl"}),
        scopes=Scopes(school=True),
        mfa=True,
        auth_time=None,
    )
    data = pdf()
    with tenant_session(tenant_id, person.user_id) as s:
        up = service.create_upload(
            s,
            ctx,
            UploadCreate(
                filename="other-school.pdf",
                content_type="application/pdf",
                size_bytes=len(data),
                purpose="circular",
            ),
        )
    assert store.browser_post(up.fields, data, "application/pdf") == 204
    with tenant_session(tenant_id, person.user_id) as s:
        doc = service.register_document(
            s, ctx, DocumentCreate(upload_id=up.upload_id, title="Other school circular")
        )
    return doc.id
