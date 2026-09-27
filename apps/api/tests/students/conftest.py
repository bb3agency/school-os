"""Fixtures for student tests: the synthetic world (tests/api/world.py), the shared synthetic
students (student_world.py) and the process keyring. Synthetic data only."""

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


SW = _load("sos_test_student_world", Path(__file__).with_name("student_world.py"))
W = SW.W
world = W.world
api = W.api


@pytest.fixture(autouse=True)
def _keyring() -> None:
    SW.configure_keyring()


@pytest.fixture
def shared(world: Any) -> dict[str, Any]:
    """Ids of the shared synthetic students (see student_world.py)."""
    ids: dict[str, Any] = SW.ensure_students(world)
    return ids
