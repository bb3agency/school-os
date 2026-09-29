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
from sqlalchemy import Engine, text

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


def _load_ask_support() -> ModuleType:
    """tests/knowledge/ask_support.py (indexed documents and logged questions)."""
    name = "sos_test_ask_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "knowledge" / "ask_support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


KB = _load_ask_support()


def _kb_ask(w: Any, r: str, a: Engine) -> Request:
    SW.configure_keyring()
    KB.shared_document(a, w.a)
    return (
        "/api/v1/knowledge/ask",
        {"question": "When is the parent-teacher meeting?", "session_id": str(uuid.uuid4())},
        {},
    )


def _kb_feedback(w: Any, r: str, a: Engine) -> Request:
    query_id = KB.query_row(a, w.a, w.person(r))
    return f"/api/v1/knowledge/queries/{query_id}/feedback", {"feedback": "helpful"}, {}


def _kb_verified(w: Any, r: str, a: Engine) -> Request:
    doc = KB.shared_document(a, w.a)
    body = {
        "question": f"When is the parent-teacher meeting? {uuid.uuid4().hex[:6]}",
        "language": "en",
        "answer_text": "On 18/10/2026 at 10:00.",
        "citations": [{"source": f"sos://doc/{doc}/v1#p1", "cited_text": "18/10/2026 at 10:00"}],
    }
    return "/api/v1/knowledge/verified-answers", body, {}


def _kb_verified_row(w: Any, a: Engine) -> uuid.UUID:
    """A fresh active verified answer of school A citing the shared circular (FR-KB-030)."""
    doc = KB.shared_document(a, w.a)
    answer_id = uuid.uuid4()
    with a.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.verified_answers (id, tenant_id, question_canonical, language, "
                "answer_text, citations, verified_by, verified_at) VALUES (:i, :t, "
                "'When is the parent-teacher meeting?', 'en', 'On 18/10/2026 at 10:00.', "
                "CAST(:c AS jsonb), :m, now())"
            ),
            {
                "i": answer_id,
                "t": w.a.tenant_id,
                "c": '[{"source": "sos://doc/' + str(doc) + '/v1#p1", '
                '"cited_text": "18/10/2026 at 10:00"}]',
                "m": w.a.people["owner"].membership_id,
            },
        )
    return answer_id


def _kb_manage(action: str) -> Builder:
    def build(w: Any, r: str, a: Engine) -> Request:
        answer_id = _kb_verified_row(w, a)
        body: dict[str, Any] | None = {} if action == "review" else None
        path = f"/api/v1/knowledge/verified-answers/{answer_id}/{action}"
        return path, body, _if_match(1)

    return build


def _load_promotion_support() -> ModuleType:
    """tests/students/promotion_support.py (fresh year pairs and promotions, real services)."""
    name = "sos_test_promotion_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "students" / "promotion_support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


PR = _load_promotion_support()


def _load_circulars_support() -> ModuleType:
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


CI = _load_circulars_support()


def _circular_read(w: Any, r: str, a: Engine) -> Request:
    document_id, _, _ = CI.fresh_circular(a, w.a)
    return f"/api/v1/circulars/{document_id}/read", {}, {}


def _circular_review(w: Any, r: str, a: Engine) -> Request:
    document_id, _, _ = CI.fresh_circular(a, w.a, reading="needs_review")
    return f"/api/v1/circulars/{document_id}/review", {}, _if_match(1)


def _suggestion(action: str) -> Builder:
    def build(w: Any, r: str, a: Engine) -> Request:
        _, _, suggestion_id = CI.fresh_circular(a, w.a, reading="ready", suggestion=True)
        body: dict[str, Any] = {}
        if action == "confirm":
            body = {"owner_membership_id": str(w.person(r).membership_id)}
        path = f"/api/v1/circular-suggestions/{suggestion_id}/{action}"
        return path, body, _if_match(1)

    return build


def _own_task(w: Any, r: str) -> uuid.UUID:
    task_id: uuid.UUID = CI.task(w.a, owner=w.person(r), by="owner")
    return task_id


def _notice(state: str) -> Builder:
    def build(w: Any, r: str, a: Engine) -> Request:
        if state == "draft":
            notice_id, version = CI.complete_notice(w.a)
            return (
                f"/api/v1/notices/{notice_id}",
                {"title_en": "Sports day 2026"},
                _if_match(version),
            )
        if state == "approve":
            notice_id, version = CI.complete_notice(w.a)
            return f"/api/v1/notices/{notice_id}/approve", {}, _if_match(version)
        if state == "render":
            notice_id, version = CI.rendered_notice(a, w.a, state="failed")
            return f"/api/v1/notices/{notice_id}/render", {}, _if_match(version)
        notice_id, _version = CI.rendered_notice(a, w.a)
        return f"/api/v1/notices/{notice_id}/download-url?format=png", None, {}

    return build


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


_STRUCTURE_TABLES = {
    "academic-years": "core.academic_years",
    "classes": "core.classes",
    "sections": "core.sections",
}


