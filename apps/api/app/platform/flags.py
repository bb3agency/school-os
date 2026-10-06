"""Feature flags: global, per-school override, deterministic % rollout (FR-PLT-022).

Evaluation (docs/16 §5.11): a school override wins; otherwise the global row, and with a
rollout percentage the school is in when ``bucket(key, tenant_id) < rollout_percent`` where the
bucket is a stable SHA-256 hash mod 100. Unknown flags are off.

``is_enabled`` is safe for tenant code: it reads ``platform.feature_flags`` (the one platform
table ``sos_app`` may SELECT) in any ``sos_app`` session, or opens a context-free one.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.core.db import context_free_session, platform_session
from app.core.errors import NotFound, PreconditionFailed
from app.core.ids import new_id
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import Actor, audit_platform, db_errors, now
from app.platform.schemas import FlagIn, FlagOut


def bucket(key: str, tenant_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"{key}:{tenant_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 100


def evaluate(rows: Iterable[Mapping[Any, Any]], key: str, tenant_id: uuid.UUID) -> bool:
    global_row: Mapping[Any, Any] | None = None
    for row in rows:
        if row["tenant_id"] == tenant_id:
            return bool(row["enabled"])
        if row["tenant_id"] is None:
            global_row = row
    if global_row is None or not global_row["enabled"]:
        return False
    percent = global_row["rollout_percent"]
    return percent is None or bucket(key, tenant_id) < int(percent)


def is_enabled(key: str, tenant_id: uuid.UUID, *, session: Session | None = None) -> bool:
    if session is not None:
        return evaluate(repo.flags_for_evaluation(session, key, tenant_id), key, tenant_id)
    with context_free_session() as s:
        return evaluate(repo.flags_for_evaluation(s, key, tenant_id), key, tenant_id)


def list_flags() -> list[FlagOut]:
    with platform_session() as s:
        return [FlagOut.model_validate(dict(r)) for r in repo.flag_rows(s)]


def _snapshot(row: Mapping[Any, Any] | None) -> dict[str, Any]:
    if row is None:
        return {"exists": False}
    return {"enabled": bool(row["enabled"]), "rollout_percent": row["rollout_percent"]}


def _check_version(row: Mapping[Any, Any] | None, expected_version: int | None) -> None:
    """The optional If-Match of a flag PUT (412 when stale, or when the flag does not exist yet;
    audit 2026-10-06 R-04: a stale form must not turn a switched-off flag back on)."""
    if expected_version is not None and (row is None or row["version"] != expected_version):
        raise PreconditionFailed()


def set_global(
    actor: Actor, key: str, data: FlagIn, *, expected_version: int | None = None
) -> FlagOut:
    with platform_session() as s, db_errors():
        row = repo.flag_row(s, key, None)
        _check_version(row, expected_version)
        before = _snapshot(row)
        values = {
            "enabled": data.enabled,
            "rollout_percent": data.rollout_percent,
            "description": data.description,
            "updated_by": actor.operator_id,
            "updated_at": now(),
        }
        if row is None:
            row = repo.insert_row(s, m.feature_flags, {"id": new_id(), "key": key, **values})
        else:
            row = repo.update_row(s, m.feature_flags, row["id"], values)
        audit_platform(
            s,
            actor,
            "flag.updated",
            "feature_flag",
            row["id"],
            {"key": key, "before": before, "after": _snapshot(row)},
        )
        return FlagOut.model_validate(dict(row))


def set_override(
    actor: Actor,
    key: str,
    tenant_id: uuid.UUID,
    enabled: bool,
    *,
    expected_version: int | None = None,
) -> FlagOut:
    with platform_session() as s, db_errors():
        if repo.get_by(s, m.deployments, m.deployments.c.tenant_id == tenant_id) is None:
            raise NotFound("School not found")
        row = repo.flag_row(s, key, tenant_id)
        _check_version(row, expected_version)
        before = _snapshot(row)
        values = {"enabled": enabled, "updated_by": actor.operator_id, "updated_at": now()}
        if row is None:
            row = repo.insert_row(
                s, m.feature_flags, {"id": new_id(), "key": key, "tenant_id": tenant_id, **values}
            )
        else:
            row = repo.update_row(s, m.feature_flags, row["id"], values)
        audit_platform(
            s,
            actor,
            "flag.override_set",
            "feature_flag",
            row["id"],
            {"key": key, "before": before, "after": _snapshot(row)},
            tenant_id=tenant_id,
        )
        return FlagOut.model_validate(dict(row))


def remove_override(actor: Actor, key: str, tenant_id: uuid.UUID) -> None:
    with platform_session() as s, db_errors():
        row = repo.flag_row(s, key, tenant_id)
        if row is None:
            raise NotFound("Override not found")
        repo.delete_row(s, m.feature_flags, row["id"])
        audit_platform(
            s,
            actor,
            "flag.override_removed",
            "feature_flag",
            row["id"],
            {"key": key, "before": _snapshot(row)},
            tenant_id=tenant_id,
        )
