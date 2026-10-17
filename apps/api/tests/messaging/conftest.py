"""Fixtures for the WhatsApp messaging tests (R2-1C; ADR-0043; synthetic data only).

The shared synthetic world (tests/api/world.py): school A with one member per role (class
teacher scoped to 9A, teacher to class X) and school B for cross-school checks. Messaging is on
(tests/conftest.py) with the fake provider; ``M`` is tests/messaging/support.py.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from app.messaging.providers.fake import STATE


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
D = _load("sos_test_documents_support", TESTS / "documents" / "support.py")
M = _load("sos_test_messaging_support", Path(__file__).with_name("support.py"))
W = SW.W
world = W.world
api = W.api


@pytest.fixture(autouse=True)
def _keyring() -> None:
    SW.configure_keyring()


@pytest.fixture
def fake_state() -> Iterator[object]:
    """The fake provider's record of calls, emptied around the test (templates survive in the
    shared schools: their rows were created already)."""
    STATE.sent.clear()
    STATE.failing.clear()
    STATE.forced.clear()
    yield STATE
    STATE.sent.clear()
    STATE.failing.clear()
    STATE.forced.clear()
