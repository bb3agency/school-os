"""Helpers for consent and breach register tests (synthetic data only). Loaded by path from
tests/privacy and the security suites (authz matrix, BOLA)."""

from __future__ import annotations

import datetime as dt
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
SHARED_KEY = "photos_school_media"
DESCRIPTION = "A class register of 9A was left on a bus and has not been found yet."


def student(school: Any, *, section_key: str = "section_9a", name: str | None = None) -> uuid.UUID:
    """A fresh synthetic student enrolled in ``section_key`` (current year)."""
    value: uuid.UUID = SW.create(
        school,
        name=name or f"Synthetica Consent {uuid.uuid4().hex[:6]}",
        section_key=section_key,
        admission_no=f"CP/{uuid.uuid4().hex[:8]}",
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


def shared_purpose(api: Any, school: Any, admin: Engine) -> uuid.UUID:
    """The school's shared optional purpose (enabled from the suggestion once; switched on again
    if a test switched it off)."""
    key = f"purpose:{SHARED_KEY}"
    if key not in school.ids:
        res = api.call(
            school.people["owner"],
            "POST",
            "/api/v1/consent-purposes",
            json={"suggested_key": SHARED_KEY},
        )
        assert res.status_code in (201, 409), res.text
        with admin.connect() as c:
            school.ids[key] = c.execute(
                text("SELECT id FROM sis.consent_purposes WHERE tenant_id = :t AND key = :k"),
                {"t": school.tenant_id, "k": SHARED_KEY},
            ).scalar_one()
    with admin.begin() as c:
        c.execute(
            text("UPDATE sis.consent_purposes SET active = true WHERE id = :i"),
            {"i": school.ids[key]},
        )
    value: uuid.UUID = school.ids[key]
    return value


def deactivate_all(admin: Engine, tenant_id: uuid.UUID) -> None:
    """Switch every purpose of the school off (keeps the active-purpose limit in reach)."""
    with admin.begin() as c:
        c.execute(
            text("UPDATE sis.consent_purposes SET active = false WHERE tenant_id = :t"),
            {"t": tenant_id},
        )


def own_purpose(api: Any, school: Any, admin: Engine, **extra: Any) -> dict[str, Any]:
    """A fresh school-defined purpose (the others are switched off first)."""
    deactivate_all(admin, school.tenant_id)
    body = {
        "key": f"own_{uuid.uuid4().hex[:10]}",
        "label_en": "Name in the school magazine",
        "description": "Your child's name and class in the printed school magazine, for one year.",
        **extra,
    }
    res = api.call(school.people["owner"], "POST", "/api/v1/consent-purposes", json=body)
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def given(purpose_id: uuid.UUID, doc: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {
        "purpose_id": str(purpose_id),
        "status": "given",
        "relationship": "mother",
        "decided_on": DECIDED_ON,
        "form_language": "en",
        "evidence_document_id": str(doc),
        **extra,
    }


def refused(purpose_id: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {
        "purpose_id": str(purpose_id),
        "status": "refused",
        "relationship": "father",
        "decided_on": DECIDED_ON,
        **extra,
    }


def record(api: Any, who: Any, student_id: uuid.UUID, body: dict[str, Any], **kw: Any) -> Any:
    return api.call(who, "POST", f"/api/v1/students/{student_id}/consents", json=body, **kw)


def consent_rows(admin: Engine, student_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text("SELECT * FROM sis.consents WHERE student_id = :s ORDER BY seq"),
                {"s": student_id},
            )
        ]


# --- breaches -------------------------------------------------------------------------------


def noticed(hours_ago: float = 2) -> str:
    return (dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours_ago)).isoformat()


def breach_body(**extra: Any) -> dict[str, Any]:
    return {
        "noticed_at": noticed(),
        "description": DESCRIPTION,
        "categories": ["identity", "attendance"],
        "approx_children": 42,
        "approx_guardians": 0,
        "approx_staff": 1,
        **extra,
    }


def create_breach(api: Any, who: Any, **extra: Any) -> dict[str, Any]:
    res = api.call(who, "POST", "/api/v1/privacy/breaches", json=breach_body(**extra))
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def breach_row(admin: Engine, breach_id: Any) -> dict[str, Any]:
    with admin.connect() as c:
        return dict(
            c.execute(text("SELECT * FROM ops.breach_records WHERE id = :i"), {"i": breach_id})
            .one()
            ._mapping
        )


def matrix_breach(api: Any, school: Any) -> uuid.UUID:
    """A fresh open breach of school A recorded by its owner (authz matrix and BOLA)."""
    out = create_breach(api, school.people["owner"])
    return uuid.UUID(out["id"])
