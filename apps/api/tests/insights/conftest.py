"""Fixtures for the student timeline and early-warning tests (synthetic data only).

Schools A and B of ``tests/api/world.py`` (one member per role; class teacher scoped to 9A) with
the shared synthetic students of ``tests/students/student_world.py``; ``support.py`` adds
attendance, marks, notes and flags through the real services.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
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


S = _load("sos_test_insights_support", Path(__file__).with_name("support.py"))
W = S.W
world = W.world
api = W.api


@pytest.fixture(autouse=True)
def installed() -> Iterator[Any]:
    yield S.install()
