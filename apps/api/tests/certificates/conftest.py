"""Fixtures for certificate tests (synthetic data only).

``school`` is a separate synthetic school (so certificates, serial numbers, notifications and
audit events of the shared world stay untouched) with the structure of ``tests/api/world.py``
(sections 9A, 9C, 10A in 2026-27) and these members:

    owner, principal        approvers (certificate.approve)
    office_admin            "Lakshmi": issues and prepares TCs (certificate.issue)
    dual                    office admin AND principal: holds both (self-approval)
    office_staff            issues (certificate.issue)
    accountant              no certificate permission
    auditor_readonly        reads certificates and registers
    scoped_clerk            custom role (read + issue + student.read_basic) scoped to 9A
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


C = _load("sos_test_certificates_support", Path(__file__).with_name("support.py"))
W = C.W
SW = C.SW
D = C.D
world = W.world
api = W.api


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "chromium: renders with the real headless Chromium (skipped when absent)"
    )


SCOPED_PERMISSIONS = (
    "student.read_basic",
    "certificate.read",
    "certificate.issue",
)


def _custom_role(
    admin: Engine, tenant_id: uuid.UUID, key: str, permissions: tuple[str, ...]
) -> None:
    role_id = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te) "
                "VALUES (:r, :t, :k, 'Synthetic scoped clerk', 'కృత్రిమ గుమస్తా')"
            ),
            {"r": role_id, "t": tenant_id, "k": key},
        )
        for perm in permissions:
            c.execute(
                text(
                    "INSERT INTO core.role_permissions (tenant_id, role_id, permission_key) "
                    "VALUES (:t, :r, :p)"
                ),
                {"t": tenant_id, "r": role_id, "p": perm},
            )


@pytest.fixture(scope="session")
def school(world: Any, admin_engine: Engine) -> Any:
    SW.configure_keyring()
    D.memory_store()
    s = W.School(W.provision_school())
    add = W.add_member
    s.people["owner"] = add(
        admin_engine, s.tenant_id, ["owner"], display_name="Synthetic Owner Prasad"
    )
    W.build_structure(s, s.people["owner"])
    s.people["principal"] = add(
        admin_engine, s.tenant_id, ["principal"], display_name="Synthetic Principal Suresh"
    )
    s.people["office_admin"] = add(
        admin_engine, s.tenant_id, ["office_admin"], display_name="Synthetic Clerk Lakshmi"
    )
    s.people["dual"] = add(
        admin_engine,
        s.tenant_id,
        ["office_admin", "principal"],
        display_name="Synthetic Vice Principal Lakshmi",
    )
    s.people["office_staff"] = add(admin_engine, s.tenant_id, ["office_staff"])
    s.people["accountant"] = add(admin_engine, s.tenant_id, ["accountant"])
    s.people["auditor_readonly"] = add(admin_engine, s.tenant_id, ["auditor_readonly"])
    _custom_role(admin_engine, s.tenant_id, "scoped_clerk", SCOPED_PERMISSIONS)
    s.people["scoped_clerk"] = add(
        admin_engine,
        s.tenant_id,
        ["scoped_clerk"],
        scopes=[("section", s.ids["section_9a"])],
    )
    return s


@pytest.fixture(autouse=True)
def _keyring_and_store() -> Any:
    return C.install()
