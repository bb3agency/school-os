"""Helpers for privacy request tests (synthetic data only). Loaded by path from tests/privacy
and the security suites."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.students import service as students
from app.students.schemas import GuardianCreate

TESTS = Path(__file__).resolve().parents[1]
BASE = "/api/v1/privacy/requests"


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


EX = _load("sos_test_exports_objects", TESTS / "exports" / "objects.py")
SW = EX.SW


def student(school: Any, *, section_key: str = "section_9a", name: str | None = None) -> uuid.UUID:
    sid: uuid.UUID = EX.student(school, section_key=section_key, name=name)
    return sid


def guardian(school: Any, student_id: uuid.UUID, name: str = "Synthetica Guardian") -> uuid.UUID:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        out = students.add_guardian(
            db,
            SW.admin_ctx(school),
            student_id,
            GuardianCreate(relationship="mother", full_name=name, is_primary=True),
        )
    return out.id


def body(
    kind: str = "access",
    *,
    student_id: uuid.UUID | None = None,
    guardian_id: uuid.UUID | None = None,
    membership_id: uuid.UUID | None = None,
    verification: str | None = "in_person_document",
    text_: str = "Please tell me what you hold about my child.",
    **extra: Any,
) -> dict[str, Any]:
    subject: dict[str, str] = {}
    if student_id is not None:
        subject["student_id"] = str(student_id)
    if guardian_id is not None:
        subject["guardian_id"] = str(guardian_id)
    if membership_id is not None:
        subject["membership_id"] = str(membership_id)
    out: dict[str, Any] = {
        "kind": kind,
        "subject": subject,
        "requester_relationship": "mother",
        "requester_name": "Synthetica Requester",
        "request_text": text_,
        **extra,
    }
    if verification is not None:
        out["verification_method"] = verification
    return out


def log(api: Any, who: Any, data: dict[str, Any], **kw: Any) -> Any:
    return api.call(who, "POST", BASE, json=data, **kw)


def logged(api: Any, who: Any, data: dict[str, Any], **kw: Any) -> dict[str, Any]:
    res = log(api, who, data, **kw)
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def request_row(admin: Engine, request_id: uuid.UUID | str) -> dict[str, Any]:
    with admin.connect() as c:
        row = c.execute(
            text("SELECT * FROM ops.privacy_requests WHERE id = :i"), {"i": request_id}
        ).one()
    return dict(row._mapping)


def run_report(school: Any, export_id: str) -> str:
    return str(EX.run(school, uuid.UUID(export_id)))
