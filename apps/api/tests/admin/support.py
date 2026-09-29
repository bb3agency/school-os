"""Admin test objects created through the real services (synthetic data only).

Loaded by path (pytest runs with ``--import-mode=importlib``) from ``tests/admin`` and the
security suites (authz matrix, BOLA, suspended school). Builds on ``tests/api/world.py``
(schools and staff), ``tests/students/student_world.py`` (students, keyring) and
``tests/documents/support.py`` (in-memory object store).
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import importlib.util
import io
import json
import sys
import uuid
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

from app.admin import service as admin
from app.admin.schemas import TenantExportCreate, TenantExportOut
from app.authz.context import UserContext
from app.core.db import tenant_session

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


W = _load("sos_test_api_world", _HERE.parents[1] / "api" / "world.py")
SW = _load("sos_test_student_world", _HERE.parents[1] / "students" / "student_world.py")
D = _load("sos_test_documents_support", _HERE.parents[1] / "documents" / "support.py")


def install() -> Any:
    """Keyring and the in-memory object store for this process."""
    SW.configure_keyring()
    return D.memory_store()


def ctx(school: Any, person: Any, role: str, **scopes: Any) -> UserContext:
    """The role's permissions with a fresh MFA sign-in (step-up satisfied)."""
    base: UserContext = SW.ctx_for(school.tenant_id, person, role, **scopes)
    return dataclasses.replace(base, auth_time=dt.datetime.now(dt.UTC))


def request(
    school: Any,
    *,
    role: str = "owner",
    include_sensitive: bool = False,
    as_ctx: UserContext | None = None,
) -> TenantExportOut:
    install()
    person = school.people[role]
    c = as_ctx or ctx(school, person, role)
    with tenant_session(school.tenant_id, person.user_id) as db:
        return admin.request_export(db, c, TenantExportCreate(include_sensitive=include_sensitive))


def run(school: Any, export_id: uuid.UUID) -> str:
    install()
    return admin.run_export(school.tenant_id, export_id)


def settle(admin_engine: Engine, school: Any) -> None:
    """Finish any export of ``school`` still queued or running (tests share schools: the next
    request would get 409 ``tenant_export_in_progress``)."""
    with admin_engine.connect() as c:
        live: list[object] = list(
            c.execute(
                text(
                    "SELECT id FROM ops.tenant_exports WHERE tenant_id = :t "
                    "AND status IN ('queued','running')"
                ),
                {"t": school.tenant_id},
            ).scalars()
        )
        ids = [uuid.UUID(str(i)) for i in live]
    for export_id in ids:
        run(school, export_id)


def ready_export(
    admin_engine: Engine, school: Any, *, include_sensitive: bool = False
) -> uuid.UUID:
    """A ready full export of ``school`` requested by its owner."""
    settle(admin_engine, school)
    out = request(school, include_sensitive=include_sensitive)
    status = run(school, out.id)
    assert status == "ready", status
    return out.id


def queued_export(admin_engine: Engine, school: Any) -> uuid.UUID:
    settle(admin_engine, school)
    return request(school).id


def row(admin_engine: Engine, export_id: uuid.UUID) -> dict[str, Any]:
    with admin_engine.connect() as c:
        r = c.execute(
            text("SELECT * FROM ops.tenant_exports WHERE id = :i"), {"i": export_id}
        ).one()
    return dict(r._mapping)


def archive(school: Any, export_id: uuid.UUID) -> zipfile.ZipFile:
    store = D.memory_store()
    key = f"t/{school.tenant_id}/tenant-export/{export_id}.zip"
    return zipfile.ZipFile(io.BytesIO(store.objects[key].data))


def records_csv(zf: zipfile.ZipFile, table: str) -> list[dict[str, str]]:
    raw = zf.read(f"records/{table}.csv").decode("utf-8")
    assert raw.startswith("﻿"), "CSV starts with a BOM (Excel, Telugu)"
    return list(csv.DictReader(io.StringIO(raw[1:])))


def records_json(zf: zipfile.ZipFile, table: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(zf.read(f"records/{table}.json"))
    return value


def audit_rows(admin_engine: Engine, tenant_id: uuid.UUID, export_id: uuid.UUID) -> list[Any]:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT action, actor_type, actor_id, summary FROM audit.events "
                "WHERE tenant_id = :t AND resource_type = 'tenant_export' AND resource_id = :r "
                "ORDER BY seq"
            ),
            {"t": tenant_id, "r": export_id},
        )
        return [dict(r._mapping) for r in rows]


def notifications(admin_engine: Engine, tenant_id: uuid.UUID, export_id: uuid.UUID) -> list[Any]:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT template_key, recipient_membership_id, params FROM ops.notifications "
                "WHERE tenant_id = :t AND resource_id = :r ORDER BY created_at"
            ),
            {"t": tenant_id, "r": export_id},
        )
        return [tuple(r) for r in rows]


def build_school(admin_engine: Engine) -> Any:
    """A separate synthetic school (structure of tests/api/world.py) with one member per role
    of interest, so an archive's contents are exactly what a test made."""
    install()
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin_engine, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in ("principal", "office_admin", "office_staff", "accountant", "auditor_readonly"):
        s.people[role] = W.add_member(admin_engine, s.tenant_id, [role])
    s.people["owner_2"] = W.add_member(admin_engine, s.tenant_id, ["owner"])
    return s
