"""Export test objects created through the real services (synthetic data only).

Loaded by path (pytest runs with ``--import-mode=importlib``) from ``tests/exports`` and the
security suites (authz matrix, BOLA). Builds on ``tests/api/world.py`` (schools and staff),
``tests/students/student_world.py`` (students, keyring) and ``tests/documents/support.py``
(in-memory object store). PDFs are made by :class:`FakeRenderer` unless a test renders for real.
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

from sqlalchemy import Engine, text

from app.authz.context import UserContext
from app.core.db import tenant_session
from app.exports import pdf
from app.exports import service as exports
from app.exports.schemas import ExportOut, ExportScopeIn, PrecheckCreate, StudentListCreate
from app.students import service as students
from app.students.schemas import StudentCreate, ValueIn

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

PDF_BYTES = b"%PDF-1.4\n% synthetic fake render\n"


class FakeRenderer:
    """Implements ``app.exports.pdf.PdfRenderer``; keeps the HTML it was given."""

    def __init__(self) -> None:
        self.pages: list[str] = []

    def render(self, html: str) -> bytes:
        self.pages.append(html)
        return PDF_BYTES


FAKE = FakeRenderer()


def _word(n: int) -> str:
    """Letters only (names with digits are a DQ-006 blocker)."""
    letters = ""
    n += 26 * 26
    while n:
        n, r = divmod(n, 26)
        letters = "abcdefghijklmnopqrstuvwxyz"[r] + letters
    return letters.capitalize()


def install() -> Any:
    """Keyring, in-memory object store and the fake renderer for this process."""
    SW.configure_keyring()
    pdf.set_renderer(FAKE)
    return D.memory_store()


def ctx(school: Any, person: Any, role: str, **scopes: Any) -> UserContext:
    """The role's permissions with a fresh MFA sign-in (step-up satisfied)."""
    base: UserContext = SW.ctx_for(school.tenant_id, person, role, **scopes)
    return dataclasses.replace(base, auth_time=dt.datetime.now(dt.UTC))


def student(
    school: Any,
    *,
    section_key: str = "section_9a",
    name: str | None = None,
    dob: str = "2012-03-14",
    roll_no: str | None = None,
    admission_no: str | None = None,
    extra: list[ValueIn] | None = None,
    parents: bool = True,
) -> uuid.UUID:
    """A synthetic student with admission-register identity values (unverified)."""
    install()
    n = next(_names)
    src = "admission_register"
    values = [
        ValueIn(
            attribute_key="full_name", source=src, value=name or f"Synthetica Export {_word(n)}"
        ),
        ValueIn(attribute_key="dob", source=src, value=dob),
        ValueIn(attribute_key="gender", source=src, value="female"),
        ValueIn(
            attribute_key="admission_no",
            source=src,
            value=admission_no or f"EX/{uuid.uuid4().hex[:6].upper()}",
        ),
    ]
    if parents:
        values += [
            ValueIn(attribute_key="father_name", source=src, value="Synthetica Father"),
            ValueIn(attribute_key="mother_name", source=src, value="Synthetica Mother"),
        ]
    values += extra or []
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        out = students.create_student(
            db,
            SW.admin_ctx(school),
            StudentCreate(values=values, section_id=school.ids[section_key], roll_no=roll_no),
        )
    return out.id


def request_precheck(
    school: Any,
    person: Any,
    role: str,
    *,
    profile_key: str = "cisce-registration-2026",
    section_keys: tuple[str, ...] = ("section_9a",),
    formats: tuple[str, ...] = ("xlsx", "pdf"),
    language: str = "en",
    include_sensitive: bool = False,
    as_ctx: UserContext | None = None,
) -> ExportOut:
    install()
    c = as_ctx or ctx(school, person, role)
    body = PrecheckCreate(
        profile_key=profile_key,
        scope=ExportScopeIn(section_ids=[school.ids[k] for k in section_keys] or None),
        format=list(formats),
        language=language,
        include_sensitive=include_sensitive,
    )
    with tenant_session(school.tenant_id, person.user_id) as db:
        return exports.request_precheck(db, c, body)


