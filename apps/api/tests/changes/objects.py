"""Change-request test objects created through the real services (synthetic data only).

Loaded by path (pytest runs with ``--import-mode=importlib``) from ``tests/changes`` and the
security suites (authz matrix, BOLA). Builds on ``tests/api/world.py`` (schools and staff),
``tests/students/student_world.py`` (students) and ``tests/documents/support.py`` (documents).
"""

from __future__ import annotations

import dataclasses
import importlib.util
import itertools
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

from app.authz.context import UserContext
from app.changes import service as changes
from app.changes.schemas import ChangeRequestCreate, ChangeRequestOut
from app.core.db import tenant_session

_HERE = Path(__file__).resolve()
_names = itertools.count(1)


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


W = _load("sos_test_api_world", _HERE.parents[1] / "api" / "world.py")
SW = _load("sos_test_student_world", _HERE.parents[1] / "students" / "student_world.py")
D = _load("sos_test_documents_support", _HERE.parents[1] / "documents" / "support.py")

REASON = "Birth certificate shows a different date of birth"


def ctx(school: Any, person: Any, role: str, **scopes: Any) -> UserContext:
    """The role's permissions with a fresh MFA sign-in (step-up satisfied)."""
    import datetime as dt

    base: UserContext = SW.ctx_for(school.tenant_id, person, role, **scopes)
    return dataclasses.replace(base, auth_time=dt.datetime.now(dt.UTC))


def ctx_roles(school: Any, person: Any, *roles: str, **scopes: Any) -> UserContext:
    """Union of several system roles (a member holding e.g. office_admin AND principal)."""
    parts = [ctx(school, person, r, **scopes) for r in roles]
    return dataclasses.replace(
        parts[0],
        roles=frozenset().union(*(p.roles for p in parts)),
        permissions=frozenset().union(*(p.permissions for p in parts)),
        scoped_permissions=frozenset.intersection(*(p.scoped_permissions for p in parts)),
    )


def audit_rows(admin: Engine, tenant_id: uuid.UUID, resource_id: uuid.UUID) -> list[dict[str, Any]]:
    """Audit events whose resource is ``resource_id`` or whose summary names it."""
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT action, actor_type, actor_id, resource_id, summary FROM audit.events "
                "WHERE tenant_id = :t AND (resource_id = :r "
                "OR summary ->> 'change_request_id' = CAST(:r AS text)) ORDER BY seq"
            ),
            {"t": tenant_id, "r": resource_id},
        )
        return [dict(r._mapping) for r in rows]


def actions(admin: Engine, tenant_id: uuid.UUID, resource_id: uuid.UUID) -> list[str]:
    return [r["action"] for r in audit_rows(admin, tenant_id, resource_id)]


def outbox(admin: Engine, tenant_id: uuid.UUID, request_id: uuid.UUID) -> list[tuple[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT event_type, payload FROM ops.outbox WHERE tenant_id = :t "
                "AND payload ->> 'change_request_id' = CAST(:r AS text) ORDER BY created_at"
            ),
            {"t": tenant_id, "r": request_id},
        )
        return [(r[0], dict(r[1])) for r in rows]


def notifications(
    admin: Engine, tenant_id: uuid.UUID, request_id: uuid.UUID
) -> list[tuple[str, uuid.UUID]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT template_key, recipient_membership_id FROM ops.notifications "
                "WHERE tenant_id = :t AND resource_id = :r ORDER BY created_at"
            ),
            {"t": tenant_id, "r": request_id},
        )
        return [(r[0], r[1]) for r in rows]


def student(school: Any, section_key: str = "section_9a", **kw: Any) -> uuid.UUID:
    """A fresh synthetic student (identity values recorded unverified, admission register)."""
    n = next(_names)
    value: uuid.UUID = SW.create(
        school,
        name=f"Synthetica Correction Student {n:04d}",
        section_key=section_key,
        admission_no=kw.pop("admission_no", f"CR/{uuid.uuid4().hex[:6].upper()}"),
        **kw,
    )
    return value


