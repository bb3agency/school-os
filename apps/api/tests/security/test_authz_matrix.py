"""Generated authorization matrix (SEC-003, SEC-005, FR-IAM-002, FR-IAM-010, docs/12 §4.2).

For every (system role, tenant route) pair a synthetic member holding only that role calls the
route with a valid request: 2xx when roles.yaml grants the route's permission, 403 otherwise.
Also: 428 for step-up routes with a stale sign-in, 403 ``mfa_required`` for privileged roles
without MFA. Expectations come from roles.yaml (itself pinned to docs/07 §6.2) and the live
route table, so a new route without a matrix entry fails the suite.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import Engine

from app.authz.catalog import AUTHENTICATED, system_roles
from app.main import create_app

pytestmark = pytest.mark.db


def _load_world() -> ModuleType:
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


W = _load_world()
world = W.world
api = W.api


def _load_students() -> ModuleType:
    """Shared synthetic students (tests/students/student_world.py)."""
    name = "sos_test_student_world"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "students" / "student_world.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _load_documents_support() -> ModuleType:
    name = "sos_test_documents_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "documents" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _load_imports_support() -> ModuleType:
    """tests/imports/support.py (synthetic spreadsheets and import batches)."""
    name = "sos_test_imports_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "imports" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _load_dq_support() -> ModuleType:
    """DQ helpers (tests/dq/dq_support.py): findings created through the real engine."""
    name = "sos_test_dq_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "dq" / "dq_support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


SW = _load_students()
D = _load_documents_support()
IM = _load_imports_support()
DQ = _load_dq_support()


def _load_extraction_support() -> ModuleType:
    """tests/extraction/support.py (register pages, batches and items via the real services)."""
    name = "sos_test_extraction_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "extraction" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


X = _load_extraction_support()

Request = tuple[str, dict[str, Any] | None, dict[str, str]]
Builder = Callable[[Any, str, Engine], Request]
_years = itertools.count(2100)


def _ct(role: str, w: Any, scoped: str, default: str) -> uuid.UUID:
    value: uuid.UUID = w.a.ids[scoped] if role == "teacher" else w.a.ids[default]
    return value


def _if_match(version: int) -> dict[str, str]:
    return {"If-Match": f'W/"{version}"'}


def _new_year(w: Any, role: str, admin: Engine) -> Request:
    y = next(_years)
    body = {
        "label": f"{y}-{(y + 1) % 100:02d}",
        "starts_on": f"{y}-06-01",
        "ends_on": f"{y + 1}-03-31",
    }
    return "/api/v1/academic-years", body, {}


def _target(w: Any) -> Any:
    return w.a.people["target"]


def _student(w: Any, role: str) -> uuid.UUID:
    """A student inside the role's scope: 9A for the class teacher, class X for the teacher."""
    ids = SW.ensure_students(w)
    value: uuid.UUID = ids["s10a"] if role == "teacher" else ids["s9a"]
    return value


def _st(w: Any, role: str, suffix: str = "") -> str:
    return f"/api/v1/students/{_student(w, role)}{suffix}"


def _student_patch(w: Any, r: str, a: Engine) -> Request:
    sid = _student(w, r)
    return _st(w, r), {"status": "active"}, _if_match(SW.version(a, "sis.students", sid))


def _verify(w: Any, r: str, a: Engine) -> Request:
    value_id = SW.current_value_id(a, _student(w, r), "mother_tongue", "parent_form")
    return _st(w, r, f"/values/{value_id}/verify"), {"status": "verified"}, {}


def _guardian_patch(w: Any, r: str, a: Engine) -> Request:
    ids = SW.ensure_students(w)
    path = f"/api/v1/students/{ids['s9a']}/guardians/{ids['g9a']}"
    return path, {"relationship": "father"}, _if_match(SW.version(a, "sis.guardians", ids["g9a"]))


def _enrol(w: Any, r: str, a: Engine) -> Request:
    mover = SW.ensure_students(w)["mover"]
    current = SW.active_section(a, mover)
    target = w.a.ids["section_9c"] if current == w.a.ids["section_9a"] else w.a.ids["section_9a"]
    return f"/api/v1/students/{mover}/enrollments", {"section_id": str(target)}, {}


# --- documents (FR-DOC-*): every reader role sees these; class teacher via 9A, teacher via X --


def _doc_acl(w: Any) -> list[dict[str, str]]:
    return [
        {"principal_type": "section", "principal_ref": str(w.a.ids["section_9a"])},
        {"principal_type": "class", "principal_ref": str(w.a.ids["class_x"])},
    ]


