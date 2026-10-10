"""Fixtures for APAAR consent register tests (synthetic data only; ADR-0039).

Uses the shared synthetic world (tests/api/world.py): school A with one member per role (class
teacher scoped to 9A, teacher to class X) and school B for cross-tenant ids. Students are created
through the real student service (tests/students/student_world.py).
"""

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


TESTS = Path(__file__).resolve().parents[1]
SW = _load("sos_test_student_world", TESTS / "students" / "student_world.py")
D = _load("sos_test_documents_support", TESTS / "documents" / "support.py")
A = _load("sos_test_apaar_support", Path(__file__).with_name("support.py"))
W = SW.W
world = W.world
api = W.api


@pytest.fixture(autouse=True)
def _keyring() -> None:
    SW.configure_keyring()