def _fresh_structure(w: Any, kind: str, *, archived: bool) -> uuid.UUID:
    """A new school A year/class/section (optionally archived) so archiving never touches the
    shared world (US-202, 0023_api_gaps)."""
    owner = w.a.people["owner"]
    with W.tenant_session(w.a.tenant_id, owner.user_id) as db:
        if kind == "academic-years":
            y = next(_years)
            row: Any = W.tenancy.create_academic_year(
                db,
                W.AcademicYearCreate(
                    label=f"{y}-{(y + 1) % 100:02d}",
                    starts_on=f"{y}-06-01",
                    ends_on=f"{y + 1}-03-31",
                ),
            )
            archive: Any = W.tenancy.archive_academic_year
        elif kind == "classes":
            row = W.tenancy.create_class(
                db,
                W.ClassCreate(
                    code=W.unique("A")[:12],
                    display_en="Matrix archive class",
                    display_te="తరగతి",
                    sort_order=950,
                ),
            )
            archive = W.tenancy.archive_class
        else:
            row = W.tenancy.create_section(
                db,
                W.SectionCreate(
                    academic_year_id=w.a.ids["year"],
                    class_id=w.a.ids["class_ix"],
                    name=W.unique("R")[:10],
                ),
            )
            archive = W.tenancy.archive_section
        if archived:
            archive(db, row.id, archived=True, expected_version=row.version)
    value: uuid.UUID = row.id
    return value


def _archive(kind: str, action: str) -> Builder:
    def build(w: Any, r: str, a: Engine) -> Request:
        rid = _fresh_structure(w, kind, archived=action == "unarchive")
        version = W.version_of(a, _STRUCTURE_TABLES[kind], rid)
        return f"/api/v1/{kind}/{rid}/{action}", None, _if_match(version)

    return build


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


def _fresh_enrolment(w: Any, r: str, a: Engine) -> tuple[uuid.UUID, uuid.UUID, int]:
    """A new student in the role's scope (9A / class X) and its enrolment (id, version)."""
    section = "section_10a" if r == "teacher" else "section_9a"
    sid: uuid.UUID = SW.create(w.a, name="Synthetica Matrix Enrolment", section_key=section)
    with a.connect() as c:
        row = c.execute(
            text("SELECT id, version FROM sis.enrollments WHERE student_id = :s"), {"s": sid}
        ).one()
    return sid, row.id, int(row.version)


def _enrolment_patch(w: Any, r: str, a: Engine) -> Request:
    sid, eid, version = _fresh_enrolment(w, r, a)
    return f"/api/v1/students/{sid}/enrollments/{eid}", {"roll_no": "5"}, _if_match(version)


def _enrolment_end(w: Any, r: str, a: Engine) -> Request:
    sid, eid, version = _fresh_enrolment(w, r, a)
    return f"/api/v1/students/{sid}/enrollments/{eid}/end", {}, _if_match(version)


def _guardian_delete(w: Any, r: str, a: Engine) -> Request:
    sid, _, _ = _fresh_enrolment(w, r, a)
    gid = SW.add_guardian(w.a, sid, full_name="Synthetica Matrix Unlinked Guardian")
    return f"/api/v1/students/{sid}/guardians/{gid}", None, _if_match(1)


# --- promotions (FR-TEN-011): tenant.structure.manage, fresh year pairs per request ---------


def _promotion_pair(w: Any, *, committed: bool) -> Any:
    PR.SW.configure_keyring()
    if committed:
        return PR.committed(w.a)
    pair = PR.year_pair(w.a)
    PR.enrolled(w.a, pair.section("from", "IX"))
    return pair


def _promotion(action: str, *, committed: bool = False) -> Builder:
    def build(w: Any, r: str, a: Engine) -> Request:
        pair = _promotion_pair(w, committed=committed)
        path = f"/api/v1/academic-years/{pair.from_year}/promotions:{action}"
        body = None if action == "undo" else PR.body(pair)
        return path, body, {}

    return build


def _promotion_list(w: Any, r: str, a: Engine) -> Request:
    pair = _promotion_pair(w, committed=False)
    return f"/api/v1/academic-years/{pair.from_year}/promotions", None, {}


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


def _doc_patch(w: Any, r: str, a: Engine) -> Request:
    doc = _shared_doc(w, a, "patch")
    return (
        f"/api/v1/documents/{doc}",
        {"title": "Matrix circular"},
        _if_match(D.document_version(a, doc)),
    )


def _doc_archive(action: str) -> Builder:
    def build(w: Any, r: str, a: Engine) -> Request:
        doc = D.make_document(
            a,
            w.a.tenant_id,
            w.a.people["owner"].user_id,
            acl=[(e["principal_type"], e["principal_ref"]) for e in _doc_acl(w)],
        )
        if action == "unarchive":
            with W.tenant_session(w.a.tenant_id) as db:
                db.execute(
                    text("UPDATE kb.documents SET status = 'archived' WHERE id = :d"), {"d": doc}
                )
        return f"/api/v1/documents/{doc}/{action}", None, _if_match(D.document_version(a, doc))

    return build


