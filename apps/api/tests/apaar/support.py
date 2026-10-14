"""Helpers for APAAR consent tests (synthetic data only). Loaded by path from tests/apaar and the
security suites."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

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


SW = _load("sos_test_student_world", TESTS / "students" / "student_world.py")
D = _load("sos_test_documents_support", TESTS / "documents" / "support.py")

DECIDED_ON = "2026-07-15"


def student(school: Any, *, section_key: str = "section_9a", name: str | None = None) -> uuid.UUID:
    """A fresh synthetic student enrolled in ``section_key`` (current year)."""
    value: uuid.UUID = SW.create(
        school,
        name=name or f"Synthetica Apaar {uuid.uuid4().hex[:6]}",
        section_key=section_key,
        admission_no=f"AP/{uuid.uuid4().hex[:8]}",
    )
    return value


def signed_form(admin: Engine, school: Any, uploader: Any) -> uuid.UUID:
    """An uploaded signed form: an evidence document (C3 scan) with a ready version."""
    doc: uuid.UUID = D.make_document(
        admin,
        school.tenant_id,
        uploader.user_id,
        purpose="evidence",
        doc_type="form",
        sensitivity="C3",
    )
    return doc


def given(doc: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {
        "status": "given",
        "relationship": "mother",
        "decided_on": DECIDED_ON,
        "form_language": "te",
        "evidence_document_id": str(doc),
        **extra,
    }


def refused(**extra: Any) -> dict[str, Any]:
    return {"status": "refused", "relationship": "father", "decided_on": DECIDED_ON, **extra}


def consent_rows(admin: Engine, student_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text("SELECT * FROM sis.apaar_consents WHERE student_id = :s ORDER BY seq"),
                {"s": student_id},
            )
        ]


def record(api: Any, who: Any, student_id: uuid.UUID, body: dict[str, Any], **kw: Any) -> Any:
    return api.call(who, "POST", f"/api/v1/students/{student_id}/apaar-consent", json=body, **kw)
