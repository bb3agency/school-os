"""Fixtures for change-request tests (synthetic data only).

``school`` is a separate synthetic school (so notifications, attributes and audit events of the
shared world stay untouched) with the structure of ``tests/api/world.py`` (sections 9A, 9C, 10A)
and these members:

    owner, principal        approvers (student.identity_change.approve)
    office_admin            "Lakshmi": maker only (student.identity_change.request)
    dual                    office admin AND principal: holds both permissions (self-approval)
    office_staff            maker only
    accountant              neither permission
    scoped_clerk            custom role (request + read + document.read) scoped to section 9A
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


CR = _load("sos_test_changes_objects", Path(__file__).with_name("objects.py"))
W = CR.W
SW = CR.SW
D = CR.D
world = W.world
api = W.api

SCOPED_PERMISSIONS = (
    "student.read_basic",
    "student.identity_change.request",
    "document.read",
    "document.upload",
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
    _custom_role(admin_engine, s.tenant_id, "scoped_clerk", SCOPED_PERMISSIONS)
    s.people["scoped_clerk"] = add(
        admin_engine,
        s.tenant_id,
        ["scoped_clerk"],
        scopes=[("section", s.ids["section_9a"])],
    )
    return s


@pytest.fixture(autouse=True)
def _keyring_and_store() -> None:
    SW.configure_keyring()
    D.memory_store()


C3_KEY = "birth_place"


@pytest.fixture(scope="session")
def c3_identity(school: Any, admin_engine: Engine) -> str:
    """A school-defined identity attribute classified C3 (encrypted, masked) in ``school``."""
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO sis.attribute_definitions (id, tenant_id, key, data_type, "
                "classification, is_identity, validation, canonical_policy, label_en, label_te, "
                "sort_order) VALUES (:i, :t, :k, 'text', 'C3', true, "
                "CAST(:v AS jsonb), CAST(:p AS jsonb), 'Place of birth', 'జన్మ స్థలం', 900)"
            ),
            {
                "i": uuid.uuid4(),
                "t": school.tenant_id,
                "k": C3_KEY,
                "v": '{"max_length": 120}',
                "p": '{"precedence": ["admission_register", "birth_certificate"], '
                '"require_verified": true, "anchor": "admission_register", '
                '"show_conflicts_from": []}',
            },
        )
    return C3_KEY