def request_list(
    school: Any,
    person: Any,
    role: str,
    *,
    columns: tuple[str, ...] = ("admission_no", "full_name", "class", "section", "roll_no"),
    section_keys: tuple[str, ...] = ("section_9a",),
    fmt: str = "csv",
    language: str = "en",
    as_ctx: UserContext | None = None,
) -> ExportOut:
    install()
    c = as_ctx or ctx(school, person, role)
    body = StudentListCreate(
        columns=list(columns),
        scope=ExportScopeIn(section_ids=[school.ids[k] for k in section_keys] or None),
        format=fmt,
        language=language,
    )
    with tenant_session(school.tenant_id, person.user_id) as db:
        return exports.request_student_list(db, c, body)


def run(school: Any, export_id: uuid.UUID) -> str:
    install()
    return exports.run_export(school.tenant_id, export_id, renderer=FAKE)


def ready_export(
    school: Any,
    role: str,
    person: Any = None,
    *,
    section_keys: tuple[str, ...] = ("section_9a",),
) -> uuid.UUID:
    """A ready export of ``school`` requested by ``role``: a pre-check (XLSX) for holders of
    ``export.board``, else a student list (CSV) for holders of ``student.export``. The caller
    makes the students of ``section_keys`` first (never rely on another test's students)."""
    out_id = queued_export(school, role, person, section_keys=section_keys)
    status = run(school, out_id)
    assert status == "ready", status
    return out_id


def queued_export(
    school: Any,
    role: str,
    person: Any = None,
    *,
    section_keys: tuple[str, ...] = ("section_9a",),
) -> uuid.UUID:
    who = person or school.people[role]
    c = ctx(school, who, role)
    if c.has("export.board"):
        return request_precheck(
            school, who, role, formats=("xlsx",), section_keys=section_keys, as_ctx=c
        ).id
    return request_list(school, who, role, section_keys=section_keys, as_ctx=c).id


def export_row(admin: Engine, export_id: uuid.UUID) -> dict[str, Any]:
    with admin.connect() as c:
        row = c.execute(text("SELECT * FROM ops.exports WHERE id = :i"), {"i": export_id}).one()
    return dict(row._mapping)


def files(admin: Engine, export_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text("SELECT * FROM ops.export_files WHERE export_id = :i ORDER BY format"),
            {"i": export_id},
        )
        return [dict(r._mapping) for r in rows]


def audit_rows(admin: Engine, tenant_id: uuid.UUID, export_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT action, actor_type, actor_id, summary FROM audit.events "
                "WHERE tenant_id = :t AND resource_type = 'export' AND resource_id = :r "
                "ORDER BY seq"
            ),
            {"t": tenant_id, "r": export_id},
        )
        return [dict(r._mapping) for r in rows]


def notifications(admin: Engine, tenant_id: uuid.UUID, export_id: uuid.UUID) -> list[Any]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT template_key, recipient_membership_id FROM ops.notifications "
                "WHERE tenant_id = :t AND resource_id = :r ORDER BY created_at"
            ),
            {"t": tenant_id, "r": export_id},
        )
        return [(r[0], r[1]) for r in rows]


def stored(export_id: uuid.UUID, fmt: str) -> bytes:
    store = D.memory_store()
    keys = [k for k in store.objects if f"/exports/{export_id}/" in k and k.endswith(f".{fmt}")]
    assert len(keys) == 1, keys
    data: bytes = store.objects[keys[0]].data
    return data


def build_school(admin: Engine) -> Any:
    """A separate synthetic school with the structure of tests/api/world.py and one member per
    role (so export contents are deterministic)."""
    install()
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in (
        "principal",
        "office_admin",
        "office_staff",
        "exam_coordinator",
        "accountant",
        "auditor_readonly",
    ):
        s.people[role] = W.add_member(admin, s.tenant_id, [role])
    s.people["class_teacher"] = W.add_member(
        admin, s.tenant_id, ["class_teacher"], scopes=[("section", s.ids["section_9a"])]
    )
    s.people["teacher"] = W.add_member(
        admin, s.tenant_id, ["teacher"], scopes=[("class", s.ids["class_x"])]
    )
    s.people["coordinator_2"] = W.add_member(admin, s.tenant_id, ["exam_coordinator"])
    return s
