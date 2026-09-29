"""Object-level authorization and cross-tenant isolation over the API (SEC-015, SEC-001,
invariant 3, docs/12 §4.3-4.4).

- Every route with an ID in its path answers 404 for another school's ID and for a random ID,
  even for the owner (never reveal existence), and changes nothing.
- Scoped roles get 404 for classes/sections outside their scopes.
- Lists never contain another school's objects; bodies cannot reference them.
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import Engine, text

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


D = _load_documents_support()

# Minimal valid bodies so the request reaches the object lookup.
BODIES: dict[tuple[str, str], dict[str, Any] | None] = {
    ("PATCH", "/api/v1/users/{user_id}"): {"status": "active"},
    ("PUT", "/api/v1/users/{user_id}/roles"): {"roles": ["teacher"]},
    ("PUT", "/api/v1/users/{user_id}/scopes"): {"scopes": []},
    ("POST", "/api/v1/users/{user_id}/invitation-email"): None,
    ("PATCH", "/api/v1/academic-years/{year_id}"): {},
    ("PATCH", "/api/v1/classes/{class_id}"): {},
    ("PATCH", "/api/v1/sections/{section_id}"): {},
    ("POST", "/api/v1/academic-years/{year_id}/archive"): None,
    ("POST", "/api/v1/academic-years/{year_id}/unarchive"): None,
    ("POST", "/api/v1/classes/{class_id}/archive"): None,
    ("POST", "/api/v1/classes/{class_id}/unarchive"): None,
    ("POST", "/api/v1/sections/{section_id}/archive"): None,
    ("POST", "/api/v1/sections/{section_id}/unarchive"): None,
    ("POST", "/api/v1/support/tickets/{ticket_id}/messages"): {"body": "Synthetic follow-up"},
    ("PATCH", "/api/v1/students/{student_id}"): {"status": "active"},
    ("POST", "/api/v1/students/{student_id}/values"): {
        "attribute_key": "mother_tongue",
        "source": "parent_form",
        "value": "Telugu",
    },
    ("POST", "/api/v1/students/{student_id}/values/{value_id}/verify"): {"status": "verified"},
    ("POST", "/api/v1/students/{student_id}/sensitive-reveal"): {"attribute_key": "health_notes"},
    ("POST", "/api/v1/students/{student_id}/guardians"): {
        "relationship": "guardian",
        "full_name": "Synthetica Guardian",
    },
    ("PATCH", "/api/v1/students/{student_id}/guardians/{guardian_id}"): {"relationship": "father"},
    ("POST", "/api/v1/students/{student_id}/enrollments"): {
        "section_id": "01a0df6d-0000-7000-8000-000000000001"
    },
    ("PATCH", "/api/v1/students/{student_id}/enrollments/{enrollment_id}"): {"roll_no": "5"},
    ("POST", "/api/v1/students/{student_id}/enrollments/{enrollment_id}/end"): {},
    ("DELETE", "/api/v1/students/{student_id}/guardians/{guardian_id}"): None,
    # Promotions (FR-TEN-011): the year in the path decides; the target year is never reached.
    ("POST", "/api/v1/academic-years/{year_id}/promotions:preview"): {
        "to_academic_year_id": "01a0df6d-0000-7000-8000-000000000002"
    },
    ("POST", "/api/v1/academic-years/{year_id}/promotions:commit"): {
        "to_academic_year_id": "01a0df6d-0000-7000-8000-000000000002"
    },
    ("POST", "/api/v1/academic-years/{year_id}/promotions:undo"): None,
    ("POST", "/api/v1/documents/{document_id}/versions"): {"upload_id": str(uuid.uuid4())},
    ("PUT", "/api/v1/documents/{document_id}/acl"): {"acl": []},
    ("DELETE", "/api/v1/documents/{document_id}"): None,
    ("PATCH", "/api/v1/documents/{document_id}"): {"title": "Synthetic title"},
    ("POST", "/api/v1/documents/{document_id}/archive"): None,
    ("POST", "/api/v1/documents/{document_id}/unarchive"): None,
    ("POST", "/api/v1/notifications/{notification_id}/read"): None,
    ("POST", "/api/v1/breakglass/requests/{request_id}/approve"): None,
    ("POST", "/api/v1/breakglass/requests/{request_id}/deny"): None,
    ("POST", "/api/v1/breakglass/grants/{grant_id}/revoke"): None,
    ("PUT", "/api/v1/imports/{import_id}/mapping"): {"columns": []},
    ("POST", "/api/v1/imports/{import_id}/validate"): None,
    ("POST", "/api/v1/imports/{import_id}/commit"): {},
    ("POST", "/api/v1/imports/{import_id}/revert"): None,
    # Staged sheet and document sheets (FR-IMP-008, FR-DOC-010, FR-DOC-011).
    ("PATCH", "/api/v1/imports/{import_id}/sheet/rows/{row_no}"): {
        "cells": [{"column": 1, "value": "Synthetica BOLA"}]
    },
    ("POST", "/api/v1/documents/{document_id}/sheet/versions"): {
        "base_version_no": 1,
        "edits": [{"row_no": 2, "column": 0, "value": "Synthetica BOLA"}],
    },
    ("POST", "/api/v1/documents/{document_id}/sheet/export"): {"format": "csv"},
    ("POST", "/api/v1/dq/findings/{finding_id}/resolve"): {"note": "Synthetic note"},
    ("POST", "/api/v1/dq/findings/{finding_id}/waive"): {"reason": "Synthetic reason"},
    ("POST", "/api/v1/change-requests/{change_request_id}/approve"): None,
    ("POST", "/api/v1/change-requests/{change_request_id}/reject"): {
        "reason": "Synthetic BOLA rejection"
    },
    ("POST", "/api/v1/change-requests/{change_request_id}/cancel"): None,
    ("POST", "/api/v1/extraction-items/{item_id}/confirm"): {
        "fields": {"full_name": "Synthetica BOLA"}
    },
    ("POST", "/api/v1/extraction-items/{item_id}/reject"): {"reason": "other"},
    ("POST", "/api/v1/knowledge/queries/{query_id}/feedback"): {"feedback": "helpful"},
    ("POST", "/api/v1/knowledge/verified-answers/{answer_id}/review"): {},
    ("POST", "/api/v1/knowledge/verified-answers/{answer_id}/retire"): None,
    # Certificates (US-1101..US-1105): valid bodies so the request reaches the object lookup.
    ("POST", "/api/v1/students/{student_id}/certificates"): {
        "certificate_type": "bonafide",
        "inputs": {"purpose": "bus_pass"},
    },
    ("POST", "/api/v1/certificates/{certificate_id}/approve"): None,
    ("POST", "/api/v1/certificates/{certificate_id}/reject"): {
        "reason": "Synthetic BOLA rejection"
    },
    ("POST", "/api/v1/certificates/{certificate_id}/withdraw"): None,
    ("POST", "/api/v1/certificates/{certificate_id}/cancel"): {
        "reason": "Synthetic BOLA cancellation"
    },
    ("POST", "/api/v1/certificates/{certificate_id}/duplicates"): {
        "reason": "Synthetic BOLA duplicate reason"
    },
    ("POST", "/api/v1/certificates/{certificate_id}/render"): None,
    # M4: circulars (document ids), suggestions, tasks and notices of school B are 404.
    ("POST", "/api/v1/circulars/{document_id}/read"): {},
    ("POST", "/api/v1/circulars/{document_id}/review"): {},
    ("POST", "/api/v1/circular-suggestions/{suggestion_id}/confirm"): {
        "owner_membership_id": "01920000-0000-7000-8000-000000000001"
    },
    ("POST", "/api/v1/circular-suggestions/{suggestion_id}/dismiss"): {},
    ("PATCH", "/api/v1/tasks/{task_id}"): {"title": "Synthetic"},
    ("POST", "/api/v1/tasks/{task_id}/status"): {"status": "done"},
    ("PATCH", "/api/v1/notices/{notice_id}"): {"title_en": "Synthetic"},
    ("POST", "/api/v1/notices/{notice_id}/approve"): {},
    ("POST", "/api/v1/notices/{notice_id}/render"): {},
    # M5: sections, exams, students, flags and notes of school B are 404.
    ("POST", "/api/v1/sections/{section_id}/attendance"): {
        "entries": [
            {
                "student_id": "01920000-0000-7000-8000-000000000001",
                "on_date": "2026-09-01",
                "status": "present",
            }
        ]
    },
    ("POST", "/api/v1/sections/{section_id}/attendance/sheet"): {
        "document_id": "01920000-0000-7000-8000-000000000001"
    },
    ("POST", "/api/v1/sections/{section_id}/exams/{exam_id}/marks"): {
        "entries": [
            {
                "student_id": "01920000-0000-7000-8000-000000000001",
                "subject": "Maths",
                "max_marks": "50",
                "marks": "1",
            }
        ]
    },
    ("POST", "/api/v1/sections/{section_id}/exams/{exam_id}/marks/sheet"): {
        "document_id": "01920000-0000-7000-8000-000000000001"
    },
    ("POST", "/api/v1/students/{student_id}/flags"): {"indicator": "attendance"},
    ("POST", "/api/v1/insights/flags/{flag_id}/actions"): {"kind": "other"},
    ("POST", "/api/v1/insights/flags/{flag_id}/close"): {"reason": "improved"},
    ("POST", "/api/v1/insights/flags/{flag_id}/assign"): {
        "owner_membership_id": "01920000-0000-7000-8000-000000000001"
    },
    ("POST", "/api/v1/insights/flags/{flag_id}/erase"): {"reason": "parent_request"},
    ("POST", "/api/v1/students/{student_id}/behaviour-notes"): {
        "category": "positive",
        "text": "Synthetic BOLA note",
    },
    ("POST", "/api/v1/behaviour-notes/{note_id}/erase"): {"reason": "parent_request"},
    # M6 Tally connector (ADR-0032; flag on for school A during the call): school B's agent and
    # ledger are 404 like a random id.
    ("POST", "/api/v1/tally/devices/{device_id}/revoke"): None,
    ("POST", "/api/v1/tally/parties/{party_id}/links"): {
        "student_id": "01920000-0000-7000-8000-000000000001"
    },
    ("DELETE", "/api/v1/tally/parties/{party_id}/links/{student_id}"): None,
}


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


SW = _load_students()

# Routes whose permission the owner does not hold (docs/07 §6.2: student.update_nonidentity is
# principal/office_admin/office_staff): probe them as the principal so the request reaches the
# object lookup instead of stopping at 403.
ACTOR: dict[tuple[str, str], str] = dict.fromkeys(
    (
        ("POST", "/api/v1/dq/findings/{finding_id}/resolve"),
        ("POST", "/api/v1/dq/findings/{finding_id}/waive"),
        ("PATCH", "/api/v1/students/{student_id}"),
        ("POST", "/api/v1/students/{student_id}/values"),
        ("POST", "/api/v1/students/{student_id}/values/{value_id}/verify"),
        ("POST", "/api/v1/students/{student_id}/guardians"),
        ("PATCH", "/api/v1/students/{student_id}/guardians/{guardian_id}"),
        ("POST", "/api/v1/students/{student_id}/enrollments"),
        ("PATCH", "/api/v1/students/{student_id}/enrollments/{enrollment_id}"),
        ("POST", "/api/v1/students/{student_id}/enrollments/{enrollment_id}/end"),
        ("DELETE", "/api/v1/students/{student_id}/guardians/{guardian_id}"),
        # Imports (import.run / import.commit are not owner permissions, docs/07 §6.2).
        ("GET", "/api/v1/imports/{import_id}"),
        ("GET", "/api/v1/imports/{import_id}/rows"),
        ("PUT", "/api/v1/imports/{import_id}/mapping"),
        ("POST", "/api/v1/imports/{import_id}/validate"),
        ("POST", "/api/v1/imports/{import_id}/commit"),
        ("POST", "/api/v1/imports/{import_id}/revert"),
        ("GET", "/api/v1/imports/{import_id}/sheet"),
        ("PATCH", "/api/v1/imports/{import_id}/sheet/rows/{row_no}"),
        ("GET", "/api/v1/imports/{import_id}/sheet/export"),
        # Cancelling needs student.identity_change.request (the owner only approves).
        ("POST", "/api/v1/change-requests/{change_request_id}/cancel"),
        # Issuing, previews, duplicates, withdrawals and PDF retries need certificate.issue
        # (the owner approves and reads only, docs/07 §6.2).
        ("POST", "/api/v1/students/{student_id}/certificates"),
        ("GET", "/api/v1/students/{student_id}/certificates/preview"),
        ("POST", "/api/v1/certificates/{certificate_id}/duplicates"),
        ("POST", "/api/v1/certificates/{certificate_id}/withdraw"),
        ("POST", "/api/v1/certificates/{certificate_id}/render"),
        # M5: attendance, marks and insights are not owner permissions (08 PRV-004).
        ("GET", "/api/v1/sections/{section_id}/attendance"),
        ("GET", "/api/v1/sections/{section_id}/attendance/month"),
        ("POST", "/api/v1/sections/{section_id}/attendance"),
        ("POST", "/api/v1/sections/{section_id}/attendance/sheet"),
        ("GET", "/api/v1/sections/{section_id}/exams/{exam_id}/marks"),
        ("POST", "/api/v1/sections/{section_id}/exams/{exam_id}/marks"),
        ("POST", "/api/v1/sections/{section_id}/exams/{exam_id}/marks/sheet"),
        ("GET", "/api/v1/insights/flags/{flag_id}"),
        ("POST", "/api/v1/students/{student_id}/flags"),
        ("POST", "/api/v1/insights/flags/{flag_id}/actions"),
        ("POST", "/api/v1/insights/flags/{flag_id}/close"),
        ("GET", "/api/v1/insights/flags/{flag_id}/owners"),
        ("POST", "/api/v1/insights/flags/{flag_id}/assign"),
        ("POST", "/api/v1/insights/flags/{flag_id}/erase"),
        ("GET", "/api/v1/students/{student_id}/behaviour-notes"),
        ("POST", "/api/v1/students/{student_id}/behaviour-notes"),
        ("POST", "/api/v1/behaviour-notes/{note_id}/erase"),
        ("GET", "/api/v1/students/{student_id}/timeline"),
    ),
    "principal",
)
# Register-photo extraction needs import.run / import.commit (not held by the owner).
ACTOR.update(
    dict.fromkeys(
        (
            ("GET", "/api/v1/extraction-batches/{batch_id}"),
            ("GET", "/api/v1/extraction-items/{item_id}"),
            ("POST", "/api/v1/extraction-items/{item_id}/confirm"),
            ("POST", "/api/v1/extraction-items/{item_id}/reject"),
            # FR-KB-030: the owner cannot manage verified answers; the principal can.
            ("POST", "/api/v1/knowledge/verified-answers/{answer_id}/review"),
            ("POST", "/api/v1/knowledge/verified-answers/{answer_id}/retire"),
        ),
        "office_admin",
    )
)


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


# The admin engine for fixtures that insert synthetic register pages (set per test below).
_ADMIN: list[Engine] = []


@pytest.fixture(autouse=True)
def _remember_admin(admin_engine: Engine) -> None:
    _ADMIN[:] = [admin_engine]


def _b_extraction(w: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """A processed school B batch and one of its pending rows (US-402)."""
    if "bola_extraction_batch" not in w.b.ids:
        x = _load_extraction_support()
        page = x.page_png([x.register_row("Synthetica School B Row")])
        batch_id, items = x.processed_batch(_ADMIN[0], w.b, [page])
        w.b.ids["bola_extraction_batch"] = batch_id
        w.b.ids["bola_extraction_item"] = items[0]
    return w.b.ids["bola_extraction_batch"], w.b.ids["bola_extraction_item"]


def _b_ticket(w: Any) -> uuid.UUID:
    """A support ticket of school B (control-plane storage, reached via app.platform.service)."""
    from app.platform import service as platform_service

    return platform_service.open_ticket_from_tenant(
        w.b.tenant_id,
        w.b.people["owner"].user_id,
        platform_service.TicketCreateSchool(
            category="other", subject="School B ticket", body="Synthetic question"
        ),
    ).id


def _b_document(w: Any) -> uuid.UUID:
    """A school B document created through the real upload -> register path."""
    doc_id: uuid.UUID = D.service_document(w.b.tenant_id, w.b.people["owner"])
    return doc_id


def _b_import(w: Any) -> uuid.UUID:
    """A school B import batch (tests/imports/support.py; created through imports.service)."""
    name = "sos_test_imports_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "imports" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    im = sys.modules[name]
    if "bola_import" not in w.b.ids:
        rows = im.class_list(1)[0]
        w.b.ids["bola_import"] = im.start(None, w.b, im.xlsx_bytes(rows), role="owner")
    value: uuid.UUID = w.b.ids["bola_import"]
    return value


def _b_notification(w: Any) -> uuid.UUID:
    """A notification of school B's owner (FR-NOT-001; created via notifications.service)."""
    from sqlalchemy import text

    from app.core.db import tenant_session
    from app.notifications import service as notifications

    key = f"bola:{uuid.uuid4()}"
    with tenant_session(w.b.tenant_id) as s:
        notifications.notify(
            s,
            tenant_id=w.b.tenant_id,
            recipients=[w.b.people["owner"].membership_id],
            template_key="import.committed",
            params={"import_id": str(uuid.uuid4()), "rows": 1},
            dedupe_key=key,
        )
        value: object = s.execute(
            text("SELECT id FROM ops.notifications WHERE dedupe_key = :k"), {"k": key}
        ).scalar_one()
    return uuid.UUID(str(value))


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


