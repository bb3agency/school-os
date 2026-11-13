"""Test support for the safety check log and the policy library (FC-09, FC-10). Synthetic data
only. Row builders use the admin engine (no API client needed; also used by the security
suites)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

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


S = _load("sos_test_compliance_support", _HERE.with_name("support.py"))
D = S.D

BASE = "/api/v1/compliance"
CHECKS = f"{BASE}/checks"
POLICIES = f"{BASE}/policies"
MINE = f"{BASE}/my-policies"


def cfg_next(day: dt.date, months: int) -> dt.date:
    """``day`` moved by whole months (the service's rule)."""
    from app.compliance.safety_config import add_months

    return add_months(day, months)


def check(
    admin: Engine,
    school: Any,
    *,
    kind: str = "fire_drill",
    done_on: dt.date | None = None,
    label: str | None = None,
) -> uuid.UUID:
    """A recorded check entry of ``school`` (version 1), inserted directly."""
    cid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.compliance_checks (id, tenant_id, kind, label, done_on, "
                "recorded_by) VALUES (:i, :t, :k, :l, :d, :u)"
            ),
            {
                "i": cid,
                "t": school.tenant_id,
                "k": kind,
                "l": label if kind == "other" else None,
                "d": done_on or S.today(),
                "u": school.people["owner"].user_id,
            },
        )
    return cid


def policy_document(admin: Engine, school: Any, *, status: str = "ready") -> uuid.UUID:
    """A clean PDF of purpose ``policy`` (C1)."""
    D.memory_store()
    doc_id: uuid.UUID = D.make_document(
        admin,
        school.tenant_id,
        school.people["owner"].user_id,
        purpose="policy",
        doc_type="policy",
        sensitivity="C1",
        status=status,
    )
    return doc_id


def current_version(admin: Engine, document_id: uuid.UUID) -> uuid.UUID:
    with admin.connect() as c:
        value: uuid.UUID = c.execute(
            text("SELECT current_version_id FROM kb.documents WHERE id = :d"), {"d": document_id}
        ).scalar_one()
    return value


def policy(
    admin: Engine,
    school: Any,
    *,
    roles: list[str] | None = None,
    review_due: dt.date | None = None,
    document_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """An active policy of ``school`` (version 1) with a clean PDF, inserted directly."""
    pid = uuid.uuid4()
    doc = document_id or policy_document(admin, school)
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.compliance_policies (id, tenant_id, title, document_id, "
                "ack_roles, review_due, created_by) VALUES (:i, :t, :ti, :d, :r, :rd, :u)"
            ),
            {
                "i": pid,
                "t": school.tenant_id,
                "ti": "Child protection policy",
                "d": doc,
                "r": roles if roles is not None else ["teacher"],
                "rd": review_due or S.today() + dt.timedelta(days=200),
                "u": school.people["owner"].user_id,
            },
        )
    return pid


def acks(admin: Engine, policy_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text("SELECT * FROM ops.compliance_policy_acks WHERE policy_id = :p"), {"p": policy_id}
        )
        return [dict(r._mapping) for r in rows]


# --- security suites (authz matrix, BOLA) -----------------------------------------------------

Request = tuple[str, dict[str, Any] | None, dict[str, str]]


