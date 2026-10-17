"""The settings messaging reads (ADR-0043; FR-MSG-001, FR-MSG-003, FR-MSG-024).

Every messaging decision that depends on ``SOS_*`` settings (on or off, the manual-token mode,
the Meta app secret, the public origin for URL buttons) reads :func:`settings`, which is
``app.core.config.get_settings()`` unless a test has patched a few messaging values with
:func:`patch_for_tests` (the test suite turns messaging on without changing the process
environment, which other settings tests build production settings from).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from app.core.config import Settings, get_settings

_lock = threading.Lock()
_patch: dict[str, Any] | None = None


def settings() -> Settings:
    with _lock:
        patch = dict(_patch) if _patch else None
    base = get_settings()
    return base.model_copy(update=patch) if patch else base


def patch_for_tests(values: Mapping[str, Any] | None) -> None:
    """Override some settings for messaging only (tests); ``None`` removes the override."""
    global _patch  # noqa: PLW0603 - process-wide test hook by design
    with _lock:
        _patch = dict(values) if values is not None else None


def current_patch() -> dict[str, Any] | None:
    with _lock:
        return dict(_patch) if _patch is not None else None


__all__ = ["current_patch", "patch_for_tests", "settings"]