def _dq() -> ModuleType:
    """tests/dq/dq_support.py (runs and findings through the real engine)."""
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


def _changes() -> ModuleType:
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


def _b_dq_finding(w: Any) -> uuid.UUID:
    """An open finding of school B (DQ-003 on a school B student)."""
    SW.configure_keyring()
    finding: uuid.UUID = _dq().high_finding(w.b)
    return finding


def _b_dq_run(w: Any) -> uuid.UUID:
    SW.configure_keyring()
    dq = _dq()
    run_id: uuid.UUID = dq.call(w.b, dq.dq.run_checks).id
    return run_id


def _b_export(w: Any) -> uuid.UUID:
    """A ready export of school B (a student list of its owner; tests/exports/objects.py)."""
    name = "sos_test_exports_objects"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "exports" / "objects.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    if "bola_export" not in w.b.ids:
        SW.ensure_students(w)
        w.b.ids["bola_export"] = sys.modules[name].ready_export(w.b, "owner")
    value: uuid.UUID = w.b.ids["bola_export"]
    return value


def _b_query(w: Any) -> uuid.UUID:
    """A logged question of school B's owner (tests/knowledge/ask_support.py)."""
    name = "sos_test_ask_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "knowledge" / "ask_support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    if "bola_query" not in w.b.ids:
        w.b.ids["bola_query"] = sys.modules[name].query_row(_ADMIN[0], w.b, w.b.people["owner"])
    value: uuid.UUID = w.b.ids["bola_query"]
    return value