def _shared_doc(w: Any, admin: Engine, slot: str) -> uuid.UUID:
    """A ready (scanned) school A document visible to every document.read holder."""
    key = f"matrix_doc_{slot}"
    if key not in w.a.ids:
        w.a.ids[key] = D.make_document(
            admin,
            w.a.tenant_id,
            w.a.people["owner"].user_id,
            acl=[(e["principal_type"], e["principal_ref"]) for e in _doc_acl(w)],
        )
    value: uuid.UUID = w.a.ids[key]
    return value


def _doc_register(w: Any, r: str, a: Engine) -> Request:
    intent = D.make_intent(a, w.a.tenant_id, w.person(r).user_id, D.pdf())
    # Scoped uploaders (class teacher) may only share with their own sections.
    body = {"upload_id": str(intent), "title": "Matrix circular", "acl": _doc_acl(w)[:1]}
    return "/api/v1/documents", body, {}


def _doc_version(w: Any, r: str, a: Engine) -> Request:
    doc = _shared_doc(w, a, "versions")
    intent = D.make_intent(a, w.a.tenant_id, w.person(r).user_id, D.pdf(), document_id=doc)
    return f"/api/v1/documents/{doc}/versions", {"upload_id": str(intent)}, {}


def _doc_acl_put(w: Any, r: str, a: Engine) -> Request:
    doc = _shared_doc(w, a, "acl")
    return (
        f"/api/v1/documents/{doc}/acl",
        {"acl": _doc_acl(w)},
        _if_match(D.document_version(a, doc)),
    )


def _doc_delete(w: Any, r: str, a: Engine) -> Request:
    doc = D.make_document(a, w.a.tenant_id, w.a.people["owner"].user_id)
    return f"/api/v1/documents/{doc}", None, {}


# --- imports (US-401, FR-IMP-*): office roles and the exam coordinator ----------------------


def _shared_import(w: Any, admin: Engine) -> uuid.UUID:
    """A validated school A batch (started by the office admin) readable by import.run holders."""
    if "matrix_import" not in w.a.ids:
        w.a.ids["matrix_import"] = IM.start(admin, w.a, IM.xlsx_bytes(IM.class_list(1)[0]))
    value: uuid.UUID = w.a.ids["matrix_import"]
    return value


def _fresh_import(w: Any, admin: Engine) -> uuid.UUID:
    batch_id: uuid.UUID = IM.start(admin, w.a, IM.xlsx_bytes(IM.class_list(1)[0]))
    return batch_id


def _import_create(w: Any, r: str, a: Engine) -> Request:
    doc = IM.import_document(
        a, w.a.tenant_id, w.person(r).user_id, IM.xlsx_bytes(IM.class_list(1)[0])
    )
    return "/api/v1/imports", {"document_id": str(doc), "source": "admission_register"}, {}


def _import_mapping(w: Any, r: str, a: Engine) -> Request:
    batch_id = _fresh_import(w, a)
    batch = IM.batch(a, batch_id)
    columns = [{"index": int(k), "target": v} for k, v in batch["mapping"].items()]
    return (
        f"/api/v1/imports/{batch_id}/mapping",
        {"columns": columns},
        _if_match(batch["version"]),
    )


def _import_committed(w: Any, r: str, a: Engine) -> Request:
    batch_id = IM.imported(a, w.a, IM.xlsx_bytes(IM.class_list(1)[0]))
    return f"/api/v1/imports/{batch_id}/revert", None, {}


# --- data quality (FR-DQ-*, US-501, US-502): class teacher reaches 9A findings only ---------------


def _dq_run(w: Any, r: str, a: Engine) -> Request:
    """A run requested by the role itself (scoped holders see only their own runs)."""
    SW.configure_keyring()
    run = DQ.call(w.a, DQ.dq.run_checks, student_ids=[_student(w, r)], as_ctx=DQ.ctx(w.a, r))
    return f"/api/v1/dq/runs/{run.id}", None, {}


def _dq_finding(w: Any, slot: str) -> uuid.UUID:
    """A school A finding in 9A (visible to every dq.findings.read holder)."""
    key = f"matrix_dq_{slot}"
    if key not in w.a.ids:
        SW.configure_keyring()
        w.a.ids[key] = DQ.high_finding(w.a)
    value: uuid.UUID = w.a.ids[key]
    return value


def _dq_fresh(w: Any, r: str, a: Engine) -> uuid.UUID:
    SW.configure_keyring()
    finding: uuid.UUID = DQ.high_finding(w.a)
    return finding


# --- change requests (US-601, FR-CR-*) ------------------------------------------------


def _cr() -> ModuleType:
    """tests/changes/objects.py (change requests through the real services)."""
    name = "sos_test_changes_objects"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "changes" / "objects.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


_MAKERS = ("principal", "office_admin", "office_staff", "exam_coordinator")