def _doc_delete(w: Any, r: str, a: Engine) -> Request:
    doc = D.make_document(a, w.a.tenant_id, w.a.people["owner"].user_id)
    return f"/api/v1/documents/{doc}", None, {}


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _sheet_doc(w: Any, admin: Engine) -> uuid.UUID:
    """A ready single-sheet XLSX document (C2) of school A that every reader role sees
    (FR-DOC-009..011)."""
    rows = [["Receipt", "Name", "Amount"], ["R-001", "Synthetica Matrix", 1200]]
    doc: uuid.UUID = D.make_document(
        admin,
        w.a.tenant_id,
        w.a.people["owner"].user_id,
        acl=[(e["principal_type"], e["principal_ref"]) for e in _doc_acl(w)],
        data=IM.xlsx_bytes(rows),
        mime_type=_XLSX_MIME,
        ext="xlsx",
    )
    return doc


def _shared_sheet_doc(w: Any, admin: Engine) -> uuid.UUID:
    if "matrix_sheet_doc" not in w.a.ids:
        w.a.ids["matrix_sheet_doc"] = _sheet_doc(w, admin)
    value: uuid.UUID = w.a.ids["matrix_sheet_doc"]
    return value


def _doc_sheet_save(w: Any, r: str, a: Engine) -> Request:
    doc = _sheet_doc(w, a)  # a fresh one per call: each save adds a version
    body = {"base_version_no": 1, "edits": [{"row_no": 2, "column": 1, "value": "Matrix edit"}]}
    return f"/api/v1/documents/{doc}/sheet/versions", body, _if_match(D.document_version(a, doc))


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


def _import_sheet_edit(w: Any, r: str, a: Engine) -> Request:
    """FR-IMP-008: edit a cell of a fresh checked batch (the father's name, column C)."""
    batch_id = _fresh_import(w, a)
    return (
        f"/api/v1/imports/{batch_id}/sheet/rows/2",
        {"cells": [{"column": 2, "value": "Synthetic Matrix Father"}]},
        _if_match(IM.batch(a, batch_id)["version"]),
    )


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
    """The role's own export in school A (someone else's export is covered by the ADR-0021
    tests at the end); a random id for roles without export permissions (403 first)."""
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


# --- admin console (US-1201, FR-ADM-001/002): tenant.export_all (owner, step-up), settings ------


def _ad() -> ModuleType:
    """tests/admin/support.py (full exports through the real services; in-memory store)."""
    name = "sos_test_admin_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "admin" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _tenant_export_id(w: Any, role: str, a: Engine) -> uuid.UUID:
    """A ready full export of school A (made by its owner); a random id for roles without
    tenant.export_all (403 at the guard first)."""
    if "tenant.export_all" not in system_roles()[role].permission_keys:
        return uuid.uuid4()
    if "matrix_tenant_export" not in w.a.ids:
        w.a.ids["matrix_tenant_export"] = _ad().ready_export(a, w.a)
    value: uuid.UUID = w.a.ids["matrix_tenant_export"]
    return value


def _tenant_export_request(w: Any, r: str, a: Engine) -> Request:
    _ad().settle(a, w.a)  # one full export at a time per school
    return "/api/v1/admin/tenant-export", {}, {}


def _retention_put(w: Any, r: str, a: Engine) -> Request:
    with a.connect() as c:
        version = c.execute(
            text("SELECT version FROM ops.retention_settings WHERE tenant_id = :t"),
            {"t": w.a.tenant_id},
        ).scalar_one_or_none()
    return "/api/v1/admin/retention", {"rules": {}}, {"If-Match": f'W/"{version or 0}"'}


# --- certificates and registers (US-1101..US-1106): issue, approve (step-up), read, registers --


def _cert() -> ModuleType:
    """tests/certificates/support.py (certificates through the real services)."""
    name = "sos_test_certificates_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "certificates" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


_CERT_ISSUERS = ("principal", "office_admin", "office_staff")


def _cert_student(w: Any) -> uuid.UUID:
    """A fresh school A student in 9A with an admission number (no blockers)."""
    value: uuid.UUID = _cert().student(w.a)
    return value


def _cert_issued(w: Any, admin: Engine, *, fresh: bool = False) -> Any:
    """An issued bonafide certificate of school A (shared unless ``fresh``), with its PDF stored
    and scanned so downloads succeed."""
    if fresh or "matrix_certificate" not in w.a.ids:
        out = _cert().issue(w.a, _cert_student(w), "bonafide")
        if fresh:
            return out
        _cert().render(w.a, out)
        _cert().mark_document_ready(admin, _cert().row(admin, out.id)["document_id"])
        w.a.ids["matrix_certificate"] = out
    return w.a.ids["matrix_certificate"]