def _b_tenant_export(w: Any) -> uuid.UUID:
    """A ready full export of school B made by its owner (tests/admin/support.py)."""
    name = "sos_test_admin_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "admin" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    if "bola_tenant_export" not in w.b.ids:
        w.b.ids["bola_tenant_export"] = sys.modules[name].ready_export(_ADMIN[0], w.b)
    value: uuid.UUID = w.b.ids["bola_tenant_export"]
    return value


def _b_verified_answer(w: Any) -> uuid.UUID:
    """An active verified answer of school B (synthetic row; FR-KB-030)."""
    if "bola_verified" not in w.b.ids:
        answer_id = uuid.uuid4()
        with _ADMIN[0].begin() as c:
            c.execute(
                text(
                    "INSERT INTO kb.verified_answers (id, tenant_id, question_canonical, language, "
                    "answer_text, citations, verified_by, verified_at) VALUES (:i, :t, "
                    "'Synthetic question?', 'en', 'Synthetic answer.', "
                    "CAST(:c AS jsonb), :m, now())"
                ),
                {
                    "i": answer_id,
                    "t": w.b.tenant_id,
                    "c": '[{"source": "sos://doc/' + str(uuid.uuid4()) + '/v1#p1", '
                    '"cited_text": "synthetic"}]',
                    "m": w.b.people["owner"].membership_id,
                },
            )
        w.b.ids["bola_verified"] = answer_id
    value: uuid.UUID = w.b.ids["bola_verified"]
    return value


