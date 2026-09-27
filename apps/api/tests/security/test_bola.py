"""Object-level authorization and cross-tenant isolation over the API (SEC-015, SEC-001,
invariant 3, docs/12 §4.3-4.4).

- Every route with an ID in its path answers 404 for another school's ID and for a random ID,
  even for the owner (never reveal existence), and changes nothing.
- Scoped roles get 404 for classes/sections outside their scopes.
- Lists never contain another school's objects; bodies cannot reference them.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import Engine

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
    ("PATCH", "/api/v1/academic-years/{year_id}"): {},
    ("PATCH", "/api/v1/classes/{class_id}"): {},
    ("PATCH", "/api/v1/sections/{section_id}"): {},
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
    ("POST", "/api/v1/documents/{document_id}/versions"): {"upload_id": str(uuid.uuid4())},
    ("PUT", "/api/v1/documents/{document_id}/acl"): {"acl": []},
    ("DELETE", "/api/v1/documents/{document_id}"): None,
    ("POST", "/api/v1/notifications/{notification_id}/read"): None,
    ("POST", "/api/v1/breakglass/requests/{request_id}/approve"): None,
    ("POST", "/api/v1/breakglass/requests/{request_id}/deny"): None,
    ("POST", "/api/v1/breakglass/grants/{grant_id}/revoke"): None,
    ("PUT", "/api/v1/imports/{import_id}/mapping"): {"columns": []},
    ("POST", "/api/v1/imports/{import_id}/validate"): None,
    ("POST", "/api/v1/imports/{import_id}/commit"): {},
    ("POST", "/api/v1/imports/{import_id}/revert"): None,
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
        # Imports (import.run / import.commit are not owner permissions, docs/07 §6.2).
        ("GET", "/api/v1/imports/{import_id}"),
        ("GET", "/api/v1/imports/{import_id}/rows"),
        ("PUT", "/api/v1/imports/{import_id}/mapping"),
        ("POST", "/api/v1/imports/{import_id}/validate"),
        ("POST", "/api/v1/imports/{import_id}/commit"),
        ("POST", "/api/v1/imports/{import_id}/revert"),
        # Cancelling needs student.identity_change.request (the owner only approves).
        ("POST", "/api/v1/change-requests/{change_request_id}/cancel"),
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


def _fill(path: str, value: uuid.UUID) -> str:
    for param in PARAM_TO_B:
        path = path.replace("{" + param + "}", str(value))
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
        res = api.call(
            world.person(ACTOR.get(key, "owner")),
            method,
            _fill(template, target),
            json=BODIES.get(key),
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


@pytest.mark.parametrize(
    "path", ["/api/v1/users", "/api/v1/academic-years", "/api/v1/classes", "/api/v1/sections"]
)
def test_SEC_001_lists_never_show_other_school(world: Any, api: Any, path: str) -> None:
    res = api.call(world.person("owner"), "GET", path, params={"limit": 200})
    assert res.status_code == 200
    b_ids = {str(v) for v in world.b.ids.values()}
    b_ids |= {str(p.user_id) for p in world.b.people.values()}
    assert not {item["id"] for item in res.json()["data"]} & b_ids


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
