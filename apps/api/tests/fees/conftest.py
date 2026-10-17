"""Fixtures for fee tests (synthetic data only).

``school`` is a separate synthetic school (so receipts, numbers, notifications and audit events of
the shared world stay untouched) with the structure of ``tests/api/world.py`` (sections 9A, 9C,
10A in 2026-27), fees switched on (flag and setting) and these members:

    owner, principal, office_admin, office_staff, accountant, auditor_readonly, class_teacher (9A),
    teacher (Class X), accountant2 (a second accountant: the other half of maker-checker),
    scoped_collector (custom role with fee.collect and student.read_basic scoped to 9A: fee
    permissions are never section-scoped, FR-FEE-024)
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


F = _load("sos_test_fees_support", Path(__file__).with_name("support.py"))
# R2-2E: synthetic bank statements and UPI helpers.
U = _load("sos_test_fees_upi_support", Path(__file__).with_name("upi_support.py"))
P = _load("sos_test_fees_online_support", Path(__file__).with_name("online_support.py"))
W = F.W
world = W.world
api = W.api


def _custom_role(
    admin: Engine, tenant_id: uuid.UUID, key: str, permissions: tuple[str, ...]
) -> None:
    role_id = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te) "
                "VALUES (:r, :t, :k, 'Synthetic scoped collector', 'కృత్రిమ')"
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
    F.install()
    s = W.School(W.provision_school())
    add = W.add_member
    s.people["owner"] = add(admin_engine, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in ("principal", "office_admin", "office_staff", "accountant", "auditor_readonly"):
        s.people[role] = add(admin_engine, s.tenant_id, [role])
    s.people["accountant2"] = add(admin_engine, s.tenant_id, ["accountant"])
    s.people["class_teacher"] = add(
        admin_engine, s.tenant_id, ["class_teacher"], scopes=[("section", s.ids["section_9a"])]
    )
    s.people["teacher"] = add(
        admin_engine, s.tenant_id, ["teacher"], scopes=[("class", s.ids["class_x"])]
    )
    _custom_role(
        admin_engine, s.tenant_id, "scoped_collector", ("student.read_basic", "fee.collect")
    )
    s.people["scoped_collector"] = add(
        admin_engine, s.tenant_id, ["scoped_collector"], scopes=[("section", s.ids["section_9a"])]
    )
    F.enable(admin_engine, s.tenant_id)
    return s


@pytest.fixture(scope="session")
def other_school(world: Any, admin_engine: Engine) -> Any:
    """A second synthetic school with fees on (owner, accountant, office staff): the other
    school of wrong-school webhook and cross-school checks (R2-2D)."""
    F.install()
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin_engine, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in ("accountant", "office_staff"):
        s.people[role] = W.add_member(admin_engine, s.tenant_id, [role])
    F.enable(admin_engine, s.tenant_id)
    return s


@pytest.fixture(autouse=True)
def _keyring_and_store() -> Any:
    return F.install()


@pytest.fixture(autouse=True)
def _fresh_fake_provider() -> Any:
    """The fake provider's failure switches are per test (its links and payments may stay)."""
    from app.fees.providers.fake import STATE

    STATE.fail_next.clear()
    STATE.dead.clear()
    yield STATE
    STATE.fail_next.clear()
    STATE.dead.clear()
