"""API test fixtures: see ``world.py`` (synthetic schools, staff and a signed-in client)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_world() -> ModuleType:
    """Load ``world.py`` once per process (tests are not importable packages here)."""
    name = "sos_test_api_world"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("world.py"))
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


W = load_world()
world = W.world
api = W.api
