"""Fixtures for privacy request tests (synthetic data only; ADR-0048).

``world`` (tests/api/world.py): school A with one member per role and school B for cross-school
ids; ``school``: a separate synthetic school for tests that count rows or change settings. The
in-memory object store, the keyring and the fake PDF renderer come from tests/exports/objects.py.
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


TESTS = Path(__file__).resolve().parents[1]
EX = _load("sos_test_exports_objects", TESTS / "exports" / "objects.py")
P = _load("sos_test_privacy_support", Path(__file__).with_name("support.py"))
W = EX.W
world = W.world
api = W.api


@pytest.fixture(scope="session")
def school(world: Any, admin_engine: Engine) -> Any:
    return EX.build_school(admin_engine)


@pytest.fixture(autouse=True)
def _installed() -> None:
    EX.install()
