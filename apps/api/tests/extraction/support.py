"""Test support for register-photo extraction (synthetic data only).

Loaded by path (pytest runs with ``--import-mode=importlib``) from ``tests/extraction`` and the
security suites. Builds on ``tests/api/world.py`` (schools A and B), ``tests/students/
student_world.py`` (contexts, keyring) and ``tests/documents/support.py`` (in-memory S3).

Valid-checksum Aadhaar-like numbers are generated here, inside the test process only
(``app.devtools.fake_ids`` deliberately produces invalid ones): they exist to prove that such a
number never reaches the database, logs or API responses.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.redaction import verhoeff_check_digit
from app.extraction import service
from app.extraction.providers import fake_script_png
from app.extraction.schemas import BatchCreate

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
SW = _load("sos_test_student_world", TESTS / "students" / "student_world.py")
D = _load("sos_test_documents_support", TESTS / "documents" / "support.py")

MIME = {"png": "image/png", "jpg": "image/jpeg", "pdf": "application/pdf"}


# --- synthetic content ---------------------------------------------------------------------


def valid_aadhaar_like(seed: int) -> str:
    """A 12-digit number WITH a valid Verhoeff check digit (test process only, never stored)."""
    body = str(2 + seed % 8) + f"{(seed * 7919 + 12345) % 10**10:010d}"
    return body + verhoeff_check_digit(body)


def cell(value: str, confidence: float = 0.95, bbox: list[float] | None = None) -> dict[str, Any]:
    return {"value": value, "confidence": confidence, "bbox": bbox or [0.1, 0.1, 0.2, 0.05]}


def register_row(
    name: str,
    *,
    admission_no: str | None = None,
    dob: str = "2012-06-15",
    confidence: float = 0.95,
    **extra: dict[str, Any],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "admission_no": cell(admission_no or W.unique("R")[:10], confidence),
        "full_name": cell(name, confidence),
        "dob": cell(dob, confidence),
        "gender": cell("female", confidence),
        "father_name": cell("Synthetica Father Rao", confidence),
        "mother_name": cell("Synthetica Mother Devi", confidence),
        "admission_date": cell("2019-06-10", confidence),
        "mother_tongue": cell("Telugu", confidence),
    }
    row.update(extra)
    return row


def page_png(rows: list[dict[str, Any]], raw_text: str = "", **script: Any) -> bytes:
    """A PNG whose fake-provider script yields ``rows`` (plus a nonce, so hashes differ)."""
    return fake_script_png(
        {"rows": rows, "raw_text": raw_text, "nonce": uuid.uuid4().hex, **script}
    )


def rendered_page(
    rows: list[dict[str, Any]], spans: list[dict[str, Any]], size: tuple[int, int] = (600, 300)
) -> bytes:
    """A real ``size`` image whose text layer is ``spans`` (``{"text", "box": [l, t, r, b]}``,
    drawn in grey) and whose rows are ``rows``: the fake provider reads it like OCR (PRV-016)."""
    return fake_script_png(
        {"size": list(size), "spans": spans, "rows": rows, "nonce": uuid.uuid4().hex}
    )


def number_spans(number: str, top: int = 200) -> list[dict[str, Any]]:
    """``number`` as word-level OCR returns it: three spans side by side."""
    return [
        {"text": number[0:4], "box": [300, top, 340, top + 20]},
        {"text": number[4:8], "box": [345, top, 385, top + 20]},
        {"text": number[8:12], "box": [390, top, 430, top + 20]},
    ]


# --- documents ------------------------------------------------------------------------------


def register_scan(
    admin: Engine,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    data: bytes,
    *,
    ext: str = "png",
    status: str = "ready",
    purpose: str = "register_scan",
    acl: list[tuple[str, str]] | None = None,
) -> uuid.UUID:
    """Insert a scanned register page document (one version) and its object in memory S3."""
    store = D.memory_store()
    doc_id, version_id = uuid.uuid4(), uuid.uuid4()
    key = f"t/{tenant_id}/docs/{doc_id}/v1/original.{ext}"
    doc_type = "register_scan" if purpose == "register_scan" else "circular"
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, sensitivity, "
                "current_version_id, created_by) VALUES (:d, :t, :p, :dt, 'Register page', 'C2', "
                ":v, :u)"
            ),
            {
                "d": doc_id,
                "t": tenant_id,
                "p": purpose,
                "dt": doc_type,
                "v": version_id,
                "u": created_by,
            },
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
                "m": MIME[ext],
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
    store.put(key, data, MIME[ext])
    return doc_id


# --- batches through the real services ------------------------------------------------------


def ctx(school: Any, role: str = "office_admin") -> Any:
    return SW.ctx_for(school.tenant_id, school.people.get(role, school.people["owner"]), role)


def start_batch(
    school: Any, document_ids: list[uuid.UUID], role: str = "office_admin"
) -> uuid.UUID:
    person = school.people.get(role, school.people["owner"])
    with tenant_session(school.tenant_id, person.user_id) as s:
        out = service.create_batch(s, ctx(school, role), BatchCreate(document_ids=document_ids))
    return out.id


def processed_batch(
    admin: Engine, school: Any, pages: list[bytes], role: str = "office_admin"
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Documents -> batch -> fake provider (synchronously); returns (batch id, item ids)."""
    SW.configure_keyring()
    owner = school.people["owner"].user_id
    docs = [register_scan(admin, school.tenant_id, owner, page) for page in pages]
    batch_id = start_batch(school, docs, role)
    service.process_batch(school.tenant_id, batch_id)
    return batch_id, item_ids(admin, batch_id)


