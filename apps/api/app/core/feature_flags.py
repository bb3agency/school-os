"""Read-only feature-flag evaluation for tenant-side code (FR-PLT-022; docs/16 §5.11).

Tenant modules may not import the control plane (``app.platform``; import-linter
``tenant-side-without-control-plane``), but ``sos_app`` may SELECT ``platform.feature_flags``,
the one platform table it can read (CLAUDE.md invariant 1, migration 0005_platform). This module
is that read: one bound-parameter SELECT and the same evaluation rule as
``app.platform.flags`` (a school override wins; otherwise the global row, and with a rollout
percentage the school is in when a stable SHA-256 bucket of ``key:tenant_id`` is below it).
Unknown flags are off (fail closed). ``tests/core/test_feature_flags.py`` pins the rule against
``app.platform.flags`` so the two cannot drift.

Writes stay in the control plane (operators, audited there). Nothing here carries tenant data.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import context_free_session

_FLAG_ROWS = text(
    "SELECT tenant_id, enabled, rollout_percent FROM platform.feature_flags "
    "WHERE key = :key AND (tenant_id IS NULL OR tenant_id = :tenant_id)"
)


def bucket(key: str, tenant_id: uuid.UUID) -> int:
    """Stable 0..99 bucket of a school for a flag's percentage rollout."""
    digest = hashlib.sha256(f"{key}:{tenant_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 100


def evaluate(rows: Iterable[Mapping[str, Any]], key: str, tenant_id: uuid.UUID) -> bool:
    global_row: Mapping[str, Any] | None = None
    for row in rows:
        if row["tenant_id"] == tenant_id:
            return bool(row["enabled"])
        if row["tenant_id"] is None:
            global_row = row
    if global_row is None or not global_row["enabled"]:
        return False
    percent = global_row["rollout_percent"]
    return percent is None or bucket(key, tenant_id) < int(percent)


def _rows(session: Session, key: str, tenant_id: uuid.UUID) -> list[Mapping[str, Any]]:
    result = session.execute(_FLAG_ROWS, {"key": key, "tenant_id": tenant_id})
    return [dict(r) for r in result.mappings()]


def is_enabled(key: str, tenant_id: uuid.UUID, *, session: Session | None = None) -> bool:
    """Whether ``key`` is on for the school (any ``sos_app`` session, or a context-free one)."""
    if session is not None:
        return evaluate(_rows(session, key, tenant_id), key, tenant_id)
    with context_free_session() as s:
        return evaluate(_rows(s, key, tenant_id), key, tenant_id)


__all__ = ["bucket", "evaluate", "is_enabled"]
