"""Fleet: deployments registry, heartbeat keys and the heartbeat protocol (FR-PLT-023..025,
SEC-028; docs/16 §5.12, §12).

Heartbeat authentication (``require_fleet_signature``), in this order, storing nothing on any
failure:
1. deployment + key id known and the key currently valid (current key, or next key during a
   rotation; the old key stops working after the 7-day overlap) -> else 401;
2. ``|now - X-SOS-Timestamp| <= 300 s`` -> else 401;
3. body ≤ 16 KB -> else 422;
4. ``X-SOS-Signature == "v1=" + hex(HMAC-SHA256(key, timestamp + "." + raw_body))`` compared in
   constant time -> else 401;
5. strict schema (unknown fields rejected, no free text) -> else 422;
6. ``deployment_id`` / ``tenant_id`` in the body match the registry -> else 401;
7. nonce not seen in the last 10 minutes -> else 409 ``replay``;
8. at most one accepted heartbeat per deployment per minute -> else 429.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

import redis
from fastapi import Depends, Request
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.crypto import CryptoError, KeyWrapper, get_key_wrapper, unwrap_bound
from app.core.db import platform_session
from app.core.errors import (
    Conflict,
    NotFound,
    PreconditionFailed,
    RateLimited,
    Unauthenticated,
    ValidationFailed,
)
from app.core.logging import get_logger
from app.identity.service_token import InMemoryReplayStore, RedisReplayStore, ReplayStore
from app.platform import announcements, usage
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import (
    SYSTEM,
    Actor,
    audit_platform,
    clamp_limit,
    db_errors,
    fleet_cfg,
    must,
    now,
    parse_cursor,
    today_ist,
)
from app.platform.schemas import (
    DeploymentOut,
    DeploymentPatch,
    FleetVersionOut,
    HeartbeatIn,
    HeartbeatKeyOut,
    HeartbeatOut,
)
from app.platform.tenants import heartbeat_key_resource, new_heartbeat_key

log = get_logger(__name__)

HEADER_DEPLOYMENT = "X-SOS-Deployment"
HEADER_KEY_ID = "X-SOS-Key-Id"
HEADER_TIMESTAMP = "X-SOS-Timestamp"
HEADER_SIGNATURE = "X-SOS-Signature"
_REJECTED = "Heartbeat rejected"


# --- deployments ------------------------------------------------------------------------------


def list_deployments(
    status: str | None = None,
    *,
    tenant_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[DeploymentOut], str | None]:
    """Newest first, keyset-paged on the UUIDv7 id (audit 2026-10-06 R-14: the list was unpaged,
    one row per school). ``tenant_id`` narrows it to one school's deployment."""
    limit = clamp_limit(limit)
    conds = [m.deployments.c.status == status] if status else []
    if tenant_id is not None:
        conds.append(m.deployments.c.tenant_id == tenant_id)
    with platform_session() as s:
        rows = repo.list_rows(s, m.deployments, *conds, limit=limit, cursor=parse_cursor(cursor))
    items = [DeploymentOut.model_validate(dict(r)) for r in rows[:limit]]
    return items, (str(rows[limit - 1]["id"]) if len(rows) > limit else None)


def get_deployment(deployment_id: uuid.UUID) -> DeploymentOut:
    with platform_session() as s:
        row = repo.get(s, m.deployments, deployment_id)
    if row is None:
        raise NotFound("Deployment not found")
    return DeploymentOut.model_validate(dict(row))


def versions() -> list[FleetVersionOut]:
    with platform_session() as s:
        rows = s.execute(
            select(m.deployments.c.app_version, func.count())
            .where(m.deployments.c.status != "decommissioned")
            .group_by(m.deployments.c.app_version)
            .order_by(m.deployments.c.app_version)
        ).all()
    return [FleetVersionOut(version=v or "unknown", deployments=int(n)) for v, n in rows]


