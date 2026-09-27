"""Notification test fixtures: the shared synthetic world (``tests/api/world.py``) plus a fresh
school per test module (synthetic data only)."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Engine


def load_world() -> ModuleType:
    name = "sos_test_api_world"
    module = sys.modules.get(name)
    if module is None:
        path = Path(__file__).resolve().parents[1] / "api" / "world.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


W = load_world()
world = W.world
api = W.api


@dataclass
class Staff:
    tenant_id: uuid.UUID
    owner: object
    principal: object
    office_staff: object
    teacher: object


@pytest.fixture(scope="module")
def school(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Staff:
    tid = W.provision_school()
    return Staff(
        tenant_id=tid,
        owner=W.add_member(admin_engine, tid, ["owner"]),
        principal=W.add_member(admin_engine, tid, ["principal"]),
        office_staff=W.add_member(admin_engine, tid, ["office_staff"]),
        teacher=W.add_member(admin_engine, tid, ["teacher"]),
    )
