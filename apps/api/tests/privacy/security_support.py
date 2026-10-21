"""Privacy routes for the security suites (authorization matrix, BOLA): request builders for
school A and a privacy request of school B, made through the real services (R3-1A; ADR-0048;
synthetic data only). Loaded by path from ``tests/security/test_authz_matrix.py`` and
``tests/security/test_bola.py``.

Maker-checker routes (release, erasure) act on requests logged by the office admin, so the
deciding roles (owner, principal) reach the service's success path; reports are made by the
worker with the fake renderer.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine

from app.core.db import tenant_session
from app.privacy import service as privacy
from app.privacy.schemas import PrivacyRequestCreate, PrivacySubjectIn

_HERE = Path(__file__).resolve()


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


EX = _load("sos_test_exports_objects", _HERE.parents[1] / "exports" / "objects.py")
SW = EX.SW

Request = tuple[str, dict[str, Any] | None, dict[str, str]]
Builder = Callable[[Any, str, Engine], Request]
BASE = "/api/v1/privacy/requests"
_B: dict[uuid.UUID, uuid.UUID] = {}


def _ctx(school: Any, role: str) -> Any:
    base = SW.ctx_for(school.tenant_id, school.people[role], role)
    return dataclasses.replace(base, auth_time=dt.datetime.now(dt.UTC))


def log_request(
    school: Any, kind: str = "access", *, verified: bool = True, role: str = "office_admin"
) -> uuid.UUID:
    """A request about the school's shared 9A student, logged by ``role``."""
    EX.install()
    student = _student(school)
    data = PrivacyRequestCreate(
        kind=kind,
        subject=PrivacySubjectIn(student_id=student),
        requester_relationship="mother",
        request_text="Synthetic matrix request",
        verification_method="in_person_document" if verified else None,
    )
    who = school.people[role]
    with tenant_session(school.tenant_id, who.user_id) as db:
        out = privacy.create_request(db, _ctx(school, role), data)
    return out.id


def _student(school: Any) -> uuid.UUID:
    key = "privacy_matrix_student"
    if key not in school.ids:
        school.ids[key] = EX.student(school, name="Synthetica Privacy Matrix")
    value: uuid.UUID = school.ids[key]
    return value


def ready_report(school: Any, *, released: bool = False) -> uuid.UUID:
    rid = log_request(school)
    admin = school.people["office_admin"]
    with tenant_session(school.tenant_id, admin.user_id) as db:
        out = privacy.build_report(db, _ctx(school, "office_admin"), rid)
    assert out.report is not None
    assert EX.run(school, out.report.export_id) == "ready"
    if released:
        principal = school.people["principal"]
        with tenant_session(school.tenant_id, principal.user_id) as db:
            privacy.release_report(db, _ctx(school, "principal"), rid)
    return rid


def notice_file(school: Any) -> uuid.UUID:
    EX.install()
    admin = school.people["office_admin"]
    with tenant_session(school.tenant_id, admin.user_id) as db:
        out = privacy.request_notice_pdf(db, _ctx(school, "office_admin"), "notice_staff")
    assert EX.run(school, out.export_id) == "ready"
    return out.export_id


def b_request(w: Any) -> uuid.UUID:
    """A privacy request of school B (one per session)."""
    if w.b.tenant_id not in _B:
        _B[w.b.tenant_id] = log_request(w.b, "erasure", role="owner")
    return _B[w.b.tenant_id]


def _g(path: Callable[[Any], str]) -> Builder:
    return lambda w, r, a: (path(w), None, {})


