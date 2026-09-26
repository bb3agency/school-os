"""Synthetic students for student-module and security tests (synthetic data only).

Loaded by path (pytest runs with ``--import-mode=importlib``) from ``tests/students`` and from
the security suites. Builds on ``tests/api/world.py``: school A (sections 9A, 9C, 10A; one
member per role; class teacher scoped to 9A, teacher scoped to class X) and school B.

:func:`ensure_students` creates, once per process and school, through the real service:

    school A  s9a    enrolled in 9A: identity values (admission register), mother tongue,
                     health notes (C3), Aadhaar last 4 (C3), one guardian with phone + address
              s9c    enrolled in 9C (outside the class teacher's scope)
              s10a   enrolled in 10A (the subject teacher's class X)
              mover  enrolled in 9A, moved between sections by the authz matrix
    school B  sb     enrolled in 9A of school B, with a value and a guardian (cross-tenant ids)
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

from app.authz.catalog import implicit_permissions, system_roles
from app.authz.context import Scopes, UserContext
from app.core.db import tenant_session
from app.students import crypto
from app.students import service as students
from app.students.schemas import GuardianCreate, StudentCreate, ValueIn


def load_world() -> ModuleType:
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


W = load_world()
_CACHE: dict[uuid.UUID, dict[str, uuid.UUID]] = {}
_configured = False


def configure_keyring() -> None:
    """Point the process keyring at the synthetic CI wrapper that provisioned the schools."""
    global _configured  # noqa: PLW0603 - test process setup
    if not _configured:
        crypto.set_key_wrapper(W.wrapper())
        _configured = True


def ctx_for(
    tenant_id: uuid.UUID,
    person: Any,
    role: str,
    *,
    section_ids: frozenset[uuid.UUID] = frozenset(),
    class_ids: frozenset[uuid.UUID] = frozenset(),
) -> UserContext:
    """A UserContext with exactly the permissions of one system role (like the resolver)."""
    template = system_roles()[role]
    perms = set(template.permission_keys) | set(implicit_permissions())
    scoped = {p for p in template.permission_keys if (g := template.grant(p)) and g.scoped}
    return UserContext(
        user_id=person.user_id,
        tenant_id=tenant_id,
        membership_id=person.membership_id,
        roles=frozenset({role}),
        permissions=frozenset(perms),
        scopes=Scopes(
            school=not section_ids and not class_ids,
            class_ids=class_ids,
            section_ids=section_ids,
        ),
        mfa=True,
        auth_time=None,
        scoped_permissions=frozenset(scoped),
    )


def admin_ctx(school: Any) -> UserContext:
    """Office-admin permissions acting as the school's owner account (setup only)."""
    return ctx_for(school.tenant_id, school.people["owner"], "office_admin")


def identity_values(name: str, dob: str, father: str, mother: str) -> list[ValueIn]:
    src = "admission_register"
    return [
        ValueIn(attribute_key="full_name", source=src, value=name),
        ValueIn(attribute_key="dob", source=src, value=dob),
        ValueIn(attribute_key="gender", source=src, value="male"),
        ValueIn(attribute_key="father_name", source=src, value=father),
        ValueIn(attribute_key="mother_name", source=src, value=mother),
    ]


def create(
    school: Any,
    *,
    name: str,
    section_key: str | None,
    extra: list[ValueIn] | None = None,
    admission_no: str | None = None,
) -> uuid.UUID:
    configure_keyring()
    values = identity_values(name, "2012-03-14", "Kommineni Ramana", "Kommineni Sarada")
    if admission_no:
        values.append(ValueIn(attribute_key="admission_no", source="admission_register", value=admission_no))
    values.extend(extra or [])
    section_id = school.ids[section_key] if section_key else None
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        out = students.create_student(
            db, admin_ctx(school), StudentCreate(values=values, section_id=section_id)
        )
    return out.id


def add_guardian(school: Any, student_id: uuid.UUID, **fields: Any) -> uuid.UUID:
    configure_keyring()
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        out = students.add_guardian(
            db, admin_ctx(school), student_id, GuardianCreate(relationship="father", **fields)
        )
    return out.id


def ensure_students(w: Any) -> dict[str, uuid.UUID]:
    """Ids of the shared synthetic students (created once per process)."""
    configure_keyring()
    a = _CACHE.get(w.a.tenant_id)
    if a is None:
        sensitive = [
            ValueIn(attribute_key="mother_tongue", source="parent_form", value="Telugu"),
            ValueIn(attribute_key="health_notes", source="parent_form", value="Synthetic asthma note"),
            ValueIn(attribute_key="aadhaar_last4", source="aadhaar_as_printed", value="4821"),
        ]
        a = {
            "s9a": create(w.a, name="Synthetica Venkata Sai", section_key="section_9a", extra=sensitive),
            "s9c": create(w.a, name="Synthetica Lakshmi Devi", section_key="section_9c"),
            "s10a": create(w.a, name="Synthetica Ravi Teja", section_key="section_10a", extra=sensitive),
            "mover": create(w.a, name="Synthetica Mover Kumar", section_key="section_9a"),
        }
        a["g9a"] = add_guardian(
            w.a, a["s9a"], full_name="Synthetica Ramana", phone="9876501234", address="Synthetic street 1"
        )
        _CACHE[w.a.tenant_id] = a
    b = _CACHE.get(w.b.tenant_id)
    if b is None:
        sb = create(
            w.b,
            name="Synthetica Other School",
            section_key="section_9a",
            extra=[ValueIn(attribute_key="mother_tongue", source="parent_form", value="Telugu")],
        )
        b = {"sb": sb, "gb": add_guardian(w.b, sb, full_name="Synthetica Guardian B", phone="9876505678")}
        _CACHE[w.b.tenant_id] = b
    return {**a, **{f"b_{k}": v for k, v in b.items()}}


def current_value_id(admin: Engine, student_id: uuid.UUID, key: str, source: str) -> uuid.UUID:
    with admin.connect() as c:
        return uuid.UUID(
            str(
                c.execute(
                    text(
                        "SELECT id FROM sis.attribute_values WHERE student_id = :s "
                        "AND attribute_key = :k AND source = :src AND superseded_by IS NULL"
                    ),
                    {"s": student_id, "k": key, "src": source},
                ).scalar_one()
            )
        )


def version(admin: Engine, table: str, row_id: uuid.UUID) -> int:
    assert table in {"sis.students", "sis.guardians", "sis.enrollments"}
    with admin.connect() as c:
        return int(
            c.execute(text(f"SELECT version FROM {table} WHERE id = :i"), {"i": row_id}).scalar_one()
        )


def active_section(admin: Engine, student_id: uuid.UUID) -> uuid.UUID | None:
    with admin.connect() as c:
        value = c.execute(
            text(
                "SELECT section_id FROM sis.enrollments WHERE student_id = :s AND status = 'active' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"s": student_id},
        ).scalar_one_or_none()
    return uuid.UUID(str(value)) if value is not None else None
