"""Fixtures for the public surface tests (R2-0; ADR-0047, ADR-0046, ADR-0044). Synthetic data only.

Uses the shared synthetic world (tests/api/world.py): school A with one member per role and
school B for cross-school checks; guardians are added through the real student service
(tests/students/student_world.py). Public routes are called like the BFF calls them: a fresh
service token per request and no user token (``P.public_call``).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.authz.kv import InMemoryKV, set_kv_store


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
P = _load("sos_test_public_support", Path(__file__).with_name("support.py"))
W = SW.W
world = W.world
api = W.api


@pytest.fixture(autouse=True)
def _keyring() -> None:
    SW.configure_keyring()


@pytest.fixture
def public_client(app_engine: Engine, platform_engine: Engine) -> Iterator[TestClient]:
    """The real app with real authentication (no principal override): public routes take the
    BFF service token only."""
    set_kv_store(InMemoryKV())
    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client
    set_kv_store(None)
