"""Import tests: the shared synthetic world (tests/api/world.py), shared students, the in-memory
object store and the import support helpers (support.py). Synthetic data only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


S = _load("sos_test_imports_support", Path(__file__).with_name("support.py"))
W = S.W
world = W.world
api = W.api


@pytest.fixture(autouse=True)
def _keyring_and_store() -> None:
    S.SW.configure_keyring()
    S.D.memory_store()


@pytest.fixture
def shared(world: Any) -> dict[str, Any]:
    ids: dict[str, Any] = S.SW.ensure_students(world)
    return ids
