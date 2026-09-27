"""DQ engine test helpers (synthetic data only). Loaded by path from tests/dq and the security
suites (``sos_test_dq_support``); builds on tests/students/student_world.py."""

from __future__ import annotations

import importlib.util
import random
import string
import sys
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

from app.authz.context import UserContext
from app.core.db import tenant_session
from app.dq import service as dq
from app.students.schemas import ValueIn


def _student_world() -> ModuleType:
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


SW = _student_world()
AAD = "aadhaar_as_printed"


def aadhaar(
    name: str | None = None,
    dob: str | None = None,
    gender: str | None = None,
    last4: str | None = None,
) -> list[ValueIn]:
    out = []
    for key, value in (
        ("aadhaar_name_as_printed", name),
        ("aadhaar_dob_as_printed", dob),
        ("aadhaar_gender_as_printed", gender),
        ("aadhaar_last4", last4),
    ):
        if value is not None:
            out.append(ValueIn(attribute_key=key, source=AAD, value=value))
    return out


def unique_name(prefix: str = "Kommineni Venkata") -> str:
    """A register name no other test student shares (so DQ-008 pairs only when a test wants)."""
    word = "".join(random.choices(string.ascii_lowercase, k=9)).capitalize()
    return f"{prefix} {word}"


def student(
    school: Any,
    *,
    name: str | None = None,
    section_key: str | None = "section_9a",
    extra: Iterable[ValueIn] = (),
    admission_no: str | None = None,
) -> uuid.UUID:
    """A synthetic student (register identity values: dob 2012-03-14, male, parents); the name
    is unique unless given."""
    sid: uuid.UUID = SW.create(
        school,
        name=name or unique_name(),
        section_key=section_key,
        extra=list(extra),
        admission_no=admission_no,
    )
    return sid


def ctx(school: Any, role: str = "office_admin", person: Any = None) -> UserContext:
    who = person or school.people.get(role) or school.people["owner"]
    sections = frozenset({school.ids["section_9a"]}) if role == "class_teacher" else frozenset()
    classes = frozenset({school.ids["class_x"]}) if role == "teacher" else frozenset()
    result: UserContext = SW.ctx_for(
        school.tenant_id, who, role, section_ids=sections, class_ids=classes
    )
    return result


def call(school: Any, fn: Callable[..., Any], *args: Any, as_ctx: Any = None, **kw: Any) -> Any:
    c = as_ctx or ctx(school)
    with tenant_session(school.tenant_id, c.user_id) as db:
        return fn(db, c, *args, **kw)


def run(school: Any, *student_ids: uuid.UUID, profile_key: str | None = None, **kw: Any) -> Any:
    return call(school, dq.run_checks, student_ids=student_ids, profile_key=profile_key, **kw)


def findings(
    admin: Engine, student_id: uuid.UUID, rule_id: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM sis.dq_findings WHERE student_id = :s"
    params: dict[str, Any] = {"s": student_id}
    if rule_id is not None:
        sql += " AND rule_id = :r"
        params["r"] = rule_id
    with admin.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql + " ORDER BY rule_id, id"), params)]


def one(admin: Engine, student_id: uuid.UUID, rule_id: str) -> dict[str, Any]:
    rows = findings(admin, student_id, rule_id)
    assert len(rows) == 1, rows
    return rows[0]


def one_by_id(admin: Engine, finding_id: uuid.UUID) -> dict[str, Any]:
    with admin.connect() as c:
        row = c.execute(
            text("SELECT * FROM sis.dq_findings WHERE id = :f"), {"f": finding_id}
        ).one()
    return dict(row._mapping)


def outbox(admin: Engine, tenant_id: uuid.UUID, event_type: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT payload FROM ops.outbox WHERE tenant_id = :t AND event_type = :e "
                "ORDER BY created_at"
            ),
            {"t": tenant_id, "e": event_type},
        )
        return [dict(r[0]) for r in rows]


def conflict_student(school: Any, **kw: Any) -> uuid.UUID:
    """A student whose Aadhaar gender differs from the register (DQ-003, severity high)."""
    return student(school, extra=aadhaar(gender="female"), **kw)


def high_finding(school: Any, section_key: str = "section_9a") -> uuid.UUID:
    """A fresh open DQ-003 (high) finding id in ``section_key`` (checked by the office admin)."""
    sid = conflict_student(school, section_key=section_key)
    run(school, sid)
    with tenant_session(school.tenant_id) as db:
        value: object = db.execute(
            text("SELECT id FROM sis.dq_findings WHERE student_id = :s AND rule_id = 'DQ-003'"),
            {"s": sid},
        ).scalar_one()
    return uuid.UUID(str(value))


def blocker_finding(school: Any) -> uuid.UUID:
    """A fresh open DQ-002 (blocker) finding id in 9A."""
    sid = student(school, extra=aadhaar(dob="2012-05-14"))
    run(school, sid)
    with tenant_session(school.tenant_id) as db:
        value: object = db.execute(
            text("SELECT id FROM sis.dq_findings WHERE student_id = :s AND rule_id = 'DQ-002'"),
            {"s": sid},
        ).scalar_one()
    return uuid.UUID(str(value))


def change_request(
    admin: Any, school: Any, student_id: uuid.UUID, attribute_key: str = "dob", new_value: str = ""
) -> uuid.UUID:
    """A real pending change request (tests/changes/objects.py, submitted by the office admin),
    so findings can link to it (dq_findings_change_request_fk, 0014)."""
    name = "sos_test_changes_objects"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "changes" / "objects.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    objects = sys.modules[name]
    requester = school.people.get("office_admin") or school.people["owner"]
    defaults = {"dob": "2012-03-15", "gender": "female"}
    out = objects.submit(
        admin,
        school,
        requester,
        "office_admin",
        student_id=student_id,
        attribute_key=attribute_key,
        new_value=new_value or defaults[attribute_key],
    )
    value: uuid.UUID = out.id
    return value