def _cr_submit(w: Any, role: str, admin: Engine) -> Request:
    """A fresh student and an evidence document the role can open (identity correction)."""
    student = _cr().student(w.a)
    doc = _cr().evidence(admin, w.a, w.a.people["owner"])
    body = {
        "student_id": str(student),
        "attribute_key": "dob",
        "new_value": "2012-03-15",
        "reason": "Synthetic matrix correction reason",
        "evidence_document_id": str(doc),
    }
    return "/api/v1/change-requests", body, {}


def _cr_pending(w: Any, admin: Engine, role: str | None = None) -> uuid.UUID:
    """A fresh pending request: submitted by ``role`` when it is a maker (cancel), otherwise by
    the office admin (so approvers are never the requester)."""
    maker = role if role in _MAKERS else "office_admin"
    out = _cr().submit(admin, w.a, w.person(maker), maker)
    value: uuid.UUID = out.id
    return value


def _cr_shared(w: Any, admin: Engine) -> uuid.UUID:
    if "matrix_change_request" not in w.a.ids:
        w.a.ids["matrix_change_request"] = _cr_pending(w, admin)
    value: uuid.UUID = w.a.ids["matrix_change_request"]
    return value


# --- register-photo extraction (US-402): import.run / import.commit, school-wide roles only ---


def _x_page(w: Any, a: Engine) -> uuid.UUID:
    page = X.page_png([X.register_row(f"Synthetica Matrix {W.unique()}")])
    doc: uuid.UUID = X.register_scan(a, w.a.tenant_id, w.a.people["owner"].user_id, page)
    return doc


def _x_batch(w: Any, a: Engine) -> uuid.UUID:
    """A processed school A batch with pending rows (created once)."""
    if "matrix_extraction_batch" not in w.a.ids:
        batch_id, items = X.processed_batch(a, w.a, [X.page_png([X.register_row("Synthetica M")])])
        w.a.ids["matrix_extraction_batch"] = batch_id
        w.a.ids["matrix_extraction_item"] = items[0]
    value: uuid.UUID = w.a.ids["matrix_extraction_batch"]
    return value


def _x_item(w: Any, a: Engine) -> uuid.UUID:
    _x_batch(w, a)
    value: uuid.UUID = w.a.ids["matrix_extraction_item"]
    return value


def _x_create(w: Any, r: str, a: Engine) -> Request:
    return "/api/v1/extraction-batches", {"document_ids": [str(_x_page(w, a))]}, {}


def _x_confirm(w: Any, r: str, a: Engine) -> Request:
    item = X.pending_item(a, w.a)
    body = {"fields": {"full_name": f"Synthetica Matrix Confirm {W.unique()}"}}
    return f"/api/v1/extraction-items/{item}/confirm", body, {}


def _x_reject(w: Any, r: str, a: Engine) -> Request:
    item = X.pending_item(a, w.a)
    return f"/api/v1/extraction-items/{item}/reject", {"reason": "other"}, {}


# --- exports (US-501 AC4, US-901, FR-EXP-*): export.board/portal, student.export (step-up) ------


def _ex() -> ModuleType:
    """tests/exports/objects.py (exports through the real services; fake PDF renderer)."""
    name = "sos_test_exports_objects"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "exports" / "objects.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


_EXPORT_PERMISSIONS = ("export.board", "export.portal", "student.export")


def _exporter(role: str) -> bool:
    held = system_roles()[role].permission_keys
    return any(p in held for p in _EXPORT_PERMISSIONS)


def _export_id(w: Any, role: str, *, ready: bool) -> uuid.UUID:
    """The role's own export in school A (exports are visible only to their requester); a
    random id for roles without export permissions (they stop at 403 first)."""
    if not _exporter(role):
        return uuid.uuid4()
    SW.ensure_students(w)
    ex = _ex()
    value: uuid.UUID = (ex.ready_export if ready else ex.queued_export)(w.a, role)
    return value


def _precheck_body(w: Any) -> dict[str, Any]:
    SW.ensure_students(w)
    return {
        "profile_key": "cisce-registration-2026",
        "scope": {"section_ids": [str(w.a.ids["section_9a"])]},
        "format": ["xlsx"],
    }


def _list_body(w: Any) -> dict[str, Any]:
    SW.ensure_students(w)
    return {
        "columns": ["admission_no", "full_name"],
        "scope": {"section_ids": [str(w.a.ids["section_9a"])]},
        "format": "csv",
    }


