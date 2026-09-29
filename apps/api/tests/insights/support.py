"""Synthetic set-up for the attendance, marks and early-warning tests (M5; synthetic data only).

Loaded by path (``--import-mode=importlib``). Builds on ``tests/api/world.py`` (schools A and B,
one member per role; class teacher scoped to section 9A, subject teacher to class X) and
``tests/students/student_world.py`` (``s9a`` in 9A, ``s9c`` in 9C, ``s10a`` in 10A, ``sb`` in
school B).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

TESTS = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


SW = _load("sos_test_student_world", TESTS / "students" / "student_world.py")
W = SW.W
D = _load("sos_test_documents_support", TESTS / "documents" / "support.py")


def install() -> Any:
    """Keyring for the synthetic schools and an in-memory object store."""
    SW.configure_keyring()
    return D.memory_store()