def update_deployment(
    actor: Actor, deployment_id: uuid.UUID, data: DeploymentPatch, *, expected_version: int | None
) -> DeploymentOut:
    values = data.model_dump(exclude_unset=True)
    with platform_session() as s, db_errors():
        row = repo.get(s, m.deployments, deployment_id, for_update=True)
        if row is None:
            raise NotFound("Deployment not found")
        if expected_version is not None and row["version"] != expected_version:
            raise PreconditionFailed()
        if row["mode"] == "shared" and ({"custom_domain", "host_ref"} & set(values)):
            raise Conflict("Shared-tier schools have no host or custom domain.", code="shared_tier")
        row = repo.update_row(s, m.deployments, deployment_id, values)
        audit_platform(
            s,
            actor,
            "deployment.updated",
            "deployment",
            deployment_id,
            {"fields": sorted(values)},
            tenant_id=row["tenant_id"],
        )
        return DeploymentOut.model_validate(dict(row))


def rotate_key(actor: Actor, deployment_id: uuid.UUID, *, wrapper: KeyWrapper) -> HeartbeatKeyOut:
    """New key valid alongside the current one for the overlap; returned ONCE (runbook -> SSM).

    409 ``rotation_pending`` while an earlier rotation is inside the overlap (R-15)."""
    with platform_session() as s, db_errors():
        row = repo.get(s, m.deployments, deployment_id, for_update=True)
        if row is None:
            raise NotFound("Deployment not found")
        if row["mode"] != "dedicated" or row["status"] == "decommissioned":
            raise Conflict(
                "Only live dedicated deployments have heartbeat keys.", code="invalid_state"
            )
        values: dict[str, Any] = {}
        if row["heartbeat_next_key_id"]:
            # Audit 2026-10-06 R-15: during the overlap the host may still sign with the
            # current key, so a second rotation (double click, retry; the key is shown once)
            # must not promote the pending key and drop it. After the overlap the pending key is
            # promoted first, as the staleness sweep would have done.
            overlap = dt.timedelta(days=int(fleet_cfg()["key_rotation_overlap_days"]))
            if now() - row["heartbeat_rotation_started_at"] <= overlap:
                raise Conflict(
                    "A key rotation is already in progress. The new key is valid now; the old "
                    "one stops working when the overlap ends.",
                    code="rotation_pending",
                )
            values = _promoted(row)
        key_id, wrapped, plaintext = new_heartbeat_key(row["tenant_id"], row["id"], wrapper)
        values.update(
            {
                "heartbeat_next_key_id": key_id,
                "heartbeat_next_key_ciphertext": wrapped,
                "heartbeat_rotation_started_at": now(),
            }
        )
        repo.update_row(s, m.deployments, deployment_id, values)
        audit_platform(
            s,
            actor,
            "deployment.heartbeat_key_rotated",
            "deployment",
            deployment_id,
            {"heartbeat_key_id": key_id},
            tenant_id=row["tenant_id"],
        )
    return HeartbeatKeyOut(
        deployment_id=deployment_id, heartbeat_key_id=key_id, heartbeat_key=plaintext
    )


def decommission(actor: Actor, deployment_id: uuid.UUID) -> DeploymentOut:
    with platform_session() as s, db_errors():
        row = repo.get(s, m.deployments, deployment_id, for_update=True)
        if row is None:
            raise NotFound("Deployment not found")
        if row["tenant_status"] not in ("offboarding", "deleted"):
            raise Conflict("Decommission follows an approved offboarding.", code="invalid_state")
        row = repo.update_row(
            s,
            m.deployments,
            deployment_id,
            {
                "status": "decommissioned",
                "heartbeat_key_id": None,
                "heartbeat_key_ciphertext": None,
                "heartbeat_next_key_id": None,
                "heartbeat_next_key_ciphertext": None,
                "heartbeat_rotation_started_at": None,
            },
        )
        audit_platform(
            s,
            actor,
            "deployment.decommissioned",
            "deployment",
            deployment_id,
            {},
            tenant_id=row["tenant_id"],
        )
        return DeploymentOut.model_validate(dict(row))


