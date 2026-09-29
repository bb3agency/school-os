"""Fixtures for the attendance, exams and marks tests (synthetic data only).

Shares ``tests/insights/support.py`` (a separate synthetic school with a class teacher recorded
for 9A, students in 9A, 9C and 10A) with the early-warning tests.
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


S = _load(
    "sos_test_insights_support", Path(__file__).resolve().parents[1] / "insights" / "support.py"
)
W = S.W
world = W.world
api = W.api


@pytest.fixture(autouse=True)
def installed() -> Any:
    return S.install()


@pytest.fixture(scope="session")
def school(world: Any, admin_engine: Engine) -> Any:
    """A separate synthetic school (see ``tests/insights/support.insights_school``)."""
    return S.insights_school(admin_engine)
