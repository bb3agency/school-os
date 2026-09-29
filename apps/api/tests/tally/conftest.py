"""Fixtures for the Tally connector tests (M6; ADR-0032; synthetic data only).

Schools A and B of ``tests/api/world.py`` (one member per role); most tests make their own
school with ``T.fresh_school`` (flag ``tally.connector.enabled`` on unless asked otherwise).
The API client wraps device secrets with the synthetic CI key wrapper.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine

from app.tally import agent_auth


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


@pytest.fixture
def api(app_engine: Engine, platform_engine: Engine) -> Iterator[Any]:
    """The world's API client; device secrets are wrapped with the synthetic CI key wrapper."""
    for client in W.make_client():
        client.client.app.dependency_overrides[agent_auth.get_agent_key_wrapper] = W.wrapper
        yield client