def _b_certificate(w: Any) -> uuid.UUID:
    """An issued bonafide certificate of school B (real services; the B owner acting with the
    office admin's permissions, since school B has no office admin)."""
    if "bola_certificate" not in w.b.ids:
        name = "sos_test_certificates_support"
        if name not in sys.modules:
            path = Path(__file__).resolve().parents[1] / "certificates" / "support.py"
            spec = importlib.util.spec_from_file_location(name, path)
            assert spec is not None
            assert spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        from app.certificates import service as certificates
        from app.certificates.schemas import CertificateRequest

        cert = sys.modules[name]
        cert.install()
        student = cert.student(w.b)
        out = cert.call(
            w.b,
            w.b.people["owner"],
            "office_admin",
            certificates.request_certificate,
            student,
            CertificateRequest(certificate_type="bonafide", inputs={"purpose": "bus_pass"}),
        )
        w.b.ids["bola_certificate"] = out.id
    value: uuid.UUID = w.b.ids["bola_certificate"]
    return value


def _circ() -> ModuleType:
    """tests/circulars/support.py (circulars, tasks and notices through the real services)."""
    name = "sos_test_circulars_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "circulars" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _b_suggestion(w: Any) -> uuid.UUID:
    """A suggested deadline of a circular of school B (M4, FR-CIR-004)."""
    if "bola_suggestion" not in w.b.ids:
        _doc, _reading, suggestion = _circ().fresh_circular(
            _ADMIN[0], w.b, reading="ready", suggestion=True
        )
        w.b.ids["bola_suggestion"] = suggestion
    value: uuid.UUID = w.b.ids["bola_suggestion"]
    return value


def _b_task(w: Any) -> uuid.UUID:
    """A task of school B (FR-TASK-002)."""
    if "bola_task" not in w.b.ids:
        w.b.ids["bola_task"] = _circ().task(w.b)
    value: uuid.UUID = w.b.ids["bola_task"]
    return value


def _b_notice(w: Any) -> uuid.UUID:
    """An approved parent notice of school B with rendered files (FR-NOTICE-006)."""
    if "bola_notice" not in w.b.ids:
        w.b.ids["bola_notice"] = _circ().rendered_notice(_ADMIN[0], w.b)[0]
    value: uuid.UUID = w.b.ids["bola_notice"]
    return value


def _m5() -> ModuleType:
    """tests/insights/support.py (M5 exams, flags and notes through the real services)."""
    name = "sos_test_insights_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "insights" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _tally() -> ModuleType:
    """tests/tally/support.py (Tally connector rows of a school, the connector flag)."""
    name = "sos_test_tally_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "tally" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _b_tally(w: Any, key: str) -> uuid.UUID:
    """An enrolled agent and a synced ledger of school B (M6, FR-TALLY-002, FR-TALLY-006)."""
    if "bola_tally_party" not in w.b.ids:
        ids = _tally().seed_objects(_ADMIN[0], w.b.tenant_id, w.b.people["owner"].user_id)
        w.b.ids["bola_tally_device"] = ids["device"]
        w.b.ids["bola_tally_party"] = ids["party"]
    value: uuid.UUID = w.b.ids[key]
    return value


def _tally_flag(path: str, w: Any) -> contextlib.AbstractContextManager[None]:
    """Tally routes reach the object lookup only with school A's connector flag on."""
    if path.startswith("/api/v1/tally/"):
        flag: contextlib.AbstractContextManager[None] = _tally().flag_on(_ADMIN[0], w.a.tenant_id)
        return flag
    return contextlib.nullcontext()


