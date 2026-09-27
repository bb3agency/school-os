"""Fixtures for DQ tests: in-memory fact builders (checks_support.py), the synthetic API world
(tests/api/world.py via tests/students/student_world.py) and DQ engine helpers (dq_support.py).
Synthetic data only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

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


HERE = Path(__file__).resolve().parent
CS = _load("sos_test_dq_checks_support", HERE / "checks_support.py")
SW = _load("sos_test_student_world", HERE.parent / "students" / "student_world.py")
DS = _load("sos_test_dq_support", HERE / "dq_support.py")
W = SW.W
world = W.world
api = W.api


@pytest.fixture
def keyring() -> None:
    SW.configure_keyring()