def _cert_pending(w: Any, role: str | None = None) -> Any:
    """A fresh pending TC prepared by ``role`` when it may issue (withdraw), otherwise by the
    office admin (so approvers are never the requester)."""
    maker = role if role in _CERT_ISSUERS else "office_admin"
    return _cert().issue(w.a, _cert_student(w), "transfer", role=maker)


def _cert_decide(action: str) -> Builder:
    def build(w: Any, r: str, a: Engine) -> Request:
        tc = _cert_pending(w, r if action == "withdraw" else None)
        body = (
            None
            if action in ("approve", "withdraw")
            else {"reason": "Synthetic matrix reason for this decision"}
        )
        return (
            f"/api/v1/certificates/{tc.id}/{action}",
            body,
            {"If-Match": f'W/"{tc.version}"'},
        )

    return build


def _cert_cancel(w: Any, r: str, a: Engine) -> Request:
    cert = _cert_issued(w, a, fresh=True)
    return (
        f"/api/v1/certificates/{cert.id}/cancel",
        {"reason": "Synthetic matrix cancellation reason"},
        {"If-Match": f'W/"{cert.version}"'},
    )


def _cert_path(suffix: str = "") -> Builder:
    def build(w: Any, r: str, a: Engine) -> Request:
        return f"/api/v1/certificates/{_cert_issued(w, a).id}{suffix}", None, {}

    return build


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
    ("GET", "/api/v1/staff"): lambda w, r, a: ("/api/v1/staff", None, {}),
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
    # Archive / unarchive (US-202, FR-TEN-010): a fresh row per call.
    ("POST", "/api/v1/academic-years/{year_id}/archive"): _archive("academic-years", "archive"),
    ("POST", "/api/v1/academic-years/{year_id}/unarchive"): _archive("academic-years", "unarchive"),
    ("POST", "/api/v1/classes/{class_id}/archive"): _archive("classes", "archive"),
    ("POST", "/api/v1/classes/{class_id}/unarchive"): _archive("classes", "unarchive"),
    ("POST", "/api/v1/sections/{section_id}/archive"): _archive("sections", "archive"),
    ("POST", "/api/v1/sections/{section_id}/unarchive"): _archive("sections", "unarchive"),
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
    ("PATCH", "/api/v1/documents/{document_id}"): _doc_patch,
    ("POST", "/api/v1/documents/{document_id}/archive"): _doc_archive("archive"),
    ("POST", "/api/v1/documents/{document_id}/unarchive"): _doc_archive("unarchive"),
    # Document sheets (FR-DOC-009..011): read with document.read, save with document.upload.
    ("GET", "/api/v1/documents/{document_id}/sheet"): lambda w, r, a: (
        f"/api/v1/documents/{_shared_sheet_doc(w, a)}/sheet",
        None,
        {},
    ),
    ("POST", "/api/v1/documents/{document_id}/sheet/versions"): _doc_sheet_save,
    ("POST", "/api/v1/documents/{document_id}/sheet/export"): lambda w, r, a: (
        f"/api/v1/documents/{_shared_sheet_doc(w, a)}/sheet/export",
        {"format": "csv"},
        {},
    ),
    ("GET", "/api/v1/audit/verify"): lambda w, r, a: ("/api/v1/audit/verify", None, {}),
    # Invitation email (US-102): the target member is active and email is off in tests, so a
    # permitted caller reaches the service and gets 409 (see _success); others get 403.
    ("POST", "/api/v1/users/{user_id}/invitation-email"): lambda w, r, a: (
        f"/api/v1/users/{w.a.people['target'].user_id}/invitation-email",
        None,
        {},
    ),
    # CSV export (FR-AUD-005): a narrow filter keeps each matrix call small.
    ("GET", "/api/v1/audit/export"): lambda w, r, a: (
        "/api/v1/audit/export?action=section.created",
        None,
        {},
    ),
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
    ("GET", "/api/v1/students/{student_id}/enrollments"): lambda w, r, a: (
        _st(w, r, "/enrollments"),
        None,
        {},
    ),
    ("PATCH", "/api/v1/students/{student_id}/enrollments/{enrollment_id}"): _enrolment_patch,
    ("POST", "/api/v1/students/{student_id}/enrollments/{enrollment_id}/end"): _enrolment_end,
    ("DELETE", "/api/v1/students/{student_id}/guardians/{guardian_id}"): _guardian_delete,
    # Promotions (FR-TEN-011, US-202 AC2): tenant.structure.manage, school-wide.
    ("POST", "/api/v1/academic-years/{year_id}/promotions:preview"): _promotion("preview"),
    ("POST", "/api/v1/academic-years/{year_id}/promotions:commit"): _promotion("commit"),
    ("POST", "/api/v1/academic-years/{year_id}/promotions:undo"): _promotion(
        "undo", committed=True
    ),
    ("GET", "/api/v1/academic-years/{year_id}/promotions"): _promotion_list,
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
    # Staged sheet (FR-IMP-008, FR-IMP-009): import.run; the download also needs step-up.
    ("GET", "/api/v1/imports/{import_id}/sheet"): lambda w, r, a: (
        f"/api/v1/imports/{_shared_import(w, a)}/sheet",
        None,
        {},
    ),
    ("PATCH", "/api/v1/imports/{import_id}/sheet/rows/{row_no}"): _import_sheet_edit,
    ("GET", "/api/v1/imports/{import_id}/sheet/export"): lambda w, r, a: (
        f"/api/v1/imports/{_shared_import(w, a)}/sheet/export",
        None,
        {},
    ),
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
    # SchoolOS support only (ADR-0023): every school role is refused (support principals are
    # tested in tests/breakglass/test_support_signin.py).
    ("POST", "/api/v1/breakglass/support-session"): lambda w, r, a: (
        "/api/v1/breakglass/support-session",
        {"platform_request_id": str(uuid.uuid4())},
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
    # Admin console (app/admin/api.py; docs/09 Exports, audit, admin; US-1201).
    ("POST", "/api/v1/admin/tenant-export"): _tenant_export_request,
    ("GET", "/api/v1/admin/tenant-export"): lambda w, r, a: (
        "/api/v1/admin/tenant-export",
        None,
        {},
    ),
    ("GET", "/api/v1/admin/tenant-export/{tenant_export_id}"): lambda w, r, a: (
        f"/api/v1/admin/tenant-export/{_tenant_export_id(w, r, a)}",
        None,
        {},
    ),
    ("GET", "/api/v1/admin/tenant-export/{tenant_export_id}/download-url"): lambda w, r, a: (
        f"/api/v1/admin/tenant-export/{_tenant_export_id(w, r, a)}/download-url",
        None,
        {},
    ),
    ("GET", "/api/v1/admin/retention"): lambda w, r, a: ("/api/v1/admin/retention", None, {}),
    ("PUT", "/api/v1/admin/retention"): _retention_put,
    # Certificates and registers (app/certificates/api.py; docs/09 Certificates; US-1101..1106).
    ("GET", "/api/v1/certificates/types"): lambda w, r, a: (
        "/api/v1/certificates/types",
        None,
        {},
    ),
    ("GET", "/api/v1/students/{student_id}/certificates/preview"): lambda w, r, a: (
        f"/api/v1/students/{_cert_student(w)}/certificates/preview?certificate_type=bonafide",
        None,
        {},
    ),
    ("POST", "/api/v1/students/{student_id}/certificates"): lambda w, r, a: (
        f"/api/v1/students/{_cert_student(w)}/certificates",
        {"certificate_type": "bonafide", "inputs": {"purpose": "bus_pass"}},
        {},
    ),
    ("GET", "/api/v1/certificates"): lambda w, r, a: ("/api/v1/certificates", None, {}),
    ("GET", "/api/v1/certificates/{certificate_id}"): _cert_path(),
    ("GET", "/api/v1/certificates/{certificate_id}/print"): _cert_path("/print"),
    ("GET", "/api/v1/certificates/{certificate_id}/download-url"): _cert_path("/download-url"),
    ("POST", "/api/v1/certificates/{certificate_id}/render"): _cert_path("/render"),
    ("POST", "/api/v1/certificates/{certificate_id}/duplicates"): lambda w, r, a: (
        f"/api/v1/certificates/{_cert_issued(w, a).id}/duplicates",
        {"reason": "Synthetic matrix duplicate reason"},
        {},
    ),
    ("POST", "/api/v1/certificates/{certificate_id}/approve"): _cert_decide("approve"),
    ("POST", "/api/v1/certificates/{certificate_id}/reject"): _cert_decide("reject"),
    ("POST", "/api/v1/certificates/{certificate_id}/withdraw"): _cert_decide("withdraw"),
    ("POST", "/api/v1/certificates/{certificate_id}/cancel"): _cert_cancel,
    ("GET", "/api/v1/registers/transfer-certificates"): lambda w, r, a: (
        "/api/v1/registers/transfer-certificates",
        None,
        {},
    ),
    ("GET", "/api/v1/registers/certificates"): lambda w, r, a: (
        "/api/v1/registers/certificates",
        None,
        {},
    ),
    ("GET", "/api/v1/registers/admission-withdrawal"): lambda w, r, a: (
        "/api/v1/registers/admission-withdrawal",
        None,
        {},
    ),
    # Knowledge ("Ask the school"; docs/09 Knowledge, FR-KB-*, FR-KB-030).
    ("POST", "/api/v1/knowledge/ask"): _kb_ask,
    ("POST", "/api/v1/knowledge/search"): lambda w, r, a: (
        "/api/v1/knowledge/search",
        {"query": "parent-teacher meeting"},
        {},
    ),
    ("POST", "/api/v1/knowledge/queries/{query_id}/feedback"): _kb_feedback,
    ("GET", "/api/v1/knowledge/verified-answers"): lambda w, r, a: (
        "/api/v1/knowledge/verified-answers",
        None,
        {},
    ),
    ("POST", "/api/v1/knowledge/verified-answers"): _kb_verified,
    ("POST", "/api/v1/knowledge/verified-answers/{answer_id}/review"): _kb_manage("review"),
    ("POST", "/api/v1/knowledge/verified-answers/{answer_id}/retire"): _kb_manage("retire"),
    # Circulars, tasks and parent notices (M4; FR-CIR-*, FR-TASK-*, FR-NOTICE-*).
    ("GET", "/api/v1/circulars"): lambda w, r, a: ("/api/v1/circulars", None, {}),
    ("GET", "/api/v1/circulars/{document_id}"): lambda w, r, a: (
        f"/api/v1/circulars/{KB.shared_document(a, w.a)}",
        None,
        {},
    ),
    ("POST", "/api/v1/circulars/{document_id}/read"): _circular_read,
    ("POST", "/api/v1/circulars/{document_id}/review"): _circular_review,
    ("POST", "/api/v1/circular-suggestions/{suggestion_id}/confirm"): _suggestion("confirm"),
    ("POST", "/api/v1/circular-suggestions/{suggestion_id}/dismiss"): _suggestion("dismiss"),
    ("GET", "/api/v1/tasks"): lambda w, r, a: ("/api/v1/tasks", None, {}),
    ("POST", "/api/v1/tasks"): lambda w, r, a: (
        "/api/v1/tasks",
        {
            "title": "Send the synthetic report",
            "owner_membership_id": str(w.person(r).membership_id),
            "due_on": "2026-12-01",
        },
        {},
    ),
    ("GET", "/api/v1/task-assignees"): lambda w, r, a: ("/api/v1/task-assignees", None, {}),
    ("GET", "/api/v1/tasks/{task_id}"): lambda w, r, a: (
        f"/api/v1/tasks/{_own_task(w, r)}",
        None,
        {},
    ),
    ("PATCH", "/api/v1/tasks/{task_id}"): lambda w, r, a: (
        f"/api/v1/tasks/{_own_task(w, r)}",
        {"title": "Send the synthetic report today"},
        _if_match(1),
    ),
    ("POST", "/api/v1/tasks/{task_id}/status"): lambda w, r, a: (
        f"/api/v1/tasks/{_own_task(w, r)}/status",
        {"status": "in_progress"},
        _if_match(1),
    ),
    ("GET", "/api/v1/notices"): lambda w, r, a: ("/api/v1/notices", None, {}),
    ("POST", "/api/v1/notices"): lambda w, r, a: ("/api/v1/notices", {"source": "blank"}, {}),
    ("GET", "/api/v1/notices/{notice_id}"): lambda w, r, a: (
        f"/api/v1/notices/{CI.complete_notice(w.a)[0]}",
        None,
        {},
    ),
    ("PATCH", "/api/v1/notices/{notice_id}"): _notice("draft"),
    ("POST", "/api/v1/notices/{notice_id}/approve"): _notice("approve"),
    ("POST", "/api/v1/notices/{notice_id}/render"): _notice("render"),
    ("GET", "/api/v1/notices/{notice_id}/download-url"): _notice("download"),
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
        if str(rc.path).startswith("/api/v1/edge/"):
            continue  # Tally edge agent, device-signed (ADR-0032): tests/tally/test_api.py
        for method in rc.methods or ():
            table[(method, str(rc.path))] = guard[0]
    return table


ROUTES = _route_table()
ROLES = tuple(system_roles())
# Routes guarded by session.authenticated whose service accepts ONLY a SchoolOS support
# principal (ADR-0023): 403 ``breakglass_only`` for every school role.
SUPPORT_ONLY = frozenset({("POST", "/api/v1/breakglass/support-session")})
STEP_UP_ROUTES = sorted(k for k, g in ROUTES.items() if g.sos_step_up)


# --- Tally connector (M6; ADR-0032 Proposed): school A flag on around each call -------------------


def _tally() -> ModuleType:
    """tests/tally/support.py (connector rows written as the test superuser, the flag)."""
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


def _tally_party(w: Any, a: Engine) -> uuid.UUID:
    if "matrix_tally_party" not in w.a.ids:
        w.a.ids["matrix_tally_party"] = _tally().seed_party(
            a, w.a.tenant_id, w.person("owner").user_id, ledger="Synthetic Matrix Ledger"
        )
    value: uuid.UUID = w.a.ids["matrix_tally_party"]
    return value


def _tally_code(w: Any, r: str, a: Engine) -> Request:
    """A new code needs a free device slot: earlier matrix rows may have left agents active."""
    with a.begin() as c:
        c.execute(
            text(
                "UPDATE ops.tally_devices SET status = 'revoked', key_id = NULL, "
                "key_ciphertext = NULL, next_key_id = NULL, next_key_ciphertext = NULL, "
                "rotation_started_at = NULL, revoked_at = now() "
                "WHERE tenant_id = :t AND status = 'active'"
            ),
            {"t": w.a.tenant_id},
        )
    return "/api/v1/tally/enrolment-codes", {"device_name": "Matrix PC"}, {}


def _tally_revoke(w: Any, r: str, a: Engine) -> Request:
    device = _tally().seed_device(a, w.a.tenant_id, w.person("owner").user_id)
    return f"/api/v1/tally/devices/{device}/revoke", None, {"If-Match": 'W/"1"'}


def _tally_link(w: Any, r: str, a: Engine) -> Request:
    student = SW.ensure_students(w)["s9a"]
    return (
        f"/api/v1/tally/parties/{_tally_party(w, a)}/links",
        {"student_id": str(student)},
        {},
    )


def _tally_unlink(w: Any, r: str, a: Engine) -> Request:
    party, student = _tally_party(w, a), SW.ensure_students(w)["s9a"]
    _tally().seed_link(a, w.a.tenant_id, party, student, w.person("owner").user_id)
    return f"/api/v1/tally/parties/{party}/links/{student}", None, {}


SPECS.update(
    {
        ("GET", "/api/v1/tally/status"): lambda w, r, a: ("/api/v1/tally/status", None, {}),
        ("GET", "/api/v1/tally/devices"): lambda w, r, a: ("/api/v1/tally/devices", None, {}),
        ("POST", "/api/v1/tally/enrolment-codes"): _tally_code,
        ("POST", "/api/v1/tally/devices/{device_id}/revoke"): _tally_revoke,
        ("GET", "/api/v1/tally/groups"): lambda w, r, a: ("/api/v1/tally/groups", None, {}),
        ("PUT", "/api/v1/tally/groups/selection"): lambda w, r, a: (
            "/api/v1/tally/groups/selection",
            {"company": "Synthetic Matrix Company", "group_ids": []},
            {},
        ),
        ("GET", "/api/v1/tally/parties"): lambda w, r, a: ("/api/v1/tally/parties", None, {}),
        ("POST", "/api/v1/tally/parties/search"): lambda w, r, a: (
            "/api/v1/tally/parties/search",
            {"query": "Synthetic"},
            {},
        ),
        ("GET", "/api/v1/tally/parties/{party_id}"): lambda w, r, a: (
            f"/api/v1/tally/parties/{_tally_party(w, a)}",
            None,
            {},
        ),
        ("POST", "/api/v1/tally/parties/{party_id}/links"): _tally_link,
        ("DELETE", "/api/v1/tally/parties/{party_id}/links/{student_id}"): _tally_unlink,
        ("GET", "/api/v1/tally/dues"): lambda w, r, a: ("/api/v1/tally/dues", None, {}),
    }
)


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
        "/api/v1/academic-years/{year_id}/promotions:commit",
        "/api/v1/knowledge/verified-answers",
        "/api/v1/students/{student_id}/certificates",
        "/api/v1/certificates/{certificate_id}/duplicates",
        "/api/v1/tasks",
        "/api/v1/notices",
        "/api/v1/circular-suggestions/{suggestion_id}/confirm",
        "/api/v1/tally/enrolment-codes",
        "/api/v1/tally/parties/{party_id}/links",
    }
    accepted = {
        "/api/v1/documents",
        "/api/v1/documents/{document_id}/versions",
        "/api/v1/documents/{document_id}/sheet/versions",
        "/api/v1/imports",
        "/api/v1/imports/{import_id}/validate",
        "/api/v1/imports/{import_id}/commit",
        "/api/v1/dq/runs",
        "/api/v1/extraction-batches",
        "/api/v1/exports",
        "/api/v1/exports/student-list",
        "/api/v1/admin/tenant-export",
        "/api/v1/circulars/{document_id}/read",
        "/api/v1/notices/{notice_id}/render",
    }
    if method == "POST" and path in accepted:
        return 202
    if (method, path) == ("POST", "/api/v1/users/{user_id}/invitation-email"):
        return 409  # guard passed; the service refuses (email off in tests)
    if method == "DELETE" and path in (
        "/api/v1/documents/{document_id}",
        "/api/v1/students/{student_id}/guardians/{guardian_id}",
        "/api/v1/tally/parties/{party_id}/links/{student_id}",
    ):
        return 204
    return 201 if method == "POST" and path in creates else 200