SPECS: dict[tuple[str, str], Builder] = {
    ("GET", "/api/v1/me"): lambda w, r, a: ("/api/v1/me", None, {}),
    ("GET", "/api/v1/me/schools"): lambda w, r, a: ("/api/v1/me/schools", None, {}),
    ("POST", "/api/v1/me/accept-invitations"): lambda w, r, a: (
        "/api/v1/me/accept-invitations",
        None,
        {},
    ),
    ("POST", "/api/v1/me/active-tenant"): lambda w, r, a: (
        "/api/v1/me/active-tenant",
        {"tenant_id": str(w.a.tenant_id)},
        {},
    ),
    ("POST", "/api/v1/me/login-event"): lambda w, r, a: ("/api/v1/me/login-event", None, {}),
    ("GET", "/api/v1/users"): lambda w, r, a: ("/api/v1/users", None, {}),
    ("GET", "/api/v1/users/{user_id}"): lambda w, r, a: (
        f"/api/v1/users/{_target(w).user_id}",
        None,
        {},
    ),
    ("POST", "/api/v1/users"): lambda w, r, a: (
        "/api/v1/users",
        {
            "idp_subject": f"sub-{uuid.uuid4().hex}",
            "display_name": "Synthetic Matrix Invitee",
            "roles": ["teacher"],
        },
        {},
    ),
    ("PATCH", "/api/v1/users/{user_id}"): lambda w, r, a: (
        f"/api/v1/users/{_target(w).user_id}",
        {"status": "active"},
        _if_match(W.version_of(a, "core.memberships", _target(w).membership_id)),
    ),
    ("PUT", "/api/v1/users/{user_id}/roles"): lambda w, r, a: (
        f"/api/v1/users/{_target(w).user_id}/roles",
        {"roles": ["teacher"]},
        {},
    ),
    ("PUT", "/api/v1/users/{user_id}/scopes"): lambda w, r, a: (
        f"/api/v1/users/{_target(w).user_id}/scopes",
        {"scopes": []},
        {},
    ),
    ("GET", "/api/v1/roles"): lambda w, r, a: ("/api/v1/roles", None, {}),
    ("GET", "/api/v1/permissions"): lambda w, r, a: ("/api/v1/permissions", None, {}),
    ("GET", "/api/v1/tenant"): lambda w, r, a: ("/api/v1/tenant", None, {}),
    ("PATCH", "/api/v1/tenant"): lambda w, r, a: (
        "/api/v1/tenant",
        {"idle_timeout_minutes": 15},
        _if_match(W.tenant_version(a, w.a.tenant_id)),
    ),
    ("GET", "/api/v1/academic-years"): lambda w, r, a: ("/api/v1/academic-years", None, {}),
    ("GET", "/api/v1/academic-years/{year_id}"): lambda w, r, a: (
        f"/api/v1/academic-years/{w.a.ids['old_year']}",
        None,
        {},
    ),
    ("POST", "/api/v1/academic-years"): _new_year,
    ("PATCH", "/api/v1/academic-years/{year_id}"): lambda w, r, a: (
        f"/api/v1/academic-years/{w.a.ids['old_year']}",
        {},
        _if_match(W.version_of(a, "core.academic_years", w.a.ids["old_year"])),
    ),
    ("POST", "/api/v1/academic-years/{year_id}/make-current"): lambda w, r, a: (
        f"/api/v1/academic-years/{w.a.ids['year']}/make-current",
        None,
        _if_match(W.version_of(a, "core.academic_years", w.a.ids["year"])),
    ),
    ("GET", "/api/v1/classes"): lambda w, r, a: ("/api/v1/classes", None, {}),
    ("POST", "/api/v1/classes/defaults"): lambda w, r, a: ("/api/v1/classes/defaults", None, {}),
    ("GET", "/api/v1/classes/{class_id}"): lambda w, r, a: (
        f"/api/v1/classes/{_ct(r, w, 'class_x', 'class_ix')}",
        None,
        {},
    ),
    ("POST", "/api/v1/classes"): lambda w, r, a: (
        "/api/v1/classes",
        {
            "code": W.unique("M")[:12],
            "display_en": "Matrix class",
            "display_te": "తరగతి",
            "sort_order": 900,
        },
        {},
    ),
    ("PATCH", "/api/v1/classes/{class_id}"): lambda w, r, a: (
        f"/api/v1/classes/{w.a.ids['class_ix']}",
        {},
        _if_match(W.version_of(a, "core.classes", w.a.ids["class_ix"])),
    ),
    ("GET", "/api/v1/sections"): lambda w, r, a: ("/api/v1/sections", None, {}),
    ("GET", "/api/v1/sections/{section_id}"): lambda w, r, a: (
        f"/api/v1/sections/{_ct(r, w, 'section_10a', 'section_9a')}",
        None,
        {},
    ),
    ("POST", "/api/v1/sections"): lambda w, r, a: (
        "/api/v1/sections",
        {
            "academic_year_id": str(w.a.ids["year"]),
            "class_id": str(w.a.ids["class_ix"]),
            "name": W.unique("S")[:10],
        },
        {},
    ),
    ("PATCH", "/api/v1/sections/{section_id}"): lambda w, r, a: (
        f"/api/v1/sections/{w.a.ids['section_9a']}",
        {},
        _if_match(W.version_of(a, "core.sections", w.a.ids["section_9a"])),
    ),
    ("GET", "/api/v1/audit/events"): lambda w, r, a: ("/api/v1/audit/events", None, {}),
    # Documents (FR-DOC-001..006, SEC-016).
    ("POST", "/api/v1/documents/uploads"): lambda w, r, a: (
        "/api/v1/documents/uploads",
        {
            "filename": "matrix.pdf",
            "content_type": "application/pdf",
            "size_bytes": 100,
            "purpose": "circular",
        },
        {},
    ),
    ("POST", "/api/v1/documents"): _doc_register,
    ("GET", "/api/v1/documents"): lambda w, r, a: ("/api/v1/documents", None, {}),
    ("GET", "/api/v1/documents/{document_id}"): lambda w, r, a: (
        f"/api/v1/documents/{_shared_doc(w, a, 'read')}",
        None,
        {},
    ),
    ("POST", "/api/v1/documents/{document_id}/versions"): _doc_version,
    ("GET", "/api/v1/documents/{document_id}/download-url"): lambda w, r, a: (
        f"/api/v1/documents/{_shared_doc(w, a, 'read')}/download-url",
        None,
        {},
    ),
    ("PUT", "/api/v1/documents/{document_id}/acl"): _doc_acl_put,
    ("DELETE", "/api/v1/documents/{document_id}"): _doc_delete,
    ("GET", "/api/v1/audit/verify"): lambda w, r, a: ("/api/v1/audit/verify", None, {}),
    # School-side routes backed by the control plane (app/platform/tenant_api.py).
    ("GET", "/api/v1/tenant/billing"): lambda w, r, a: ("/api/v1/tenant/billing", None, {}),
    ("GET", "/api/v1/tenant/billing/invoices"): lambda w, r, a: (
        "/api/v1/tenant/billing/invoices",
        None,
        {},
    ),
    ("GET", "/api/v1/announcements"): lambda w, r, a: ("/api/v1/announcements", None, {}),
    ("POST", "/api/v1/support/tickets"): lambda w, r, a: (
        "/api/v1/support/tickets",
        {"category": "other", "subject": "Matrix ticket", "body": "Synthetic question"},
        {},
    ),
    ("GET", "/api/v1/support/tickets"): lambda w, r, a: ("/api/v1/support/tickets", None, {}),
    # Students (app/students/api.py; docs/09 Students).
    ("GET", "/api/v1/attributes"): lambda w, r, a: ("/api/v1/attributes", None, {}),
    ("GET", "/api/v1/students"): lambda w, r, a: ("/api/v1/students", None, {}),
    # Name/admission-number search in the body, never the URL (SEC-008, FR-STU-010).
    ("POST", "/api/v1/students/search"): lambda w, r, a: (
        "/api/v1/students/search",
        {"query": "Synthetica", "limit": 20},
        {},
    ),
    ("POST", "/api/v1/students"): lambda w, r, a: (
        "/api/v1/students",
        {
            "values": [
                {
                    "attribute_key": "full_name",
                    "source": "admission_register",
                    "value": "Synthetica Matrix Student",
                }
            ]
        },
        {},
    ),
    ("GET", "/api/v1/students/{student_id}"): lambda w, r, a: (_st(w, r), None, {}),
    ("PATCH", "/api/v1/students/{student_id}"): _student_patch,
    ("GET", "/api/v1/students/{student_id}/values"): lambda w, r, a: (
        _st(w, r, "/values"),
        None,
        {},
    ),
    ("POST", "/api/v1/students/{student_id}/values"): lambda w, r, a: (
        _st(w, r, "/values"),
        {"attribute_key": "mother_tongue", "source": "parent_form", "value": "Telugu"},
        {},
    ),
    ("POST", "/api/v1/students/{student_id}/values/{value_id}/verify"): _verify,
    ("POST", "/api/v1/students/{student_id}/sensitive-reveal"): lambda w, r, a: (
        _st(w, r, "/sensitive-reveal"),
        {"attribute_key": "health_notes"},
        {},
    ),
    ("GET", "/api/v1/students/{student_id}/guardians"): lambda w, r, a: (
        _st(w, r, "/guardians"),
        None,
        {},
    ),
    ("POST", "/api/v1/students/{student_id}/guardians"): lambda w, r, a: (
        _st(w, r, "/guardians"),
        {"relationship": "guardian", "full_name": "Synthetica Matrix Guardian"},
        {},
    ),
    ("PATCH", "/api/v1/students/{student_id}/guardians/{guardian_id}"): _guardian_patch,
    ("POST", "/api/v1/students/{student_id}/enrollments"): _enrol,
    ("GET", "/api/v1/support/tickets/{ticket_id}"): lambda w, r, a: (
        f"/api/v1/support/tickets/{_ticket(w)}",
        None,
        {},
    ),
    ("POST", "/api/v1/support/tickets/{ticket_id}/messages"): lambda w, r, a: (
        f"/api/v1/support/tickets/{_ticket(w)}/messages",
        {"body": "Synthetic follow-up"},
        {},
    ),
    # Imports (app/imports/api.py; US-401, FR-IMP-001..007).
    ("POST", "/api/v1/imports"): _import_create,
    ("GET", "/api/v1/imports"): lambda w, r, a: ("/api/v1/imports", None, {}),
    ("GET", "/api/v1/imports/{import_id}"): lambda w, r, a: (
        f"/api/v1/imports/{_shared_import(w, a)}",
        None,
        {},
    ),
    ("GET", "/api/v1/imports/{import_id}/rows"): lambda w, r, a: (
        f"/api/v1/imports/{_shared_import(w, a)}/rows",
        None,
        {},
    ),
    ("PUT", "/api/v1/imports/{import_id}/mapping"): _import_mapping,
    ("POST", "/api/v1/imports/{import_id}/validate"): lambda w, r, a: (
        f"/api/v1/imports/{_fresh_import(w, a)}/validate",
        None,
        {},
    ),
    ("POST", "/api/v1/imports/{import_id}/commit"): lambda w, r, a: (
        f"/api/v1/imports/{_fresh_import(w, a)}/commit",
        {},
        {},
    ),
    ("POST", "/api/v1/imports/{import_id}/revert"): _import_committed,
    ("GET", "/api/v1/import-templates"): lambda w, r, a: ("/api/v1/import-templates", None, {}),
    ("POST", "/api/v1/import-templates"): lambda w, r, a: (
        "/api/v1/import-templates",
        {"name": f"Matrix layout {uuid.uuid4().hex[:8]}", "import_id": str(_shared_import(w, a))},
        {},
    ),
    # Notifications (FR-NOT-001): every member, own notifications only.
    ("GET", "/api/v1/notifications"): lambda w, r, a: ("/api/v1/notifications", None, {}),
    ("GET", "/api/v1/notifications/unread-count"): lambda w, r, a: (
        "/api/v1/notifications/unread-count",
        None,
        {},
    ),
    ("POST", "/api/v1/notifications/read-all"): lambda w, r, a: (
        "/api/v1/notifications/read-all",
        None,
        {},
    ),
    ("POST", "/api/v1/notifications/{notification_id}/read"): lambda w, r, a: (
        f"/api/v1/notifications/{_notification(w.a.tenant_id, w.person(r).membership_id)}/read",
        None,
        {},
    ),
    # Break-glass, school side (US-103, FR-OPS-004): owner and principal, step-up for changes.
    ("GET", "/api/v1/breakglass/requests"): lambda w, r, a: (
        "/api/v1/breakglass/requests",
        None,
        {},
    ),
    ("GET", "/api/v1/breakglass/requests/{request_id}"): lambda w, r, a: (
        f"/api/v1/breakglass/requests/{_bg().pending_grant(w.a.tenant_id)}",
        None,
        {},
    ),
    ("POST", "/api/v1/breakglass/requests/{request_id}/approve"): lambda w, r, a: (
        f"/api/v1/breakglass/requests/{_bg().pending_grant(w.a.tenant_id)}/approve",
        None,
        {},
    ),
    ("POST", "/api/v1/breakglass/requests/{request_id}/deny"): lambda w, r, a: (
        f"/api/v1/breakglass/requests/{_bg().pending_grant(w.a.tenant_id)}/deny",
        None,
        {},
    ),
    # Data quality (app/dq/api.py; docs/09 Data quality).
    ("POST", "/api/v1/dq/runs"): lambda w, r, a: (
        "/api/v1/dq/runs",
        {"scope": {"student_ids": [str(_student(w, r))]}},
        {},
    ),
    ("GET", "/api/v1/dq/runs/{run_id}"): _dq_run,
    ("GET", "/api/v1/dq/findings"): lambda w, r, a: ("/api/v1/dq/findings", None, {}),
    ("GET", "/api/v1/dq/findings/{finding_id}"): lambda w, r, a: (
        f"/api/v1/dq/findings/{_dq_finding(w, 'read')}",
        None,
        {},
    ),
    ("POST", "/api/v1/dq/findings/{finding_id}/resolve"): lambda w, r, a: (
        f"/api/v1/dq/findings/{_dq_fresh(w, r, a)}/resolve",
        {"note": "Synthetic matrix note"},
        {},
    ),
    ("POST", "/api/v1/dq/findings/{finding_id}/waive"): lambda w, r, a: (
        f"/api/v1/dq/findings/{_dq_fresh(w, r, a)}/waive",
        {"reason": "Synthetic matrix reason"},
        {},
    ),
    ("GET", "/api/v1/dq/rules"): lambda w, r, a: ("/api/v1/dq/rules", None, {}),
    ("GET", "/api/v1/dq/profiles"): lambda w, r, a: ("/api/v1/dq/profiles", None, {}),
    ("GET", "/api/v1/dq/summary"): lambda w, r, a: (
        "/api/v1/dq/summary",
        None,
        {},
    ),
    ("POST", "/api/v1/breakglass/grants/{grant_id}/revoke"): lambda w, r, a: (
        f"/api/v1/breakglass/grants/{_bg().active_grant(w.a.tenant_id, w.person('owner'))}/revoke",
        None,
        {},
    ),
    # Change requests (US-601, FR-CR-*): makers request/cancel, checkers approve/reject.
    ("POST", "/api/v1/change-requests"): _cr_submit,
    ("GET", "/api/v1/change-requests"): lambda w, r, a: ("/api/v1/change-requests", None, {}),
    ("GET", "/api/v1/change-requests/{change_request_id}"): lambda w, r, a: (
        f"/api/v1/change-requests/{_cr_shared(w, a)}",
        None,
        {},
    ),
    ("GET", "/api/v1/change-requests/{change_request_id}/memo"): lambda w, r, a: (
        f"/api/v1/change-requests/{_cr_shared(w, a)}/memo",
        None,
        {},
    ),
    ("POST", "/api/v1/change-requests/{change_request_id}/approve"): lambda w, r, a: (
        f"/api/v1/change-requests/{_cr_pending(w, a)}/approve",
        None,
        _if_match(1),
    ),
    ("POST", "/api/v1/change-requests/{change_request_id}/reject"): lambda w, r, a: (
        f"/api/v1/change-requests/{_cr_pending(w, a)}/reject",
        {"reason": "Synthetic matrix rejection"},
        _if_match(1),
    ),
    ("POST", "/api/v1/change-requests/{change_request_id}/cancel"): lambda w, r, a: (
        f"/api/v1/change-requests/{_cr_pending(w, a, r)}/cancel",
        None,
        _if_match(1),
    ),
    # Register-photo extraction (US-402, FR-IMP-020..023).
    ("GET", "/api/v1/extraction-batches"): lambda w, r, a: ("/api/v1/extraction-batches", None, {}),
    ("POST", "/api/v1/extraction-batches"): _x_create,
    ("GET", "/api/v1/extraction-batches/{batch_id}"): lambda w, r, a: (
        f"/api/v1/extraction-batches/{_x_batch(w, a)}",
        None,
        {},
    ),
    ("GET", "/api/v1/extraction-items"): lambda w, r, a: ("/api/v1/extraction-items", None, {}),
    ("GET", "/api/v1/extraction-items/{item_id}"): lambda w, r, a: (
        f"/api/v1/extraction-items/{_x_item(w, a)}",
        None,
        {},
    ),
    ("POST", "/api/v1/extraction-items/{item_id}/confirm"): _x_confirm,
    ("POST", "/api/v1/extraction-items/{item_id}/reject"): _x_reject,
    # Exports (app/exports/api.py; docs/09 Exports).
    ("GET", "/api/v1/export-profiles"): lambda w, r, a: ("/api/v1/export-profiles", None, {}),
    ("POST", "/api/v1/exports"): lambda w, r, a: ("/api/v1/exports", _precheck_body(w), {}),
    ("POST", "/api/v1/exports/student-list"): lambda w, r, a: (
        "/api/v1/exports/student-list",
        _list_body(w),
        {},
    ),
    ("GET", "/api/v1/exports"): lambda w, r, a: ("/api/v1/exports", None, {}),
    ("GET", "/api/v1/exports/{export_id}"): lambda w, r, a: (
        f"/api/v1/exports/{_export_id(w, r, ready=False)}",
        None,
        {},
    ),
    ("GET", "/api/v1/exports/{export_id}/download-url"): lambda w, r, a: (
        f"/api/v1/exports/{_export_id(w, r, ready=True)}/download-url",
        None,
        {},
    ),
}


