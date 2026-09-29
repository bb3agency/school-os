"""Fixtures for admin tests (synthetic data only).

``school`` is a separate synthetic school (structure of tests/api/world.py: sections 9A, 9C,
10A) with an owner, a second owner, a principal and other staff, so the contents of a full
export are exactly what the tests made. The in-memory object store and the keyring are
installed for every test.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


AD = _load("sos_test_admin_support", Path(__file__).with_name("support.py"))
W = AD.W
world = W.world
api = W.api


@pytest.fixture(scope="session")
def school(world: Any, admin_engine: Engine) -> Any:
    return AD.build_school(admin_engine)


@pytest.fixture(autouse=True)
def _installed() -> None:
    AD.install()
