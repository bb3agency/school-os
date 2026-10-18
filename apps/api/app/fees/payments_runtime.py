"""The settings online payments read (ADR-0042 decisions 3, 6; FR-PAY-001..004).

Every payments decision that depends on ``SOS_*`` settings (provider mode, the manual-keys
pilot switch, the OAuth partner application, the webhook origin) reads :func:`settings`, which
is ``app.core.config.get_settings()`` unless a test patched a few payment values with
:func:`patch_for_tests` (tests switch modes without changing the process environment, which
other settings tests build production settings from). Same pattern as ``app.messaging.runtime``.
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
    """Override some settings for payments only (tests); ``None`` removes the override."""
    global _patch  # noqa: PLW0603 - process-wide test hook by design
    with _lock:
        _patch = dict(values) if values is not None else None


__all__ = ["patch_for_tests", "settings"]
