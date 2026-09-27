"""Fixtures for extraction tests: the synthetic world, shared students, in-memory S3 and a
check that the notification templates ship (see support.py). Synthetic data only."""

from __future__ import annotations

import importlib.util
import sys
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


X = _load("sos_test_extraction_support", Path(__file__).with_name("support.py"))
W = X.W
world = W.world
api = W.api
extraction_templates = X.extraction_templates


@pytest.fixture(autouse=True)
def _keyring_and_store() -> None:
    X.SW.configure_keyring()
    X.D.memory_store()


@pytest.fixture
def x() -> Any:
    return X