def _promoted(row: Mapping[Any, Any]) -> dict[str, Any]:
    return {
        "heartbeat_key_id": row["heartbeat_next_key_id"],
        "heartbeat_key_ciphertext": row["heartbeat_next_key_ciphertext"],
        "heartbeat_next_key_id": None,
        "heartbeat_next_key_ciphertext": None,
        "heartbeat_rotation_started_at": None,
    }


# --- heartbeat verification -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FleetStores:
    nonces: ReplayStore
    rate: ReplayStore


@lru_cache(maxsize=1)
def get_fleet_stores() -> FleetStores:
    settings = get_settings()
    if settings.is_production_like:
        client = redis.Redis.from_url(
            settings.redis_url.get_secret_value(), socket_timeout=0.5, socket_connect_timeout=0.5
        )
        return FleetStores(
            RedisReplayStore(client, prefix="sos:hb-nonce:"),
            RedisReplayStore(client, prefix="sos:hb-rate:"),
        )
    return FleetStores(InMemoryReplayStore(), InMemoryReplayStore())


@lru_cache(maxsize=1)
def get_fleet_key_wrapper() -> KeyWrapper:
    return get_key_wrapper(get_settings())


@dataclass(frozen=True, slots=True)
class VerifiedHeartbeat:
    deployment: Mapping[Any, Any]
    payload: HeartbeatIn


