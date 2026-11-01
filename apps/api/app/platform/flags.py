"""Feature flags: global, per-school override, deterministic % rollout (FR-PLT-022).

Evaluation (docs/16 §5.11): a school override wins; otherwise the global row, and with a
rollout percentage the school is in when ``bucket(key, tenant_id) < rollout_percent`` where the
bucket is a stable SHA-256 hash mod 100. Unknown flags are off.

``is_enabled`` is safe for tenant code: it reads ``platform.feature_flags`` (the one platform
table ``sos_app`` may SELECT) in any ``sos_app`` session, or opens a context-free one.

``set_school_override`` / ``clear_school_override`` are the public (``platform.service``) path
for other modules and dev tooling: a per-school override for any school the database knows,
shared-tier schools without a ``platform.deployments`` row included, audited on the platform
chain as ``flag.override_set`` / ``flag.override_removed`` (actor ``system``).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.core.db import context_free_session, platform_session
from app.core.errors import NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import (
    SYSTEM,
    Actor,
    audit_platform,
    db_errors,
    if_match_required,
    now,
)
from app.platform.schemas import FlagIn, FlagOut
from app.tenancy import service as tenancy

# The same rule as ``schemas.FlagKey`` and the CHECK on ``platform.feature_flags.key``.
_KEY_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
_KEY_MAX = 100


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
    """The If-Match of a flag PUT (audit 2026-10-06 R-04: a stale form must not turn a
    switched-off flag back on; 2026-10-04 AA-13). Required once the flag exists (400
    ``if_match_required``); 412 when stale, or when it is sent for a flag that does not exist
    yet. A new flag is created without it: there is nothing to overwrite."""
    if expected_version is None:
        if row is not None:
            raise if_match_required()
        return
    if row is None or row["version"] != expected_version:
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


def _write_override(
    s: Session,
    actor: Actor,
    key: str,
    tenant_id: uuid.UUID,
    enabled: bool,
    *,
    expected_version: int | None,
    source: str | None = None,
) -> tuple[Mapping[Any, Any], bool]:
    """Insert or update one school's override; returns (row, changed). Audited when changed.

    Operators (``source`` None) send the If-Match of the form they read. The public path
    (``source`` set) sets an absolute value, so it needs no version and a no-op writes nothing."""
    row = repo.flag_row(s, key, tenant_id)
    if source is None:
        _check_version(row, expected_version)
    elif row is not None and bool(row["enabled"]) is enabled:
        return row, False
    before = _snapshot(row)
    values = {"enabled": enabled, "updated_by": actor.operator_id, "updated_at": now()}
    if row is None:
        row = repo.insert_row(
            s, m.feature_flags, {"id": new_id(), "key": key, "tenant_id": tenant_id, **values}
        )
    else:
        row = repo.update_row(s, m.feature_flags, row["id"], values)
    summary: dict[str, Any] = {"key": key, "before": before, "after": _snapshot(row)}
    if source is not None:
        summary["source"] = source
    audit_platform(
        s, actor, "flag.override_set", "feature_flag", row["id"], summary, tenant_id=tenant_id
    )
    return row, True


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
        row, _ = _write_override(
            s, actor, key, tenant_id, enabled, expected_version=expected_version
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


# --- public path for other modules (platform.service) -------------------------------------------


def _valid_key(key: str) -> str:
    if len(key) > _KEY_MAX or not _KEY_RE.match(key):
        raise ValidationFailed(
            [{"field": "key", "code": "invalid_flag_key", "message_key": "errors.invalid"}]
        )
    return key


def _known_school(s: Session, tenant_id: uuid.UUID) -> None:
    """Any school the database knows (every status), with or without a deployments row: a
    shared-tier school seeded outside provisioning has none (counts only, no tenant data)."""
    if repo.get_by(s, m.deployments, m.deployments.c.tenant_id == tenant_id) is not None:
        return
    if tenant_id not in tenancy.list_tenant_ids(s, None):
        raise NotFound("School not found")


def set_school_override(
    key: str,
    tenant_id: uuid.UUID,
    enabled: bool,
    *,
    source: str,
    request_id: str | None = None,
) -> bool:
    """Turn ``key`` on or off for one school (system actor). Returns whether anything changed:
    setting the value a school already has writes and audits nothing. 422 ``invalid_flag_key``
    for a malformed key, 404 for a school the database does not know. ``source`` names the
    caller in the audit summary (a code, never personal data)."""
    _valid_key(key)
    actor = Actor(None, request_id) if request_id else SYSTEM
    with platform_session() as s, db_errors():
        _known_school(s, tenant_id)
        _, changed = _write_override(
            s, actor, key, tenant_id, enabled, expected_version=None, source=source
        )
        return changed


def clear_school_override(
    key: str, tenant_id: uuid.UUID, *, source: str, request_id: str | None = None
) -> bool:
    """Remove one school's override of ``key`` (it then follows the global flag). Returns
    whether there was one; same validation as :func:`set_school_override`."""
    _valid_key(key)
    actor = Actor(None, request_id) if request_id else SYSTEM
    with platform_session() as s, db_errors():
        _known_school(s, tenant_id)
        row = repo.flag_row(s, key, tenant_id)
        if row is None:
            return False
        repo.delete_row(s, m.feature_flags, row["id"])
        audit_platform(
            s,
            actor,
            "flag.override_removed",
            "feature_flag",
            row["id"],
            {"key": key, "before": _snapshot(row), "source": source},
            tenant_id=tenant_id,
        )
        return True