PARAM_TO_B: dict[str, Callable[[Any], uuid.UUID]] = {
    "user_id": lambda w: w.b.people["target"].user_id,
    "year_id": lambda w: w.b.ids["year"],
    "class_id": lambda w: w.b.ids["class_ix"],
    "section_id": lambda w: w.b.ids["section_9a"],
    "ticket_id": _b_ticket,
    # Students: every id in these paths is replaced by school B's student id (see _fill).
    "student_id": lambda w: SW.ensure_students(w)["b_sb"],
    "value_id": lambda w: SW.ensure_students(w)["b_sb"],
    "guardian_id": lambda w: SW.ensure_students(w)["b_gb"],
    "enrollment_id": lambda w: SW.ensure_students(w)["b_sb"],
    "document_id": _b_document,
    "notification_id": _b_notification,
    # Break-glass (US-103): a pending request / an active grant of school B.
    "request_id": lambda w: _bg().pending_grant(w.b.tenant_id),
    "grant_id": lambda w: _bg().active_grant(w.b.tenant_id, w.b.people["owner"]),
    "import_id": _b_import,
    # Data quality (FR-DQ-*): a run and a finding of school B.
    "run_id": _b_dq_run,
    "finding_id": _b_dq_finding,
    # Change requests (US-601): a pending request of school B (real services only).
    "change_request_id": lambda w: _changes().pending(w.b),
    # Register-photo extraction (US-402): a batch / a pending row of school B.
    "batch_id": lambda w: _b_extraction(w)[0],
    "item_id": lambda w: _b_extraction(w)[1],
    # Exports (US-501 AC4, US-901): a ready export of school B.
    "export_id": _b_export,
    # Full data export (FR-ADM-001): a ready full export of school B.
    "tenant_export_id": _b_tenant_export,
    # Knowledge (FR-KB-012): a logged question of school B.
    "query_id": _b_query,
    # Knowledge (FR-KB-030): a verified answer of school B.
    "answer_id": _b_verified_answer,
    # Certificates (US-1101): an issued certificate of school B.
    "certificate_id": _b_certificate,
    # Circulars, tasks and notices (M4): a suggestion, a task and a notice of school B.
    "suggestion_id": _b_suggestion,
    "task_id": _b_task,
    "notice_id": _b_notice,
    # M5: an exam, a flag and a behaviour note of school B.
    "exam_id": lambda w: _m5().b_exam(w),
    "flag_id": lambda w: _m5().b_flag(w),
    "note_id": lambda w: _m5().b_note(w),
    # Tally connector (M6): an agent and a ledger of school B.
    "device_id": lambda w: _b_tally(w, "bola_tally_device"),
    "party_id": lambda w: _b_tally(w, "bola_tally_party"),
}


def _id_routes() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for rc in iter_route_contexts(create_app().routes):
        # Control-plane routes are operator-only and not tenant scoped (tests/platform).
        if str(rc.path).startswith(("/api/v1/platform/", "/api/v1/fleet/")):
            continue
        if isinstance(rc.original_route, APIRoute) and "{" in str(rc.path):
            out.extend((m, str(rc.path)) for m in sorted(rc.methods or ()))
    return sorted(out)


ID_ROUTES = _id_routes()


# Required query parameters of ID routes, so the request reaches the object lookup.
QUERY: dict[tuple[str, str], dict[str, str]] = {
    ("GET", "/api/v1/students/{student_id}/certificates/preview"): {"certificate_type": "bonafide"},
    ("GET", "/api/v1/sections/{section_id}/attendance/month"): {"month": "2026-09"},
}


# Path parameters that are not object ids (a row number inside the object in the path).
PLAIN_PARAMS: dict[str, str] = {"row_no": "2"}


def _fill(path: str, value: uuid.UUID) -> str:
    for param in PARAM_TO_B:
        path = path.replace("{" + param + "}", str(value))
    for param, plain in PLAIN_PARAMS.items():
        path = path.replace("{" + param + "}", plain)
    return path


def _param(path: str) -> str:
    return next(p for p in PARAM_TO_B if "{" + p + "}" in path)


def test_SEC_015_every_id_route_is_covered() -> None:
    assert ID_ROUTES
    for method, path in ID_ROUTES:
        assert _param(path)
        if method != "GET":
            assert (method, path) in BODIES or path.endswith("/make-current"), path


@pytest.mark.parametrize("key", ID_ROUTES, ids=lambda k: f"{k[0]} {k[1]}")
def test_SEC_001_other_school_ids_are_404(
    world: Any, api: Any, admin_engine: Engine, key: tuple[str, str]
) -> None:
    method, template = key
    b_id = PARAM_TO_B[_param(template)](world)
    before = W.audit_events(admin_engine, world.b.tenant_id)
    bodies = []
    for target in (b_id, uuid.uuid4()):
        with _tally_flag(template, world):
            res = api.call(
                world.person(ACTOR.get(key, "owner")),
                method,
                _fill(template, target),
                json=BODIES.get(key),
                params=QUERY.get(key),
                headers={"If-Match": 'W/"1"'},
            )
        assert res.status_code == 404, (key, res.text)
        body = res.json()
        bodies.append({k: body.get(k) for k in ("status", "code", "title", "detail")})
    assert bodies[0] == bodies[1], "another school's ID is indistinguishable from a random one"
    assert W.audit_events(admin_engine, world.b.tenant_id) == before, "school B unchanged"


@pytest.mark.parametrize(
    ("role", "path_key"),
    [
        ("class_teacher", "section_9c"),
        ("class_teacher", "section_10a"),
        ("teacher", "section_9a"),
        ("teacher", "section_9c"),
    ],
)
def test_SEC_015_section_outside_scope_is_404(
    world: Any, api: Any, role: str, path_key: str
) -> None:
    res = api.call(world.person(role), "GET", f"/api/v1/sections/{world.a.ids[path_key]}")
    assert res.status_code == 404


@pytest.mark.parametrize(("role", "klass"), [("class_teacher", "class_x"), ("teacher", "class_ix")])
def test_SEC_015_class_outside_scope_is_404(world: Any, api: Any, role: str, klass: str) -> None:
    res = api.call(world.person(role), "GET", f"/api/v1/classes/{world.a.ids[klass]}")
    assert res.status_code == 404


