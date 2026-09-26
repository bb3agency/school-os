"""Documents test fixtures: the shared synthetic world plus an in-memory object store."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load_support() -> ModuleType:
    name = "sos_test_documents_support"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("support.py"))
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


S = load_support()
W = S.load_world()
world = W.world
api = W.api


@pytest.fixture
def store() -> object:
    return S.memory_store()