def _bg() -> ModuleType:
    """tests/breakglass/objects.py (pending/active grants through the real services)."""
    name = "sos_test_breakglass_objects"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "breakglass" / "objects.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _notification(tenant_id: uuid.UUID, membership_id: uuid.UUID) -> uuid.UUID:
    """A notification for ``membership_id`` (created through app.notifications.service)."""
    from sqlalchemy import text

    from app.core.db import tenant_session
    from app.notifications import service as notifications

    key = f"matrix:{uuid.uuid4()}"
    with tenant_session(tenant_id) as s:
        notifications.notify(
            s,
            tenant_id=tenant_id,
            recipients=[membership_id],
            template_key="import.committed",
            params={"import_id": str(uuid.uuid4()), "rows": 1},
            dedupe_key=key,
        )
        value: object = s.execute(
            text("SELECT id FROM ops.notifications WHERE dedupe_key = :k"), {"k": key}
        ).scalar_one()
    return uuid.UUID(str(value))


def _ticket(w: Any) -> uuid.UUID:
    """A ticket of school A (opened through the platform service, as the owner)."""
    from app.platform import service as platform_service

    ticket = platform_service.open_ticket_from_tenant(
        w.a.tenant_id,
        w.a.people["owner"].user_id,
        platform_service.TicketCreateSchool(
            category="other", subject="Matrix ticket", body="Synthetic question"
        ),
    )
    return ticket.id