def _call(api: Any, w: Any, admin: Engine, role: str, key: tuple[str, str], **kw: Any) -> Any:
    path, body, headers = SPECS[key](w, role, admin)
    if key[1].startswith("/api/v1/tally/"):
        # The Tally connector is behind a per-school flag (default off, ADR-0032): on for the call.
        with _tally().flag_on(admin, w.a.tenant_id):
            return api.call(w.person(role), key[0], path, json=body, headers=headers, **kw)
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
    expected = _success(*key) if granted and key not in SUPPORT_ONLY else 403
    assert res.status_code == expected, f"{role} {key}: {res.status_code} {res.text}"
    if key in SUPPORT_ONLY:
        assert res.json()["code"] == "breakglass_only"


@pytest.mark.parametrize("key", STEP_UP_ROUTES, ids=lambda k: f"{k[0]} {k[1]}")
def test_SEC_005_step_up_routes_need_recent_mfa(
    world: Any, api: Any, admin_engine: Engine, key: tuple[str, str]
) -> None:
    guard = ROUTES[key]
    # require_any(..., step_up=True) guards (POST /exports, ADR-0021): every alternative's holders.
    permissions = (guard.sos_permission, *getattr(guard, "sos_any_of", ()))
    holders = [r for r in ROLES if set(permissions) & system_roles()[r].permission_keys]
    assert holders
    for role in holders:
        res = _call(api, world, admin_engine, role, key, auth_age_s=301)
        assert res.status_code == 428, (role, res.text)
        assert res.json()["code"] == "step_up_required"


