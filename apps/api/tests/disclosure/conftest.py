"""Fixtures for the CBSE disclosure tests (R3-1C; ADR-0051). Synthetic data only."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.authz.kv import InMemoryKV, set_kv_store
from app.core import pdf


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


S = _load("sos_test_disclosure_support", Path(__file__).with_name("support.py"))
W = S.W
world = W.world
api = W.api


@pytest.fixture(autouse=True)
def _keyring() -> None:
    S.SW.configure_keyring()


@pytest.fixture
def renderer() -> Iterator[Any]:
    fake = S.FakeRenderer()
    pdf.set_renderer(fake)
    yield fake
    pdf.set_renderer(None)


@pytest.fixture
def school(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    """A fresh CBSE school per test (one live disclosure link per school)."""
    S.D.memory_store()
    return S.cbse_school(admin_engine)


@pytest.fixture
def public_client(app_engine: Engine, platform_engine: Engine) -> Iterator[TestClient]:
    set_kv_store(InMemoryKV())
    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client
    set_kv_store(None)