def _route_table() -> dict[tuple[str, str], Any]:
    table: dict[tuple[str, str], Any] = {}
    for rc in iter_route_contexts(create_app().routes):
        route = rc.original_route
        if not isinstance(route, APIRoute):
            continue
        guard = [d.call for d in route.dependant.dependencies if hasattr(d.call, "sos_permission")]
        if not guard:
            continue  # public health checks
        if str(rc.path).startswith(("/api/v1/platform/", "/api/v1/fleet/")):
            continue  # control plane: tests/platform/test_authz_matrix.py (operator roles)
        for method in rc.methods or ():
            table[(method, str(rc.path))] = guard[0]
    return table


ROUTES = _route_table()
ROLES = tuple(system_roles())
STEP_UP_ROUTES = sorted(k for k, g in ROUTES.items() if g.sos_step_up)


def _success(method: str, path: str) -> int:
    creates = {
        "/api/v1/users",
        "/api/v1/academic-years",
        "/api/v1/classes",
        "/api/v1/sections",
        "/api/v1/support/tickets",
        "/api/v1/students",
        "/api/v1/students/{student_id}/values",
        "/api/v1/students/{student_id}/guardians",
        "/api/v1/students/{student_id}/enrollments",
        "/api/v1/documents/uploads",
        "/api/v1/import-templates",
        "/api/v1/change-requests",
    }
    accepted = {
        "/api/v1/documents",
        "/api/v1/documents/{document_id}/versions",
        "/api/v1/imports",
        "/api/v1/imports/{import_id}/validate",
        "/api/v1/imports/{import_id}/commit",
        "/api/v1/dq/runs",
        "/api/v1/extraction-batches",
        "/api/v1/exports",
        "/api/v1/exports/student-list",
    }
    if method == "POST" and path in accepted:
        return 202
    if method == "DELETE" and path == "/api/v1/documents/{document_id}":
        return 204
    return 201 if method == "POST" and path in creates else 200