def evidence(
    admin: Engine,
    school: Any,
    uploader: Any,
    *,
    purpose: str = "evidence",
    status: str = "ready",
    acl: list[tuple[str, str]] | None = None,
) -> uuid.UUID:
    """An evidence document (C3 scan) of ``school`` with a ready version."""
    doc_id: uuid.UUID = D.make_document(
        admin,
        school.tenant_id,
        uploader.user_id,
        purpose=purpose,
        doc_type="certificate" if purpose == "evidence" else "circular",
        sensitivity="C3" if purpose == "evidence" else "C1",
        status=status,
        acl=acl,
    )
    return doc_id


def submit(
    admin: Engine,
    school: Any,
    requester: Any,
    role: str,
    *,
    student_id: uuid.UUID | None = None,
    attribute_key: str = "dob",
    new_value: str = "2012-03-15",
    target_source: str = "admission_register",
    evidence_id: uuid.UUID | None = None,
) -> ChangeRequestOut:
    """Submit a request through ``app.changes.service`` as ``requester`` holding ``role``."""
    SW.configure_keyring()
    sid = student_id or student(school)
    doc = evidence_id or evidence(admin, school, requester)
    with tenant_session(school.tenant_id, requester.user_id) as s:
        return changes.submit(
            s,
            ctx(school, requester, role),
            ChangeRequestCreate(
                student_id=sid,
                attribute_key=attribute_key,
                target_source=target_source,
                new_value=new_value,
                reason=REASON,
                evidence_document_id=doc,
            ),
        )


def service_evidence(school: Any, person: Any) -> uuid.UUID:
    """An evidence document created through the real upload -> register path (no admin engine;
    for cross-tenant fixtures of the BOLA suite). Its version is ``queued`` (usable evidence)."""
    from app.documents import service as documents
    from app.documents.schemas import DocumentCreate, UploadCreate

    store = D.memory_store()
    uploader = dataclasses.replace(
        SW.ctx_for(school.tenant_id, person, "owner"),
        permissions=frozenset({"document.upload", "document.read", "document.manage_acl"}),
    )
    data = D.pdf()
    with tenant_session(school.tenant_id, person.user_id) as s:
        up = documents.create_upload(
            s,
            uploader,
            UploadCreate(
                filename="birth-certificate.pdf",
                content_type="application/pdf",
                size_bytes=len(data),
                purpose="evidence",
            ),
        )
    assert store.browser_post(up.fields, data, "application/pdf") == 204
    with tenant_session(school.tenant_id, person.user_id) as s:
        doc = documents.register_document(
            s, uploader, DocumentCreate(upload_id=up.upload_id, title="Synthetic birth certificate")
        )
    return doc.id


def pending(school: Any) -> uuid.UUID:
    """A pending request of ``school`` through the real services only, submitted by its office
    admin (or, for school B which has none, by the owner acting with office-admin permissions)."""
    requester = school.people.get("office_admin") or school.people["owner"]
    SW.configure_keyring()
    sid = student(school)
    doc = service_evidence(school, requester)
    with tenant_session(school.tenant_id, requester.user_id) as s:
        return changes.submit(
            s,
            ctx(school, requester, "office_admin"),
            ChangeRequestCreate(
                student_id=sid,
                attribute_key="dob",
                new_value="2012-03-15",
                reason=REASON,
                evidence_document_id=doc,
            ),
        ).id


def version(admin: Engine, request_id: uuid.UUID) -> int:
    with admin.connect() as c:
        return int(
            c.execute(
                text("SELECT version FROM sis.change_requests WHERE id = :i"), {"i": request_id}
            ).scalar_one()
        )


def row(admin: Engine, request_id: uuid.UUID) -> dict[str, Any]:
    with admin.connect() as c:
        return dict(
            c.execute(text("SELECT * FROM sis.change_requests WHERE id = :i"), {"i": request_id})
            .one()
            ._mapping
        )


def if_match(admin: Engine, request_id: uuid.UUID) -> dict[str, str]:
    return {"If-Match": f'W/"{version(admin, request_id)}"'}
