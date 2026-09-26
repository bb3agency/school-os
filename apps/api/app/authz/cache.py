"""Permission snapshot cache (docs/04 §10; FR-IAM-014: changes effective within 60 s).

A snapshot holds a membership's role keys, effective permissions, which of them are only
scoped, its scopes and whether MFA is required. It is cached for :data:`SNAPSHOT_TTL_S` seconds
under a key that includes the tenant id and a per-tenant generation:

    sos:authz:gen:<tenant_id>                                  -> generation counter
    sos:authz:snap:<tenant_id>:<generation>:<membership_id>    -> snapshot JSON

Role, scope and membership changes call :func:`invalidate_on_commit`, which deletes the
membership's snapshot (or bumps the tenant generation) **after** the transaction commits, so a
concurrent request cannot re-cache the old state from before the commit for longer than the TTL.
The cache is an optimisation only: when the store is unavailable the resolver reads the
database every time, and a failed invalidation is bounded by the 60 s TTL.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.authz.context import Scopes
from app.authz.kv import KVUnavailable, kv_store
from app.core.logging import get_logger

SNAPSHOT_TTL_S = 60
_GEN_TTL_S = 7 * 24 * 3600
_PENDING_KEY = "sos_authz_invalidate"
log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PermissionSnapshot:
    roles: frozenset[str]
    permissions: frozenset[str]
    scoped_permissions: frozenset[str]
    scopes: Scopes
    mfa_required: bool

    def to_json(self) -> bytes:
        return json.dumps(
            {
                "roles": sorted(self.roles),
                "permissions": sorted(self.permissions),
                "scoped": sorted(self.scoped_permissions),
                "school": self.scopes.school,
                "classes": sorted(str(c) for c in self.scopes.class_ids),
                "sections": sorted(str(s) for s in self.scopes.section_ids),
                "mfa": self.mfa_required,
            },
            separators=(",", ":"),
        ).encode()

    @classmethod
    def from_json(cls, raw: bytes) -> PermissionSnapshot:
        data: dict[str, Any] = json.loads(raw)
        return cls(
            roles=frozenset(data["roles"]),
            permissions=frozenset(data["permissions"]),
            scoped_permissions=frozenset(data["scoped"]),
            scopes=Scopes(
                school=bool(data["school"]),
                class_ids=frozenset(uuid.UUID(c) for c in data["classes"]),
                section_ids=frozenset(uuid.UUID(s) for s in data["sections"]),
            ),
            mfa_required=bool(data["mfa"]),
        )


def _gen_key(tenant_id: uuid.UUID) -> str:
    return f"sos:authz:gen:{tenant_id}"


def _generation(tenant_id: uuid.UUID) -> str:
    raw = kv_store().get(_gen_key(tenant_id))
    return raw.decode() if raw else "0"


def _snap_key(tenant_id: uuid.UUID, generation: str, membership_id: uuid.UUID) -> str:
    return f"sos:authz:snap:{tenant_id}:{generation}:{membership_id}"


def get_snapshot(tenant_id: uuid.UUID, membership_id: uuid.UUID) -> PermissionSnapshot | None:
    try:
        raw = kv_store().get(_snap_key(tenant_id, _generation(tenant_id), membership_id))
    except KVUnavailable:
        log.warning("authz.cache_unavailable", action="get")
        return None
    if raw is None:
        return None
    try:
        return PermissionSnapshot.from_json(raw)
    except (ValueError, KeyError, TypeError):
        return None


def put_snapshot(
    tenant_id: uuid.UUID, membership_id: uuid.UUID, snapshot: PermissionSnapshot
) -> None:
    try:
        key = _snap_key(tenant_id, _generation(tenant_id), membership_id)
        kv_store().set(key, snapshot.to_json(), ttl_s=SNAPSHOT_TTL_S)
    except KVUnavailable:
        log.warning("authz.cache_unavailable", action="put")


def invalidate_membership(tenant_id: uuid.UUID, membership_id: uuid.UUID) -> None:
    try:
        kv_store().delete(_snap_key(tenant_id, _generation(tenant_id), membership_id))
    except KVUnavailable:
        log.warning("authz.cache_unavailable", action="invalidate")


def invalidate_tenant(tenant_id: uuid.UUID) -> None:
    """Drop every snapshot of the tenant (role permission changes affect many members)."""
    try:
        kv_store().incr(_gen_key(tenant_id), ttl_s=_GEN_TTL_S)
    except KVUnavailable:
        log.warning("authz.cache_unavailable", action="invalidate")


def _flush(session: Session) -> None:
    pending: set[tuple[uuid.UUID, uuid.UUID | None]] = session.info.pop(_PENDING_KEY, set())
    for tenant_id, membership_id in pending:
        if membership_id is None:
            invalidate_tenant(tenant_id)
        else:
            invalidate_membership(tenant_id, membership_id)


def _discard(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


def invalidate_on_commit(
    session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID | None = None
) -> None:
    """Invalidate after ``session`` commits (``membership_id=None``: the whole tenant)."""
    pending: set[tuple[uuid.UUID, uuid.UUID | None]] | None = session.info.get(_PENDING_KEY)
    if pending is None:
        pending = set()
        session.info[_PENDING_KEY] = pending
        event.listen(session, "after_commit", _flush, once=True)
        event.listen(session, "after_rollback", _discard, once=True)
    pending.add((tenant_id, membership_id))