# Downloads of personal data whose permission is not a step-up permission itself, but which need
# a recent MFA sign-in anyway (FR-EXP-004): the staged import sheet always; a document sheet
# when the document is personal (C2) or restricted (C3), as the matrix's shared sheet is.
PERSONAL_DOWNLOADS = (
    ("GET", "/api/v1/imports/{import_id}/sheet/export"),
    ("POST", "/api/v1/documents/{document_id}/sheet/export"),
)


@pytest.mark.parametrize("key", PERSONAL_DOWNLOADS, ids=lambda k: f"{k[0]} {k[1]}")
def test_FR_EXP_004_sheet_downloads_need_recent_mfa(
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


# --- ADR-0021: who sees and downloads whose exports (export.read_all, export.download_any) -------

_READ_ALL, _DOWNLOAD_ANY = "export.read_all", "export.download_any"
_READERS = (*_EXPORT_PERMISSIONS, _READ_ALL)
_DOWNLOADERS = (*_EXPORT_PERMISSIONS, _DOWNLOAD_ANY)


def _coordinator_export(w: Any) -> uuid.UUID:
    """A ready pre-check of school A requested by its exam coordinator (one per session)."""
    if "matrix_coordinator_export" not in w.a.ids:
        SW.ensure_students(w)
        w.a.ids["matrix_coordinator_export"] = _ex().ready_export(w.a, "exam_coordinator")
    value: uuid.UUID = w.a.ids["matrix_coordinator_export"]
    return value


def _holds(role: str, permissions: tuple[str, ...]) -> bool:
    return bool(set(permissions) & system_roles()[role].permission_keys)


@pytest.mark.parametrize("role", ROLES)
def test_ADR_0021_list_all_exports_matrix(world: Any, api: Any, role: str) -> None:
    export_id = str(_coordinator_export(world))
    params = {"requested_by": "all", "limit": 200}
    res = api.call(world.person(role), "GET", "/api/v1/exports", params=params)
    if _holds(role, (_READ_ALL,)):
        assert res.status_code == 200, (role, res.text)
        assert export_id in {e["id"] for e in res.json()["data"]}
    else:
        assert res.status_code == 403, (role, res.text)


@pytest.mark.parametrize("role", ROLES)
def test_ADR_0021_someone_elses_export_matrix(world: Any, api: Any, role: str) -> None:
    """Detail: own or export.read_all -> 200; other export readers -> 404 (existence hidden);
    everyone else -> 403 at the guard. Download: own or export.download_any -> 200 (step-up);
    export.read_all only -> 403 not_own_export; other downloaders -> 404; others -> 403."""
    export_id = _coordinator_export(world)
    who = world.person(role)
    own = role == "exam_coordinator"
    detail = api.call(who, "GET", f"/api/v1/exports/{export_id}")
    sees = own or _holds(role, (_READ_ALL,))
    expected = 200 if sees else 404 if _holds(role, _READERS) else 403
    assert detail.status_code == expected, (role, detail.text)
    link = api.call(who, "GET", f"/api/v1/exports/{export_id}/download-url")
    if own or _holds(role, (_DOWNLOAD_ANY,)):
        assert link.status_code == 200, (role, link.text)
    elif _holds(role, _DOWNLOADERS):
        expected = 403 if _holds(role, (_READ_ALL,)) else 404
        assert link.status_code == expected, (role, link.text)
        if expected == 403:
            assert link.json()["code"] == "not_own_export"
    else:
        assert link.status_code == 403, (role, link.text)
        assert link.json()["code"] == "forbidden"


def test_ADR_0021_download_any_needs_step_up_and_stays_in_school(world: Any, api: Any) -> None:
    export_id = _coordinator_export(world)
    holders = [r for r in ROLES if _holds(r, (_DOWNLOAD_ANY,))]
    assert holders == ["owner"], "ADR-0021: only the owner by default"
    path = f"/api/v1/exports/{export_id}/download-url"
    stale = api.call(world.person("owner"), "GET", path, auth_age_s=301)
    assert stale.status_code == 428
    assert stale.json()["code"] == "step_up_required"
    # School B's owner holds the same permissions in B: school A's export is not found.
    b_owner = world.b.people["owner"]
    for p in (path, f"/api/v1/exports/{export_id}"):
        assert api.call(b_owner, "GET", p).status_code == 404
