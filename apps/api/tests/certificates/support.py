"""Certificate test objects created through the real services (synthetic data only).

Loaded by path (pytest runs with ``--import-mode=importlib``) from ``tests/certificates`` and the
security suites (authz matrix, BOLA). Builds on ``tests/api/world.py`` (schools and staff),
``tests/students/student_world.py`` (students) and ``tests/documents/support.py`` (in-memory
object store).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
import itertools
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, text

from app.authz.context import UserContext
from app.certificates import service as certificates
from app.certificates.schemas import (
    ApproveIn,
    CertificateOut,
    CertificateRequest,
    DuplicateRequest,
    ReasonIn,
)
from app.core.db import tenant_session

_HERE = Path(__file__).resolve()
_names = itertools.count(1)
IST = ZoneInfo("Asia/Kolkata")
REASON = "The original certificate was lost on the way home"


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


class FakeRenderer:
    """Records the HTML it was given and returns a small valid PDF."""

    def __init__(self) -> None:
        self.pages: list[str] = []

    def render(self, html: str) -> bytes:
        self.pages.append(html)
        pdf: bytes = D.pdf()
        return pdf


def install() -> Any:
    """Keyring and the in-memory object store for this process."""
    SW.configure_keyring()
    return D.memory_store()


def today() -> dt.date:
    return dt.datetime.now(IST).date()


def ctx(school: Any, person: Any, role: str, **scopes: Any) -> UserContext:
    """The role's permissions with a fresh MFA sign-in (step-up satisfied)."""
    base: UserContext = SW.ctx_for(school.tenant_id, person, role, **scopes)
    return dataclasses.replace(base, auth_time=dt.datetime.now(dt.UTC))


def ctx_roles(school: Any, person: Any, *roles: str) -> UserContext:
    """Union of several system roles (a member holding e.g. office_admin AND principal)."""
    parts = [ctx(school, person, r) for r in roles]
    return dataclasses.replace(
        parts[0],
        roles=frozenset().union(*(p.roles for p in parts)),
        permissions=frozenset().union(*(p.permissions for p in parts)),
        scoped_permissions=frozenset.intersection(*(p.scoped_permissions for p in parts)),
    )


def student(school: Any, section_key: str = "section_9a", **kw: Any) -> uuid.UUID:
    """A fresh synthetic student with an admission number (identity values unverified)."""
    n = next(_names)
    value: uuid.UUID = SW.create(
        school,
        name=f"Synthetica Certificate Student {n:04d}",
        section_key=section_key,
        admission_no=kw.pop("admission_no", f"C/{uuid.uuid4().hex[:6].upper()}"),
        **kw,
    )
    return value


def call(school: Any, person: Any, role: str, fn: Any, *args: Any, **kw: Any) -> Any:
    c = kw.pop("as_ctx", None) or ctx(school, person, role)
    with tenant_session(school.tenant_id, person.user_id) as s:
        return fn(s, c, *args, **kw)


def tc_inputs(**overrides: str) -> dict[str, str]:
    return {
        "leaving_date": today().isoformat(),
        "leaving_reason": "parent_transferred",
        "promotion": "promoted",
        "conduct": "good",
        **overrides,
    }


def issue(
    school: Any,
    student_id: uuid.UUID,
    certificate_type: str = "bonafide",
    *,
    role: str = "office_admin",
    inputs: dict[str, str] | None = None,
) -> CertificateOut:
    """Request (and for direct types issue) a certificate as ``role``."""
    install()
    if inputs is None:
        inputs = {
            "bonafide": {"purpose": "bus_pass"},
            "study": {},
            "conduct": {"conduct": "good"},
            "transfer": tc_inputs(),
        }[certificate_type]
    person = school.people[role]
    out: CertificateOut = call(
        school,
        person,
        role,
        certificates.request_certificate,
        student_id,
        CertificateRequest(certificate_type=certificate_type, inputs=inputs),
    )
    return out


def pending_tc(school: Any, student_id: uuid.UUID | None = None) -> CertificateOut:
    """A transfer certificate prepared by the office admin, waiting for approval."""
    return issue(school, student_id or student(school), "transfer")


def draft_hash(school: Any, certificate_id: uuid.UUID, *, role: str = "principal") -> str:
    """The ``draft_sha256`` of a pending certificate as ``role`` reads it (A-11); a placeholder
    for one that is not pending, so the approval's own refusal is what a test sees."""
    install()
    out: CertificateOut = call(
        school, school.people[role], role, certificates.get_certificate, certificate_id
    )
    return out.draft_sha256 or "0" * 64