def pending_item(admin: Engine, school: Any, **row: Any) -> uuid.UUID:
    """One fresh pending item of ``school`` (a one-row page)."""
    name = row.pop("name", f"Synthetica Queue {W.unique()}")
    _, items = processed_batch(admin, school, [page_png([register_row(name, **row)])])
    return items[0]


def item_ids(admin: Engine, batch_id: uuid.UUID) -> list[uuid.UUID]:
    with admin.connect() as c:
        rows = c.execute(
            text("SELECT id FROM sis.extraction_items WHERE batch_id = :b ORDER BY id"),
            {"b": batch_id},
        )
        return [uuid.UUID(str(r[0])) for r in rows]


def row_of(admin: Engine, table: str, row_id: uuid.UUID) -> dict[str, Any]:
    assert table in {"sis.extraction_batches", "sis.extraction_pages", "sis.extraction_items"}
    with admin.connect() as c:
        return dict(
            c.execute(text(f"SELECT * FROM {table} WHERE id = :i"), {"i": row_id}).one()._mapping
        )


def dump_tenant_text(admin: Engine, tenant_id: uuid.UUID) -> str:
    """Every row a school owns in the tables extraction writes to or triggers, as text."""
    tables = (
        "sis.extraction_batches",
        "sis.extraction_pages",
        "sis.extraction_items",
        "sis.attribute_values",
        "sis.student_profiles",
        "sis.students",
        "audit.events",
        "ops.outbox",
        "ops.notifications",
    )
    parts: list[str] = []
    with admin.connect() as c:
        for table in tables:
            rows = c.execute(
                text(f"SELECT row_to_json(x)::text FROM {table} x WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
            parts.extend(r[0] for r in rows)
    return "\n".join(parts)


# --- notification templates (FR-NOT-001; owned by app/notifications/templates.yaml) ---------


@pytest.fixture
def extraction_templates() -> None:
    """The extraction templates ship in the production notifications catalog: tests that
    expect the batch owner to be told use that catalog, nothing injected."""
    from app.notifications import templates

    catalog = templates.catalog()
    assert {service.READY_TEMPLATE, service.FAILED_TEMPLATE} <= set(catalog)