def _call(api: Any, w: Any, admin: Engine, role: str, key: tuple[str, str], **kw: Any) -> Any:
    path, body, headers = SPECS[key](w, role, admin)
    return api.call(w.person(role), key[0], path, json=body, headers=headers, **kw)


def test_SEC_003_matrix_covers_every_protected_route() -> None:
    assert set(SPECS) == set(ROUTES)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("key", sorted(SPECS), ids=lambda k: f"{k[0]} {k[1]}")
def test_SEC_003_role_route_matrix(
    world: Any, api: Any, admin_engine: Engine, role: str, key: tuple[str, str]
) -> None:
    guard = ROUTES[key]
    # require_any() guards (change-request reads) pass with any of their permissions.
    permissions = (guard.sos_permission, *getattr(guard, "sos_any_of", ()))
    held = system_roles()[role].permission_keys
    granted = AUTHENTICATED in permissions or any(p in held for p in permissions)
    res = _call(api, world, admin_engine, role, key)
    expected = _success(*key) if granted else 403
    assert res.status_code == expected, f"{role} {key}: {res.status_code} {res.text}"


@pytest.mark.parametrize("key", STEP_UP_ROUTES, ids=lambda k: f"{k[0]} {k[1]}")
def test_SEC_005_step_up_routes_need_recent_mfa(
    world: Any, api: Any, admin_engine: Engine, key: tuple[str, str]
) -> None:
    permission = ROUTES[key].sos_permission
    holders = [r for r in ROLES if permission in system_roles()[r].permission_keys]
    assert holders
    for role in holders:
        res = _call(api, world, admin_engine, role, key, auth_age_s=301)
        assert res.status_code == 428, (role, res.text)
        assert res.json()["code"] == "step_up_required"


@pytest.mark.parametrize("key", sorted(SPECS), ids=lambda k: f"{k[0]} {k[1]}")
def test_FR_IAM_002_privileged_roles_need_mfa_on_every_route(
    world: Any, api: Any, admin_engine: Engine, key: tuple[str, str]
) -> None:
    for role in ("owner", "principal", "office_admin"):
        res = _call(api, world, admin_engine, role, key, mfa=False)
        assert res.status_code == 403, (role, res.text)
        assert res.json()["code"] == "mfa_required"