def matrix_specs() -> dict[tuple[str, str], Builder]:
    """One builder per privacy route (school A)."""
    today = dt.datetime.now(dt.UTC).date().isoformat()
    return {
        ("GET", "/api/v1/privacy/templates"): _g(lambda w: "/api/v1/privacy/templates"),
        ("GET", "/api/v1/privacy/templates/{key}/preview"): _g(
            lambda w: "/api/v1/privacy/templates/notice_parents/preview"
        ),
        ("POST", "/api/v1/privacy/templates/{key}/pdf"): _g(
            lambda w: "/api/v1/privacy/templates/notice_staff/pdf"
        ),
        ("GET", "/api/v1/privacy/files/{export_id}"): _g(
            lambda w: f"/api/v1/privacy/files/{notice_file(w.a)}"
        ),
        ("GET", "/api/v1/privacy/files/{export_id}/download-url"): _g(
            lambda w: f"/api/v1/privacy/files/{notice_file(w.a)}/download-url"
        ),
        ("GET", "/api/v1/privacy/notices"): _g(lambda w: "/api/v1/privacy/notices"),
        ("POST", "/api/v1/privacy/notices"): lambda w, r, a: (
            "/api/v1/privacy/notices",
            {
                "template_key": "notice_parents",
                "template_version": "0.1",
                "audience": "parents",
                "issued_on": today,
                "channel": "paper",
            },
            {},
        ),
        ("GET", "/api/v1/privacy/record-classes"): _g(lambda w: "/api/v1/privacy/record-classes"),
        ("POST", f"{BASE}/search"): lambda w, r, a: (f"{BASE}/search", {}, {}),
        ("POST", BASE): lambda w, r, a: (
            BASE,
            {
                "kind": "other",
                "subject": {"student_id": str(_student(w.a))},
                "requester_relationship": "father",
            },
            {},
        ),
        ("GET", f"{BASE}/{{request_id}}"): _g(lambda w: f"{BASE}/{log_request(w.a)}"),
        ("PATCH", f"{BASE}/{{request_id}}"): lambda w, r, a: (
            f"{BASE}/{log_request(w.a)}",
            {"notes": "Synthetic matrix note"},
            {"If-Match": 'W/"1"'},
        ),
        ("GET", f"{BASE}/{{request_id}}/letters/{{letter}}"): _g(
            lambda w: f"{BASE}/{log_request(w.a)}/letters/acknowledgement"
        ),
        ("POST", f"{BASE}/{{request_id}}/report"): _g(
            lambda w: f"{BASE}/{log_request(w.a)}/report"
        ),
        ("POST", f"{BASE}/{{request_id}}/report/release"): _g(
            lambda w: f"{BASE}/{ready_report(w.a)}/report/release"
        ),
        ("GET", f"{BASE}/{{request_id}}/report/download-url"): _g(
            lambda w: f"{BASE}/{ready_report(w.a, released=True)}/report/download-url"
        ),
        ("POST", f"{BASE}/{{request_id}}/links"): lambda w, r, a: (
            f"{BASE}/{log_request(w.a, 'correction')}/links",
            {"kind": "change_request", "target_id": str(uuid.uuid4())},
            {},
        ),
        ("GET", f"{BASE}/{{request_id}}/erasure-plan"): _g(
            lambda w: f"{BASE}/{log_request(w.a, 'erasure')}/erasure-plan"
        ),
        ("POST", f"{BASE}/{{request_id}}/erasure"): lambda w, r, a: (
            f"{BASE}/{log_request(w.a, 'erasure')}/erasure",
            {"class_keys": ["behaviour_notes"]},
            {},
        ),
        ("POST", f"{BASE}/{{request_id}}/close"): lambda w, r, a: (
            f"{BASE}/{log_request(w.a, 'other')}/close",
            {},
            {},
        ),
    }


MATRIX_CREATES = frozenset({BASE, "/api/v1/privacy/notices"})
MATRIX_ACCEPTED = frozenset(
    {"/api/v1/privacy/templates/{key}/pdf", f"{BASE}/{{request_id}}/report"}
)
# Guard passed; the service answers: no such change request to link (422).
MATRIX_STATUS: dict[tuple[str, str], int] = {("POST", f"{BASE}/{{request_id}}/links"): 422}

BOLA_BODIES: dict[tuple[str, str], dict[str, Any] | None] = {
    ("PATCH", f"{BASE}/{{request_id}}"): {"notes": "Synthetic"},
    ("POST", f"{BASE}/{{request_id}}/report"): None,
    ("POST", f"{BASE}/{{request_id}}/report/release"): None,
    ("POST", f"{BASE}/{{request_id}}/links"): {
        "kind": "change_request",
        "target_id": "00000000-0000-4000-8000-000000000001",
    },
    ("POST", f"{BASE}/{{request_id}}/erasure"): {"class_keys": ["behaviour_notes"]},
    ("POST", f"{BASE}/{{request_id}}/close"): {},
}