def approve(
    school: Any, certificate: CertificateOut, *, role: str = "principal", person: Any = None
) -> CertificateOut:
    install()
    who = person or school.people[role]
    out: CertificateOut = call(
        school,
        who,
        role,
        certificates.approve,
        certificate.id,
        ApproveIn(draft_sha256=draft_hash(school, certificate.id)),
        expected_version=certificate.version,
    )
    return out


def issued_tc(school: Any, student_id: uuid.UUID | None = None) -> CertificateOut:
    return approve(school, pending_tc(school, student_id))


def duplicate(
    school: Any, certificate_id: uuid.UUID, *, role: str = "office_admin"
) -> CertificateOut:
    install()
    out: CertificateOut = call(
        school,
        school.people[role],
        role,
        certificates.request_duplicate,
        certificate_id,
        DuplicateRequest(reason=REASON),
    )
    return out


def cancel(school: Any, certificate: CertificateOut, *, role: str = "principal") -> CertificateOut:
    install()
    out: CertificateOut = call(
        school,
        school.people[role],
        role,
        certificates.cancel,
        certificate.id,
        ReasonIn(reason="Issued with the wrong purpose by mistake"),
        expected_version=certificate.version,
    )
    return out


def render(school: Any, certificate: CertificateOut, renderer: FakeRenderer | None = None) -> str:
    """Run the PDF worker step with a fake renderer (no Chromium in unit tests)."""
    install()
    return certificates.render_pdf(
        school.tenant_id,
        certificate.id,
        school.people["office_admin"].user_id,
        renderer=renderer or FakeRenderer(),
    )


def row(admin: Engine, certificate_id: uuid.UUID) -> dict[str, Any]:
    with admin.connect() as c:
        r = c.execute(
            text("SELECT * FROM sis.certificates WHERE id = :i"), {"i": certificate_id}
        ).one()
    return dict(r._mapping)


def audit_rows(admin: Engine, tenant_id: uuid.UUID, resource_id: uuid.UUID) -> list[dict[str, Any]]:
    """Audit events whose resource is ``resource_id`` or whose summary names it."""
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT action, actor_type, resource_type, resource_id, summary FROM audit.events "
                "WHERE tenant_id = :t AND (resource_id = :r "
                "OR summary ->> 'certificate_id' = CAST(:r AS text)) ORDER BY seq"
            ),
            {"t": tenant_id, "r": resource_id},
        )
        return [dict(r._mapping) for r in rows]


def actions(admin: Engine, tenant_id: uuid.UUID, resource_id: uuid.UUID) -> list[str]:
    return [r["action"] for r in audit_rows(admin, tenant_id, resource_id)]


def outbox(admin: Engine, tenant_id: uuid.UUID, certificate_id: uuid.UUID) -> list[str]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT event_type FROM ops.outbox WHERE tenant_id = :t "
                "AND payload ->> 'certificate_id' = CAST(:r AS text) ORDER BY created_at"
            ),
            {"t": tenant_id, "r": certificate_id},
        )
        return [r[0] for r in rows]


def notifications(
    admin: Engine, tenant_id: uuid.UUID, certificate_id: uuid.UUID
) -> list[tuple[str, uuid.UUID]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT template_key, recipient_membership_id FROM ops.notifications "
                "WHERE tenant_id = :t AND resource_id = :r ORDER BY created_at"
            ),
            {"t": tenant_id, "r": certificate_id},
        )
        return [(r[0], r[1]) for r in rows]


def mark_document_ready(admin: Engine, document_id: uuid.UUID) -> None:
    """Pretend the malware scan passed (the scan itself is tested by the documents module)."""
    with admin.begin() as c:
        c.execute(
            text("UPDATE kb.document_versions SET status = 'ready' WHERE document_id = :d"),
            {"d": document_id},
        )


def enrolments(admin: Engine, student_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text("SELECT * FROM sis.enrollments WHERE student_id = :s ORDER BY created_at"),
            {"s": student_id},
        )
        return [dict(r._mapping) for r in rows]


def student_status(admin: Engine, student_id: uuid.UUID) -> str:
    with admin.connect() as c:
        value: str = c.execute(
            text("SELECT status FROM sis.students WHERE id = :s"), {"s": student_id}
        ).scalar_one()
    return value