def test_SEC_015_in_scope_objects_are_visible(world: Any, api: Any) -> None:
    ct = world.person("class_teacher")
    assert api.call(ct, "GET", f"/api/v1/sections/{world.a.ids['section_9a']}").status_code == 200
    assert api.call(ct, "GET", f"/api/v1/classes/{world.a.ids['class_ix']}").status_code == 200


@pytest.mark.parametrize("role", ["owner", "principal", "office_admin"])
def test_SEC_001_all_exports_list_never_shows_other_school(world: Any, api: Any, role: str) -> None:
    """ADR-0021: ``export.read_all`` means every export of *this* school; school B's export
    (and its requester) never appear, and its id stays 404 for the download_any holder."""
    b_export = str(_b_export(world))
    res = api.call(
        world.person(role), "GET", "/api/v1/exports", params={"requested_by": "all", "limit": 200}
    )
    assert res.status_code == 200, res.text
    items = res.json()["data"]
    assert b_export not in {e["id"] for e in items}
    b_members = {str(p.membership_id) for p in world.b.people.values()}
    assert not {e["requested_by"]["membership_id"] for e in items} & b_members


def test_SEC_001_full_export_list_never_shows_other_school(world: Any, api: Any) -> None:
    """FR-ADM-001: the owner lists this school's full exports only; school B's ready export is
    neither listed nor downloadable (404 like a random id)."""
    b_export = str(_b_tenant_export(world))
    owner = world.person("owner")
    res = api.call(owner, "GET", "/api/v1/admin/tenant-export", params={"limit": 200})
    assert res.status_code == 200, res.text
    assert b_export not in {e["id"] for e in res.json()["data"]}
    for suffix in ("", "/download-url"):
        other = api.call(owner, "GET", f"/api/v1/admin/tenant-export/{b_export}{suffix}")
        assert other.status_code == 404


@pytest.mark.parametrize(
    "path", ["/api/v1/users", "/api/v1/academic-years", "/api/v1/classes", "/api/v1/sections"]
)
def test_SEC_001_lists_never_show_other_school(world: Any, api: Any, path: str) -> None:
    res = api.call(world.person("owner"), "GET", path, params={"limit": 200})
    assert res.status_code == 200
    b_ids = {str(v) for v in world.b.ids.values()}
    b_ids |= {str(p.user_id) for p in world.b.people.values()}
    assert not {item["id"] for item in res.json()["data"]} & b_ids


