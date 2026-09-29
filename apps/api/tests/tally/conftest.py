"""Fixtures for the Tally connector tests (M6; ADR-0032; synthetic data only).

Schools A and B of ``tests/api/world.py`` (one member per role). The connector flag
``tally.connector.enabled`` is switched on for both by :func:`tally_on` (session scope); tests
that check the flag-off behaviour use their own school or switch it off and back.
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


T = _load("sos_test_tally_support", Path(__file__).with_name("support.py"))
W = T.W
world = W.world
api = W.api


@pytest.fixture(scope="session")
def tally_on(world: Any, admin_engine: Engine) -> Any:
    for school in (world.a, world.b):
        T.set_flag(admin_engine, school.tenant_id, enabled=True)
    return world