def sign(key: bytes, timestamp: str, body: bytes) -> str:
    return (
        "v1=" + hmac.new(key, timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    )


def _reject(reason: str) -> Unauthenticated:
    log.warning("fleet.heartbeat.rejected", error_code=reason, outcome="rejected")
    return Unauthenticated(_REJECTED)


def _valid_keys(dep: Mapping[Any, Any], at: dt.datetime) -> dict[str, bytes]:
    keys: dict[str, bytes] = {}
    overlap = dt.timedelta(days=int(fleet_cfg()["key_rotation_overlap_days"]))
    started = dep["heartbeat_rotation_started_at"]
    if dep["heartbeat_key_id"] and (started is None or at - started <= overlap):
        keys[dep["heartbeat_key_id"]] = bytes(dep["heartbeat_key_ciphertext"])
    if dep["heartbeat_next_key_id"]:
        keys[dep["heartbeat_next_key_id"]] = bytes(dep["heartbeat_next_key_ciphertext"])
    return keys


def verify_heartbeat(
    headers: Mapping[str, str],
    body: bytes,
    *,
    wrapper: KeyWrapper,
    stores: FleetStores,
    at: dt.datetime | None = None,
) -> VerifiedHeartbeat:
    cfg = fleet_cfg()
    current = at or now()
    try:
        deployment_id = uuid.UUID(headers.get(HEADER_DEPLOYMENT, ""))
    except ValueError:
        raise _reject("bad_deployment_header") from None
    key_id = headers.get(HEADER_KEY_ID, "")
    timestamp = headers.get(HEADER_TIMESTAMP, "")
    signature = headers.get(HEADER_SIGNATURE, "")
    with platform_session() as s:
        dep = repo.get(s, m.deployments, deployment_id)
    if dep is None or dep["mode"] != "dedicated" or dep["status"] == "decommissioned":
        raise _reject("unknown_deployment")
    wrapped = _valid_keys(dep, current).get(key_id)
    if wrapped is None:
        raise _reject("unknown_key")
    if not timestamp.isdigit() or len(timestamp) > 12:
        raise _reject("bad_timestamp")
    if abs(current.timestamp() - int(timestamp)) > int(cfg["skew_seconds"]):
        raise _reject("clock_skew")
    if len(body) > int(cfg["max_heartbeat_bytes"]):
        raise ValidationFailed(
            [{"field": "body", "code": "too_large", "message_key": "errors.too_large"}]
        )
    try:
        key = unwrap_bound(
            wrapper,
            wrapped,
            tenant_id=dep["tenant_id"],
            resource=heartbeat_key_resource(dep["id"]),
        )
    except CryptoError:
        raise _reject("key_unwrap_failed") from None
    if not hmac.compare_digest(sign(key, timestamp, body), signature):
        raise _reject("bad_signature")
    try:
        payload = HeartbeatIn.model_validate_json(body)
    except ValidationError as exc:
        log.warning("fleet.heartbeat.rejected", error_code="schema", outcome="rejected")
        raise ValidationFailed(
            [
                {
                    "field": ".".join(str(p) for p in err["loc"]) or "body",
                    "code": err["type"],
                    "message_key": f"errors.{err['type']}",
                }
                for err in exc.errors()
            ]
        ) from None
    if payload.deployment_id != dep["id"] or payload.tenant_id != dep["tenant_id"]:
        raise _reject("id_mismatch")
    if not stores.nonces.claim(
        str(payload.nonce), dt.timedelta(seconds=int(cfg["nonce_ttl_seconds"]))
    ):
        log.warning("fleet.heartbeat.rejected", error_code="replay", outcome="rejected")
        raise Conflict("This heartbeat was already received.", code="replay")
    if not stores.rate.claim(str(dep["id"]), dt.timedelta(seconds=int(cfg["rate_limit_seconds"]))):
        raise RateLimited(
            "One heartbeat per minute per deployment.",
            retry_after_s=int(cfg["rate_limit_seconds"]),
        )
    return VerifiedHeartbeat(dep, payload)


class RequireFleetSignature:
    """Dependency for ``POST /api/v1/fleet/heartbeat`` (machine auth; no user token)."""

    # Machine authentication, not a catalog permission: the route-enumeration test accepts
    # exactly this guard on POST /api/v1/fleet/heartbeat (CLAUDE.md §6.2).
    sos_permission = "fleet.heartbeat"
    sos_fleet_signature = True
    sos_step_up = False
    sos_scope = None

    async def __call__(
        self,
        request: Request,
        wrapper: Annotated[KeyWrapper, Depends(get_fleet_key_wrapper)],
        stores: Annotated[FleetStores, Depends(get_fleet_stores)],
    ) -> VerifiedHeartbeat:
        body = await request.body()
        return verify_heartbeat(request.headers, body, wrapper=wrapper, stores=stores)


def require_fleet_signature() -> RequireFleetSignature:
    return RequireFleetSignature()


def _status_for(payload: HeartbeatIn, at: dt.datetime) -> str:  # noqa: PLR0911
    rules = fleet_cfg()["degraded_rules"]
    health = payload.health.model_dump()
    if any(v != "ok" for v in health.values()):
        return "degraded"
    backup = payload.backup
    if backup is not None:
        if backup.status == "failing":
            return "degraded"
        if (
            backup.last_base_backup_at is not None
            and at - backup.last_base_backup_at
            > dt.timedelta(hours=int(rules["backup_max_age_hours"]))
        ):
            return "degraded"
        if backup.wal_archive_lag_s is not None and backup.wal_archive_lag_s > int(
            rules["wal_lag_max_seconds"]
        ):
            return "degraded"
    if payload.host is not None and payload.host.disk_used_pct > float(rules["disk_used_max_pct"]):
        return "degraded"
    tls = payload.tls
    if (
        tls is not None
        and tls.cert_expires_at is not None
        and tls.cert_expires_at - at < dt.timedelta(days=int(rules["cert_min_days"]))
    ):
        return "degraded"
    return "healthy"


class UsageDateInFuture(ValidationFailed):
    """A heartbeat's usage day is after today in IST (AA-09)."""

    code = "usage_date_in_future"


def _usage_to_record(verified: VerifiedHeartbeat, current: dt.datetime) -> bool:
    """Audit AA-09 (owner decision 2026-10-04): a host reports usage only for today and yesterday
    (IST). An older day is ignored and logged with IDs only (it cannot rewrite days already
    counted or invoiced); a future day is refused (422 ``usage_date_in_future``)."""
    usage_in = verified.payload.usage
    if usage_in is None:
        return False
    today = today_ist(current)
    if usage_in.date > today:
        raise UsageDateInFuture(
            [{"field": "usage.date", "code": "in_future", "message_key": "errors.invalid"}]
        )
    if usage_in.date < today - dt.timedelta(days=1):
        log.warning(
            "fleet.heartbeat.usage_too_old",
            deployment_id=str(verified.deployment["id"]),
            tenant_id=str(verified.deployment["tenant_id"]),
            outcome="ignored",
        )
        return False
    return True


def accept_heartbeat(verified: VerifiedHeartbeat, *, at: dt.datetime | None = None) -> HeartbeatOut:
    current = at or now()
    payload = verified.payload
    record_usage = _usage_to_record(verified, current)
    status = _status_for(payload, current)
    with platform_session() as s, db_errors():
        dep = repo.get(s, m.deployments, verified.deployment["id"], for_update=True)
        dep = must(dep)
        values: dict[str, Any] = {
            "last_heartbeat_at": current,
            "last_heartbeat": json.loads(payload.model_dump_json()),
            "app_version": payload.app_version,
            "status": status,
        }
        repo.update_row(s, m.deployments, dep["id"], values, bump_version=False)
        if dep["last_heartbeat_at"] is None:
            audit_platform(
                s,
                SYSTEM,
                "deployment.first_heartbeat",
                "deployment",
                dep["id"],
                {"app_version": payload.app_version},
                tenant_id=dep["tenant_id"],
            )
        if dep["status"] != status:
            audit_platform(
                s,
                SYSTEM,
                "deployment.status_changed",
                "deployment",
                dep["id"],
                {"from": dep["status"], "to": status},
                tenant_id=dep["tenant_id"],
            )
        if record_usage and payload.usage is not None:
            usage.ingest_heartbeat(s, dep["tenant_id"], payload.usage)
        target = dep["target_version"]
    return HeartbeatOut(
        received_at=current,
        target_version=target,
        min_supported_version=str(fleet_cfg()["min_supported_version"]),
        announcements=announcements.for_deployment(dep["tenant_id"], "dedicated", at=current),
    )


def check_staleness(*, at: dt.datetime | None = None) -> int:
    """Every 5 minutes: dedicated hosts without a valid heartbeat for 20 minutes -> unreachable;
    rotations older than the overlap are completed (next key becomes the only key)."""
    current = at or now()
    cutoff = current - dt.timedelta(minutes=int(fleet_cfg()["unreachable_after_minutes"]))
    overlap = dt.timedelta(days=int(fleet_cfg()["key_rotation_overlap_days"]))
    changed = 0
    with platform_session() as s:
        rows = list(
            s.execute(
                select(m.deployments)
                .where(
                    m.deployments.c.mode == "dedicated", m.deployments.c.status != "decommissioned"
                )
                .with_for_update(skip_locked=True)
            ).mappings()
        )
        for dep in rows:
            if (
                dep["heartbeat_rotation_started_at"] is not None
                and current - dep["heartbeat_rotation_started_at"] > overlap
            ):
                repo.update_row(s, m.deployments, dep["id"], _promoted(dep), bump_version=False)
            stale = dep["last_heartbeat_at"] is not None and dep["last_heartbeat_at"] < cutoff
            if stale and dep["status"] in ("healthy", "degraded"):
                repo.update_row(
                    s, m.deployments, dep["id"], {"status": "unreachable"}, bump_version=False
                )
                audit_platform(
                    s,
                    SYSTEM,
                    "deployment.status_changed",
                    "deployment",
                    dep["id"],
                    {"from": dep["status"], "to": "unreachable"},
                    tenant_id=dep["tenant_id"],
                )
                log.warning(
                    "fleet.heartbeat.missed",
                    tenant_id=str(dep["tenant_id"]),
                    resource_id=str(dep["id"]),
                    outcome="unreachable",
                )
                changed += 1
    return changed