def test_SEC_001_certificate_lists_and_registers_never_show_other_school(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """US-1101, FR-REG-001: school B's certificates never appear in school A's list or
    registers (and B's student cannot be used as a filter)."""
    b_certificate = _b_certificate(world)
    owner = world.person("owner")
    res = api.call(owner, "GET", "/api/v1/certificates", params={"limit": 200})
    assert res.status_code == 200
    assert str(b_certificate) not in {item["id"] for item in res.json()["data"]}
    b_student = api.call(
        owner,
        "GET",
        "/api/v1/certificates",
        params={"student_id": str(SW.ensure_students(world)["b_sb"])},
    )
    assert b_student.status_code == 200
    assert b_student.json()["data"] == []
    with admin_engine.connect() as c:
        b_name: str | None = c.execute(
            text("SELECT content ->> 'student_name' FROM sis.certificates WHERE id = :i"),
            {"i": b_certificate},
        ).scalar_one()
    assert b_name
    for path in ("/api/v1/registers/certificates", "/api/v1/registers/admission-withdrawal"):
        page = api.call(owner, "GET", path)
        assert page.status_code == 200
        assert b_name not in page.text
    b_year = api.call(
        owner,
        "GET",
        "/api/v1/registers/certificates",
        params={"academic_year_id": str(world.b.ids["year"])},
    )
    assert b_year.status_code == 404


def test_SEC_001_bodies_cannot_reference_other_school(world: Any, api: Any) -> None:
    owner = world.person("owner")
    res = api.call(
        owner,
        "POST",
        "/api/v1/sections",
        json={
            "academic_year_id": str(world.a.ids["year"]),
            "class_id": str(world.b.ids["class_ix"]),
            "name": "Z",
        },
    )
    assert res.status_code == 422
    res = api.call(
        owner,
        "POST",
        "/api/v1/sections",
        json={
            "academic_year_id": str(world.a.ids["year"]),
            "class_id": str(world.a.ids["class_ix"]),
            "name": "Y",
            "class_teacher_membership_id": str(world.b.people["target"].membership_id),
        },
    )
    assert res.status_code == 422
    res = api.call(
        owner,
        "PUT",
        f"/api/v1/users/{world.a.people['target'].user_id}/scopes",
        json={"scopes": [{"type": "class", "ref": str(world.b.ids["class_ix"])}]},
    )
    assert res.status_code == 422


def test_SEC_001_other_school_owner_sees_only_own_tenant(world: Any, api: Any) -> None:
    b_owner = world.b.people["owner"]
    me = api.call(b_owner, "GET", "/api/v1/me").json()
    assert me["tenant_id"] == str(world.b.tenant_id)
    tenant = api.call(b_owner, "GET", "/api/v1/tenant").json()
    assert tenant["id"] == str(world.b.tenant_id)
    res = api.call(b_owner, "GET", "/api/v1/me", tenant=world.a.tenant_id)
    assert res.status_code == 403


# --- students (SEC-015, docs/12 §4.3) -------------------------------------------------------

STUDENT_READS = ("", "/values", "/guardians")


@pytest.mark.parametrize(
    ("role", "student"),
    [("class_teacher", "s9c"), ("class_teacher", "s10a"), ("teacher", "s9a"), ("teacher", "s9c")],
)
def test_SEC_015_student_outside_scope_is_404(
    world: Any, api: Any, admin_engine: Engine, role: str, student: str
) -> None:
    """A class teacher of 9A asking for a 9C student gets 404, exactly like a random id."""
    ids = SW.ensure_students(world)
    who = world.person(role)
    before = W.audit_events(admin_engine, world.a.tenant_id, "student.sensitive_revealed")
    for suffix in STUDENT_READS:
        hidden = api.call(who, "GET", f"/api/v1/students/{ids[student]}{suffix}")
        random = api.call(who, "GET", f"/api/v1/students/{uuid.uuid4()}{suffix}")
        assert hidden.status_code == random.status_code == 404, (suffix, hidden.text)
        assert hidden.json()["detail"] == random.json()["detail"]
    if role == "class_teacher":
        res = api.call(
            who,
            "POST",
            f"/api/v1/students/{ids[student]}/sensitive-reveal",
            json={"attribute_key": "health_notes"},
        )
        assert res.status_code == 404
    after = W.audit_events(admin_engine, world.a.tenant_id, "student.sensitive_revealed")
    assert after == before, "nothing revealed"


def test_SEC_015_student_lists_respect_scope(world: Any, api: Any) -> None:
    ids = SW.ensure_students(world)
    res = api.call(world.person("class_teacher"), "GET", "/api/v1/students", params={"limit": 200})
    got = {item["id"] for item in res.json()["data"]}
    assert str(ids["s9a"]) in got
    assert not got & {str(ids["s9c"]), str(ids["s10a"])}


def test_SEC_015_student_search_body_respects_scope(world: Any, api: Any) -> None:
    """POST /students/search (SEC-008) applies the same section scope as the list."""
    ids = SW.ensure_students(world)
    for body in ({"limit": 200}, {"query": "synthetica", "limit": 200}):
        res = api.call(world.person("class_teacher"), "POST", "/api/v1/students/search", json=body)
        assert res.status_code == 200, res.text
        got = {item["id"] for item in res.json()["data"]}
        assert str(ids["s9a"]) in got
        assert not got & {str(ids["s9c"]), str(ids["s10a"])}
    hidden = api.call(
        world.person("class_teacher"),
        "POST",
        "/api/v1/students/search",
        json={"section_id": str(world.a.ids["section_9c"])},
    )
    assert hidden.json()["data"] == []


def test_SEC_001_student_lists_never_show_other_school(world: Any, api: Any) -> None:
    ids = SW.ensure_students(world)
    for query in (None, "synthetica", "9a"):
        params: dict[str, Any] = {"limit": 200}
        if query:
            params["query"] = query
        res = api.call(world.person("owner"), "GET", "/api/v1/students", params=params)
        assert res.status_code == 200
        got = {item["id"] for item in res.json()["data"]}
        assert str(ids["b_sb"]) not in got
        searched = api.call(world.person("owner"), "POST", "/api/v1/students/search", json=params)
        assert searched.status_code == 200
        assert str(ids["b_sb"]) not in {item["id"] for item in searched.json()["data"]}


def test_SEC_001_student_bodies_cannot_reference_other_school(world: Any, api: Any) -> None:
    ids = SW.ensure_students(world)
    admin = world.person("office_admin")
    enrol = api.call(
        admin,
        "POST",
        f"/api/v1/students/{ids['mover']}/enrollments",
        json={"section_id": str(world.b.ids["section_9a"])},
    )
    assert enrol.status_code == 422
    link = api.call(
        admin,
        "POST",
        f"/api/v1/students/{ids['s9c']}/guardians",
        json={"relationship": "father", "guardian_id": str(ids["b_gb"])},
    )
    assert link.status_code == 422
    create = api.call(
        admin,
        "POST",
        "/api/v1/students",
        json={
            "values": [
                {
                    "attribute_key": "full_name",
                    "source": "admission_register",
                    "value": "Synthetica X",
                }
            ],
            "section_id": str(world.b.ids["section_9a"]),
        },
    )
    assert create.status_code == 422


def test_SEC_001_change_request_lists_never_show_other_school(world: Any, api: Any) -> None:
    b_request = _changes().pending(world.b)
    res = api.call(world.person("owner"), "GET", "/api/v1/change-requests", params={"limit": 200})
    assert res.status_code == 200
    assert str(b_request) not in {r["id"] for r in res.json()["data"]}
    by_student = api.call(
        world.person("owner"),
        "GET",
        "/api/v1/change-requests",
        params={"student_id": str(SW.ensure_students(world)["b_sb"])},
    )
    assert by_student.json()["data"] == []


def test_SEC_001_change_request_bodies_cannot_reference_other_school(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """A school B student or evidence document in a school A request is unknown (404/422)."""
    cr = _changes()
    a_doc = cr.evidence(admin_engine, world.a, world.a.people["owner"])
    b_doc = cr.evidence(admin_engine, world.b, world.b.people["owner"])
    body = {
        "attribute_key": "dob",
        "new_value": "2012-03-15",
        "reason": "Synthetic cross-school probe",
    }
    admin = world.person("office_admin")
    other_student = api.call(
        admin,
        "POST",
        "/api/v1/change-requests",
        json={**body, "student_id": str(cr.student(world.b)), "evidence_document_id": str(a_doc)},
    )
    assert other_student.status_code == 404
    other_doc = api.call(
        admin,
        "POST",
        "/api/v1/change-requests",
        json={**body, "student_id": str(cr.student(world.a)), "evidence_document_id": str(b_doc)},
    )
    assert (other_doc.status_code, other_doc.json()["code"]) == (422, "evidence_required")


# --- register-photo extraction (US-402, SEC-001) --------------------------------------------


def test_SEC_001_extraction_lists_never_show_other_school(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    b_batch, b_item = _b_extraction(world)
    who = world.person("office_admin")
    batches = api.call(who, "GET", "/api/v1/extraction-batches", params={"limit": 200})
    assert batches.status_code == 200
    assert str(b_batch) not in {b["id"] for b in batches.json()["data"]}
    for params in ({"limit": 200}, {"batch_id": str(b_batch), "limit": 200}):
        items = api.call(who, "GET", "/api/v1/extraction-items", params=params)
        assert items.status_code == 200
        assert str(b_item) not in {i["id"] for i in items.json()["data"]}


def test_SEC_001_extraction_bodies_cannot_reference_other_school(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    x = _load_extraction_support()
    who = world.person("office_admin")
    b_doc = x.register_scan(
        admin_engine,
        world.b.tenant_id,
        world.b.people["owner"].user_id,
        x.page_png([x.register_row("Synthetica B Page")]),
    )
    res = api.call(who, "POST", "/api/v1/extraction-batches", json={"document_ids": [str(b_doc)]})
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "not_found"
    item = x.pending_item(admin_engine, world.a)
    b_student = SW.ensure_students(world)["b_sb"]
    link = api.call(
        who,
        "POST",
        f"/api/v1/extraction-items/{item}/confirm",
        json={"student_id": str(b_student), "fields": {"nationality": "Indian"}},
    )
    assert link.status_code == 404
    section = api.call(
        who,
        "POST",
        f"/api/v1/extraction-items/{item}/confirm",
        json={
            "fields": {"full_name": "Synthetica Cross School"},
            "section_id": str(world.b.ids["section_9a"]),
        },
    )
    assert section.status_code == 422
    assert x.row_of(admin_engine, "sis.extraction_items", item)["status"] == "pending_review"


def test_SEC_001_knowledge_never_shows_or_cites_another_school(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """FR-KB-010: search results and verified answers stay inside the school; a verified answer
    cannot cite another school's document (422 citation_not_found, same as a random id)."""
    kb = sys.modules.get("sos_test_ask_support")
    if kb is None:
        _b_query(world)
        kb = sys.modules["sos_test_ask_support"]
    SW.configure_keyring()
    b_doc = kb.shared_document(admin_engine, world.b)
    owner = world.person("owner")
    found = api.call(
        owner, "POST", "/api/v1/knowledge/search", json={"query": "parent-teacher meeting"}
    )
    assert found.status_code == 200
    assert str(b_doc) not in {r["document_id"] for r in found.json()["data"]}
    codes = []
    for doc in (b_doc, uuid.uuid4()):
        res = api.call(
            world.person("principal"),
            "POST",
            "/api/v1/knowledge/verified-answers",
            json={
                "question": "When is the meeting?",
                "language": "en",
                "answer_text": "On 18/10/2026.",
                "citations": [{"source": f"sos://doc/{doc}/v1#p1", "cited_text": "18/10/2026"}],
            },
        )
        assert res.status_code == 422, res.text
        codes.append(res.json()["errors"][0]["code"])
    assert codes == ["citation_not_found", "citation_not_found"]


# --- M5: class teachers reach only their section's records and insights (FR-EW-011, SEC-015) ---


@pytest.mark.parametrize("section", ["section_9c", "section_10a"])
def test_SEC_015_attendance_and_marks_outside_scope_are_404(
    world: Any, api: Any, section: str
) -> None:
    ct = world.person("class_teacher")
    exam_id = _m5().world_exam(world)
    base = f"/api/v1/sections/{world.a.ids[section]}"
    rnd = f"/api/v1/sections/{uuid.uuid4()}"
    for suffix in ("/attendance", f"/exams/{exam_id}/marks"):
        hidden = api.call(ct, "GET", base + suffix)
        random = api.call(ct, "GET", rnd + suffix)
        assert hidden.status_code == random.status_code == 404, (suffix, hidden.text)
        assert hidden.json()["detail"] == random.json()["detail"]


@pytest.mark.parametrize("student", ["s9c", "s10a"])
def test_SEC_015_insights_of_students_outside_scope_are_404(
    world: Any, api: Any, admin_engine: Engine, student: str
) -> None:
    """A 9A class teacher cannot read, flag or note a 9C/10A student, nor open their flag."""
    ids = SW.ensure_students(world)
    ct = world.person("class_teacher")
    flag = _m5().world_manual_flag(world, student)
    before = W.audit_events(admin_engine, world.a.tenant_id, "insights.viewed")
    for method, path, body in (
        ("GET", f"/api/v1/students/{ids[student]}/timeline", None),
        ("GET", f"/api/v1/students/{ids[student]}/behaviour-notes", None),
        (
            "POST",
            f"/api/v1/students/{ids[student]}/behaviour-notes",
            {"category": "concern", "text": "Synthetic"},
        ),
        ("POST", f"/api/v1/students/{ids[student]}/flags", {"indicator": "behaviour"}),
        ("GET", f"/api/v1/insights/flags/{flag.id}", None),
        ("POST", f"/api/v1/insights/flags/{flag.id}/actions", {"kind": "other"}),
    ):
        res = api.call(ct, method, path, json=body)
        assert res.status_code == 404, (path, res.text)
    listed = api.call(ct, "GET", "/api/v1/insights/flags", params={"view": "all", "limit": 200})
    assert str(flag.id) not in {f["id"] for f in listed.json()["data"]}
    after = W.audit_events(admin_engine, world.a.tenant_id, "insights.viewed")
    # Only the (empty-of-that-flag) list view was recorded; nothing of the hidden student.
    assert len(after) == len(before) + 1


def test_SEC_001_flag_lists_never_show_other_school(world: Any, api: Any) -> None:
    b_flag = _m5().b_flag(world)
    res = api.call(
        world.person("principal"),
        "GET",
        "/api/v1/insights/flags",
        params={"view": "all", "limit": 200},
    )
    assert res.status_code == 200
    assert str(b_flag) not in {f["id"] for f in res.json()["data"]}


def test_SEC_001_tally_lists_never_show_other_school(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """M6 (ADR-0032): school B's agent, ledger and dues never appear in school A's lists."""
    b_party = str(_b_tally(world, "bola_tally_party"))
    b_device = str(_b_tally(world, "bola_tally_device"))
    owner = world.person("owner")
    with _tally().flag_on(admin_engine, world.a.tenant_id):
        devices = api.call(owner, "GET", "/api/v1/tally/devices")
        parties = api.call(owner, "GET", "/api/v1/tally/parties", params={"limit": 200})
        dues = api.call(owner, "GET", "/api/v1/tally/dues", params={"limit": 200})
        status = api.call(owner, "GET", "/api/v1/tally/status")
    assert devices.status_code == 200
    assert parties.status_code == 200
    assert dues.status_code == 200
    assert b_device not in {d["id"] for d in devices.json()}
    assert b_party not in {p["id"] for p in parties.json()["data"]}
    assert "Synthetic Other School Ledger" not in parties.text + dues.text
    # The status counts exactly the ledgers school A can list (none of school B).
    assert status.json()["parties"] == len(parties.json()["data"])
