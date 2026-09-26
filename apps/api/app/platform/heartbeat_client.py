"""Heartbeat CLIENT for dedicated hosts (FR-PLT-024, SEC-028; docs/16 §12.1-12.4).

Outbound only: every 5 minutes the host's beat posts versions, health and aggregate counts
(never personal data) to ``SOS_CONTROL_PLANE_URL``, signed with its per-deployment key
(``SOS_HEARTBEAT_KEY_ID`` / ``SOS_HEARTBEAT_KEY``, from SSM). The response's announcements are
cached locally for the school app. The response never carries commands.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.health import get_checks
from app.core.logging import get_logger
from app.platform import announcements, usage
from app.platform.common import today_ist
from app.platform.fleet import (
    HEADER_DEPLOYMENT,
    HEADER_KEY_ID,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    sign,
)
from app.platform.schemas import HeartbeatIn, HeartbeatOut

log = get_logger(__name__)
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class HeartbeatNotConfigured(RuntimeError):
    pass


def decode_key(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded)


def build_payload(
    settings: Settings,
    *,
    checks: Mapping[str, Callable[[], bool]] | None = None,
    usage_counts: Mapping[str, Any] | None = None,
    at: dt.datetime | None = None,
) -> HeartbeatIn:
    if not (settings.deployment_id and settings.dedicated_tenant_id):
        raise HeartbeatNotConfigured("SOS_DEPLOYMENT_ID and SOS_DEDICATED_TENANT_ID are required")
    results = {name: ("ok" if fn() else "down") for name, fn in (checks or get_checks()).items()}
    health = {
        "api": "ok",
        "worker": "ok",
        "beat": "ok",
        "db": results.get("database", "unknown"),
        "valkey": results.get("redis", "unknown"),
        "s3": "unknown",
    }
    body: dict[str, Any] = {
        "schema_version": 1,
        "deployment_id": settings.deployment_id,
        "tenant_id": settings.dedicated_tenant_id,
        "sent_at": (at or dt.datetime.now(dt.UTC)).isoformat(),
        "nonce": str(uuid.uuid4()),
        "app_version": settings.version,
        "health": health,
    }
    if usage_counts is not None:
        body["usage"] = {
            "date": str(usage_counts["usage_date"]),
            **{
                k: usage_counts[k]
                for k in (
                    "active_users",
                    "staff_users",
                    "students_active",
                    "storage_bytes",
                    "documents",
                    "ai_queries",
                    "ai_input_tokens",
                    "ai_output_tokens",
                )
            },
            "ai_cost_usd": str(usage_counts["ai_cost_usd"]),
        }
    return HeartbeatIn.model_validate(body)


def signed_request(
    settings: Settings, payload: HeartbeatIn, *, now: float | None = None
) -> tuple[dict[str, str], bytes]:
    if settings.heartbeat_key is None or settings.heartbeat_key_id is None:
        raise HeartbeatNotConfigured("SOS_HEARTBEAT_KEY_ID and SOS_HEARTBEAT_KEY are required")
    body = json.dumps(
        payload.model_dump(mode="json", exclude_none=True), separators=(",", ":")
    ).encode()
    timestamp = str(int(now if now is not None else time.time()))
    key = decode_key(settings.heartbeat_key.get_secret_value())
    headers = {
        "Content-Type": "application/json",
        HEADER_DEPLOYMENT: str(payload.deployment_id),
        HEADER_KEY_ID: settings.heartbeat_key_id,
        HEADER_TIMESTAMP: timestamp,
        HEADER_SIGNATURE: sign(key, timestamp, body),
    }
    return headers, body


def send(
    settings: Settings | None = None,
    *,
    client: httpx.Client | None = None,
    include_usage: bool = True,
) -> dict[str, Any]:
    settings = settings or get_settings()
    if not settings.control_plane_url:
        raise HeartbeatNotConfigured("SOS_CONTROL_PLANE_URL is required on dedicated hosts")
    counts = None
    if include_usage and settings.dedicated_tenant_id:
        try:
            counts = usage.snapshot(
                uuid.UUID(settings.dedicated_tenant_id), today_ist() - dt.timedelta(days=1)
            )
        except Exception:
            log.warning("fleet.heartbeat.usage_unavailable", outcome="skipped")
    payload = build_payload(settings, usage_counts=counts)
    headers, body = signed_request(settings, payload)
    url = settings.control_plane_url.rstrip("/") + "/api/v1/fleet/heartbeat"
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT, follow_redirects=False)
    try:
        response = http.post(url, content=body, headers=headers)
    finally:
        if owned:
            http.close()
    if response.status_code != 200:
        log.warning("fleet.heartbeat.send_failed", status=response.status_code, outcome="rejected")
        return {"status": response.status_code}
    out = HeartbeatOut.model_validate(response.json())
    announcements.cache_from_heartbeat(out.announcements)
    log.info("fleet.heartbeat.sent", status=200, count=len(out.announcements), outcome="ok")
    return {"status": 200, "announcements": len(out.announcements)}
