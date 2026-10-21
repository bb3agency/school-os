"""Security-suite helpers for consents and breaches (R3-1B; loaded by path from
tests/security/test_authz_matrix.py and test_bola.py). Synthetic data only; objects are written
with the admin engine so builders need no API client."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

TESTS = Path(__file__).resolve().parents[1]

Request = tuple[str, dict[str, Any] | None, dict[str, str]]
Builder = Callable[[Any, str, Engine], Request]


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

SHARED_KEY = "matrix_photos"


def purpose(
    admin: Engine, school: Any, *, key: str | None = None, active: bool = True
) -> uuid.UUID:
    """A purpose of ``school`` (version 1). ``key=None``: a fresh one; the shared key is created
    once and switched on again on every call."""
    pid = uuid.uuid4()
    with admin.begin() as c:
        if key is not None:
            found = c.execute(
                text("SELECT id FROM sis.consent_purposes WHERE tenant_id = :t AND key = :k"),
                {"t": school.tenant_id, "k": key},
            ).scalar_one_or_none()
            if found is not None:
                c.execute(
                    text("UPDATE sis.consent_purposes SET active = :a WHERE id = :i"),
                    {"a": active, "i": found},
                )
                value: uuid.UUID = found
                return value
        c.execute(
            text(
                "INSERT INTO sis.consent_purposes (id, tenant_id, key, label_en, description, "
                "active, created_by) VALUES (:i, :t, :k, 'Photos on the school website', "
                "'What: photos at events. Why: school news. Who: the public. How long: a year.', "
                ":a, :u)"
            ),
            {
                "i": pid,
                "t": school.tenant_id,
                "k": key or f"m_{uuid.uuid4().hex[:12]}",
                "a": active,
                "u": school.people["owner"].user_id,
            },
        )
    return pid


def breach(admin: Engine, school: Any) -> uuid.UUID:
    """An open school breach of ``school`` (no texts), version 1."""
    bid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.breach_records (id, tenant_id, origin, noticed_at, categories, "
                "approx_children, recorded_by) VALUES (:i, :t, 'school', now() - interval "
                "'2 hours', ARRAY['identity'], 3, :u)"
            ),
            {"i": bid, "t": school.tenant_id, "u": school.people["owner"].user_id},
        )
    return bid


def _student(w: Any, role: str) -> uuid.UUID:
    ids = SW.ensure_students(w)
    value: uuid.UUID = ids["s10a"] if role == "teacher" else ids["s9a"]
    return value


def _deactivate_others(admin: Engine, school: Any) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "UPDATE sis.consent_purposes SET active = false WHERE tenant_id = :t AND key <> :k"
            ),
            {"t": school.tenant_id, "k": SHARED_KEY},
        )


def matrix_specs() -> dict[tuple[str, str], Builder]:
    """One builder per consent and breach route (school A)."""

    def shared(w: Any, a: Engine) -> uuid.UUID:
        return purpose(a, w.a, key=SHARED_KEY)

    def create_purpose(w: Any, r: str, a: Engine) -> Request:
        _deactivate_others(a, w.a)
        body = {
            "key": f"m_{uuid.uuid4().hex[:12]}",
            "label_en": "Name in the magazine",
            "description": "Your child's name in the school magazine for one year.",
        }
        return "/api/v1/consent-purposes", body, {}

    def patch_purpose(w: Any, r: str, a: Engine) -> Request:
        pid = purpose(a, w.a, active=False)
        return f"/api/v1/consent-purposes/{pid}", {"active": False}, {"If-Match": 'W/"1"'}

    def record(w: Any, r: str, a: Engine) -> Request:
        body = {
            "purpose_id": str(shared(w, a)),
            "status": "refused",
            "relationship": "father",
            "decided_on": "2026-07-15",
        }
        return f"/api/v1/students/{_student(w, r)}/consents", body, {}

    def forms(w: Any, r: str, a: Engine) -> Request:
        shared(w, a)
        return f"/api/v1/consents/forms?student_id={_student(w, r)}", None, {}

    def forms_pdf(w: Any, r: str, a: Engine) -> Request:
        shared(w, a)
        return "/api/v1/exports/consent-forms", {"student_id": str(_student(w, r))}, {}

    def student(suffix: str) -> Builder:
        return lambda w, r, a: (f"/api/v1/students/{_student(w, r)}{suffix}", None, {})

    def on_breach(
        suffix: str, body: dict[str, Any] | None = None, *, if_match: bool = False
    ) -> Builder:
        def build(w: Any, r: str, a: Engine) -> Request:
            headers = {"If-Match": 'W/"1"'} if if_match else {}
            return f"/api/v1/privacy/breaches/{breach(a, w.a)}{suffix}", body, headers

        return build

    breach_body = {
        "noticed_at": "2026-10-20T08:00:00+05:30",
        "description": "A class list was sent to the wrong parents' group.",
        "categories": ["contact"],
        "approx_guardians": 40,
    }
    return {
        ("GET", "/api/v1/consent-purposes"): lambda w, r, a: ("/api/v1/consent-purposes", None, {}),
        ("POST", "/api/v1/consent-purposes"): create_purpose,
        ("PATCH", "/api/v1/consent-purposes/{purpose_id}"): patch_purpose,
        ("GET", "/api/v1/students/{student_id}/consents"): student("/consents"),
        ("POST", "/api/v1/students/{student_id}/consents"): record,
        ("GET", "/api/v1/students/{student_id}/consent-view"): student("/consent-view"),
        ("GET", "/api/v1/consents/summary"): lambda w, r, a: ("/api/v1/consents/summary", None, {}),
        ("GET", "/api/v1/consents/forms"): forms,
        ("POST", "/api/v1/exports/consent-forms"): forms_pdf,
        ("GET", "/api/v1/privacy/breaches"): lambda w, r, a: ("/api/v1/privacy/breaches", None, {}),
        ("POST", "/api/v1/privacy/breaches"): lambda w, r, a: (
            "/api/v1/privacy/breaches",
            breach_body,
            {},
        ),
        ("GET", "/api/v1/privacy/breaches/{breach_id}"): on_breach(""),
        ("PATCH", "/api/v1/privacy/breaches/{breach_id}"): on_breach(
            "", {"approx_staff": 2}, if_match=True
        ),
        ("GET", "/api/v1/privacy/breaches/{breach_id}/affected"): on_breach("/affected"),
        ("PUT", "/api/v1/privacy/breaches/{breach_id}/affected"): on_breach(
            "/affected", {"student_ids": []}
        ),
        ("GET", "/api/v1/privacy/breaches/{breach_id}/drafts/{draft}"): on_breach(
            "/drafts/board_outline"
        ),
        ("POST", "/api/v1/privacy/breaches/{breach_id}/notice-draft"): on_breach("/notice-draft"),
    }


MATRIX_CREATES = frozenset(
    {
        "/api/v1/consent-purposes",
        "/api/v1/students/{student_id}/consents",
        "/api/v1/privacy/breaches",
        "/api/v1/privacy/breaches/{breach_id}/notice-draft",
    }
)
MATRIX_ACCEPTED = frozenset({"/api/v1/exports/consent-forms"})

# BOLA (tests/security/test_bola.py): school B's objects and minimal bodies.
BOLA_BODIES: dict[tuple[str, str], dict[str, Any] | None] = {
    ("POST", "/api/v1/students/{student_id}/consents"): {
        "purpose_id": "0192f3a4-0000-7000-8000-000000000b01",
        "status": "refused",
        "relationship": "father",
        "decided_on": "2026-07-01",
    },
    ("PATCH", "/api/v1/consent-purposes/{purpose_id}"): {"active": False},
    ("PATCH", "/api/v1/privacy/breaches/{breach_id}"): {"approx_staff": 1},
    ("PUT", "/api/v1/privacy/breaches/{breach_id}/affected"): {"student_ids": []},
    ("POST", "/api/v1/privacy/breaches/{breach_id}/notice-draft"): None,
}


def b_purpose(admin: Engine, w: Any) -> uuid.UUID:
    if "bola_purpose" not in w.b.ids:
        w.b.ids["bola_purpose"] = purpose(admin, w.b, active=False)
    value: uuid.UUID = w.b.ids["bola_purpose"]
    return value


def b_breach(admin: Engine, w: Any) -> uuid.UUID:
    if "bola_breach" not in w.b.ids:
        w.b.ids["bola_breach"] = breach(admin, w.b)
    value: uuid.UUID = w.b.ids["bola_breach"]
    return value