def matrix_specs() -> dict[tuple[str, str], Any]:
    """Valid requests for the generated authz matrix (school A)."""

    def new_check(w: Any, r: str, a: Engine) -> Request:
        cid = check(a, w.a)
        return f"{CHECKS}/entries/{cid}:remove", None, {"If-Match": 'W/"1"'}

    def new_policy(suffix: str, body: dict[str, Any] | None) -> Any:
        def build(w: Any, r: str, a: Engine) -> Request:
            pid = policy(a, w.a)
            return f"{POLICIES}/{pid}{suffix}", body, {"If-Match": 'W/"1"'}

        return build

    def put(w: Any, r: str, a: Engine) -> Request:
        doc = policy_document(a, w.a)
        pid = policy(a, w.a, document_id=doc)
        body = {
            "title": "Child protection policy",
            "document_id": str(doc),
            "review_due": (S.today() + dt.timedelta(days=100)).isoformat(),
            "ack_roles": ["teacher"],
            "status": "active",
        }
        return f"{POLICIES}/{pid}", body, {"If-Match": 'W/"1"'}

    def create(w: Any, r: str, a: Engine) -> Request:
        doc = policy_document(a, w.a)
        body = {"title": "Anti-bullying policy", "document_id": str(doc), "notify": False}
        return POLICIES, body, {}

    def mine(suffix: str, ack: bool) -> Any:
        def build(w: Any, r: str, a: Engine) -> Request:
            doc = policy_document(a, w.a)
            pid = policy(a, w.a, roles=[r], document_id=doc)
            body = {"document_version_id": str(current_version(a, doc))} if ack else None
            return f"{MINE}/{pid}{suffix}", body, {}

        return build

    return {
        ("GET", CHECKS): lambda w, r, a: (CHECKS, None, {}),
        ("GET", f"{CHECKS}/entries"): lambda w, r, a: (f"{CHECKS}/entries", None, {}),
        ("POST", f"{CHECKS}/entries"): lambda w, r, a: (
            f"{CHECKS}/entries",
            {"kind": "fire_drill", "done_on": S.today().isoformat(), "participants_count": 300},
            {},
        ),
        ("POST", f"{CHECKS}/entries/{{check_id}}:remove"): new_check,
        ("GET", POLICIES): lambda w, r, a: (POLICIES, None, {}),
        ("POST", POLICIES): create,
        ("GET", f"{POLICIES}/{{policy_id}}"): new_policy("", None),
        ("PUT", f"{POLICIES}/{{policy_id}}"): put,
        ("POST", f"{POLICIES}/{{policy_id}}:review"): new_policy(":review", {}),
        ("POST", f"{POLICIES}/{{policy_id}}:request-acknowledgement"): new_policy(
            ":request-acknowledgement", None
        ),
        ("GET", MINE): lambda w, r, a: (MINE, None, {}),
        ("GET", f"{MINE}/{{policy_id}}/file"): mine("/file", False),
        ("POST", f"{MINE}/{{policy_id}}:acknowledge"): mine(":acknowledge", True),
    }


MATRIX_CREATES = frozenset({f"{CHECKS}/entries", POLICIES})

# BOLA: school B's objects on these routes (checked before the certificate prefix).
BOLA_PREFIXES = (f"{CHECKS}/", f"{POLICIES}/", f"{MINE}/")
BOLA_BODIES: dict[tuple[str, str], dict[str, Any] | None] = {
    ("POST", f"{CHECKS}/entries/{{check_id}}:remove"): None,
    ("POST", f"{POLICIES}/{{policy_id}}:request-acknowledgement"): None,
    ("PUT", f"{POLICIES}/{{policy_id}}"): {
        "title": "Changed by A",
        "document_id": str(uuid.uuid4()),
        "review_due": "2030-01-01",
        "ack_roles": [],
    },
    ("POST", f"{POLICIES}/{{policy_id}}:review"): {},
    ("POST", f"{MINE}/{{policy_id}}:acknowledge"): {"document_version_id": str(uuid.uuid4())},
}


def b_id(admin: Engine, w: Any, template: str) -> uuid.UUID:
    """School B's check entry or policy (BOLA: school A's callers get 404). The policy asks
    every system role, so the "not asked of you" rule is not what answers 404."""
    if template.startswith(f"{CHECKS}/"):
        if "bola_compliance_check" not in w.b.ids:
            w.b.ids["bola_compliance_check"] = check(admin, w.b)
        value: uuid.UUID = w.b.ids["bola_compliance_check"]
        return value
    if "bola_compliance_policy" not in w.b.ids:
        w.b.ids["bola_compliance_policy"] = policy(admin, w.b, roles=list(S.ROLES))
    found: uuid.UUID = w.b.ids["bola_compliance_policy"]
    return found
