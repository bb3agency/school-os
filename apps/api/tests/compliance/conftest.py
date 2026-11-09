"""Fixtures for the compliance certificate register tests (FC-01). Synthetic data only."""

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


S = _load("sos_test_compliance_support", Path(__file__).with_name("support.py"))
W = S.W
world = W.world
api = W.api


@pytest.fixture
def school(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    """A fresh AP State Board school per test (the register is per school)."""
    S.D.memory_store()
    return S.make_school(admin_engine)
