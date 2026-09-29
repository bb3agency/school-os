"""Fixtures for the circulars, tasks and notices tests (synthetic data only).

Schools A and B of ``tests/api/world.py`` (one member per role); AI is switched on for both
(the ``kb.ask.enabled`` flag). The keyring, the in-memory object store, the knowledge runtime
with the offline fake provider and fake PDF/PNG renderers are installed for every test.
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

from app.core import pdf
from app.knowledge import composition


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


C = _load("sos_test_circulars_support", Path(__file__).with_name("support.py"))
W = C.W
world = W.world
api = W.api


@pytest.fixture(autouse=True)
def installed() -> Iterator[tuple[Any, Any, Any]]:
    yield C.install()
    pdf.set_renderer(None)
    pdf.set_image_renderer(None)
    composition.set_runtime(None)


@pytest.fixture(scope="session")
def ai_on(world: Any, admin_engine: Engine) -> Any:
    for school in (world.a, world.b):
        C.KB.enable_ai(admin_engine, school.tenant_id)
    return world
