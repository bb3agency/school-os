"""Test support for the compliance certificate register (FC-01). Synthetic data only.

A school of its own with one member per system role, documents inserted with the documents test
support, and row builders that use the admin engine (no API client needed; also used by the
security suites).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

_HERE = Path(__file__).resolve()
TESTS = _HERE.parents[1]


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


W = _load("sos_test_api_world", TESTS / "api" / "world.py")
D = _load("sos_test_documents_support", TESTS / "documents" / "support.py")

BASE = "/api/v1/compliance"
ROLES = (
    "owner",
    "principal",
    "office_admin",
    "office_staff",
    "accountant",
    "exam_coordinator",
    "class_teacher",
    "teacher",
    "auditor_readonly",
)
READERS = frozenset({"owner", "principal", "office_admin", "auditor_readonly"})
MANAGERS = frozenset({"owner", "principal", "office_admin"})


@dataclass
class ComplianceSchool:
    tenant_id: uuid.UUID
    people: dict[str, Any] = field(default_factory=dict)
    ids: dict[str, uuid.UUID] = field(default_factory=dict)


def set_boards(admin: Engine, tenant_id: uuid.UUID, boards: list[str]) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE core.tenants SET boards = :b WHERE id = :t"), {"b": boards, "t": tenant_id}
        )


def make_school(admin: Engine, boards: list[str] | None = None) -> ComplianceSchool:
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in ROLES[1:]:
        scopes: list[tuple[str, uuid.UUID | None]] = []
        if role == "class_teacher":
            scopes = [("section", s.ids["section_9a"])]
        elif role == "teacher":
            scopes = [("class", s.ids["class_x"])]
        s.people[role] = W.add_member(admin, s.tenant_id, [role], scopes=scopes)
    set_boards(admin, s.tenant_id, boards or ["BSEAP"])
    return ComplianceSchool(tenant_id=s.tenant_id, people=s.people, ids=s.ids)


def document(admin: Engine, school: Any, *, purpose: str = "other", **kw: Any) -> uuid.UUID:
    """A clean PDF school document (``other``/certificate: also publishable on Appendix IX)."""
    D.memory_store()
    doc_id: uuid.UUID = D.make_document(
        admin,
        school.tenant_id,
        school.people["owner"].user_id,
        purpose=purpose,
        doc_type="certificate" if purpose == "other" else purpose,
        sensitivity="C1",
        **kw,
    )
    return doc_id


def today() -> dt.date:
    """Today in IST (the service's calendar)."""
    return dt.datetime.now(dt.UTC).astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).date()


def certificate(
    admin: Engine,
    school: Any,
    *,
    kind: str = "fire_noc",
    valid_until: dt.date | None = None,
    label: str | None = None,
    owner: uuid.UUID | None = None,
) -> uuid.UUID:
    """A current certificate of ``school`` (version 1), inserted directly."""
    cid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.compliance_certificates (id, tenant_id, kind, label, valid_until, "
                "owner_membership_id, recorded_by) VALUES (:i, :t, :k, :l, :v, :o, :u)"
            ),
            {
                "i": cid,
                "t": school.tenant_id,
                "k": kind,
                "l": label if kind == "other" else None,
                "v": valid_until,
                "o": owner,
                "u": school.people["owner"].user_id,
            },
        )
    return cid


def row(admin: Engine, certificate_id: uuid.UUID) -> dict[str, Any]:
    with admin.connect() as c:
        found = c.execute(
            text("SELECT * FROM ops.compliance_certificates WHERE id = :i"), {"i": certificate_id}
        ).one()
    return dict(found._mapping)


# --- security suites (authz matrix, BOLA) -----------------------------------------------------

Request = tuple[str, dict[str, Any] | None, dict[str, str]]


def b_certificate(admin: Engine, w: Any) -> uuid.UUID:
    """A certificate of school B (BOLA: school A's callers get 404)."""
    if "bola_compliance_certificate" not in w.b.ids:
        w.b.ids["bola_compliance_certificate"] = certificate(admin, w.b, kind="other", label="B")
    value: uuid.UUID = w.b.ids["bola_compliance_certificate"]
    return value


def matrix_specs() -> dict[tuple[str, str], Any]:
    """Valid requests for the generated authz matrix (school A; FR-COMP-017)."""

    def on_new(suffix: str) -> Any:
        def build(w: Any, r: str, a: Engine) -> Request:
            cid = certificate(a, w.a, kind="other", label="Matrix certificate")
            return f"{BASE}/certificates/{cid}{suffix}", None, {"If-Match": 'W/"1"'}

        return build

    def put(w: Any, r: str, a: Engine) -> Request:
        path, _body, headers = on_new("")(w, r, a)
        return path, {"label": "Matrix certificate", "valid_until": None}, headers

    return {
        ("GET", f"{BASE}/certificates"): lambda w, r, a: (f"{BASE}/certificates", None, {}),
        ("GET", f"{BASE}/summary"): lambda w, r, a: (f"{BASE}/summary", None, {}),
        ("POST", f"{BASE}/certificates"): lambda w, r, a: (
            f"{BASE}/certificates",
            {"kind": "other", "label": f"Matrix {uuid.uuid4().hex[:6]}"},
            {},
        ),
        ("GET", f"{BASE}/certificates/{{certificate_id}}"): on_new(""),
        ("PUT", f"{BASE}/certificates/{{certificate_id}}"): put,
        ("POST", f"{BASE}/certificates/{{certificate_id}}:remove"): on_new(":remove"),
    }


MATRIX_CREATES = frozenset({f"{BASE}/certificates"})
