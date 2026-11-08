"""Heartbeat CLIENT for dedicated hosts (FR-PLT-024, SEC-028; docs/16 §12.1-12.4).

Outbound only: every 5 minutes the host's beat posts versions, health and aggregate counts
(never personal data) to ``SOS_CONTROL_PLANE_URL``, signed with its per-deployment key
(``SOS_HEARTBEAT_KEY_ID`` / ``SOS_HEARTBEAT_KEY``, from SSM).

The answer is accepted only when it is signed with the same key (ADR-0058, W3-05): the key id
is the host's, the timestamp within the fleet skew, ``X-SOS-Signature`` the HMAC-SHA256 of
``timestamp + "." + body`` (constant-time compare), the body a valid ``HeartbeatOut`` and its
``request_nonce`` the nonce just sent. Otherwise nothing is applied and the last good state
stays (``fleet.heartbeat.answer_rejected`` with a reason code; no key, signature or body is
logged). A verified answer refreshes the announcements and is applied by
:mod:`app.platform.heartbeat_apply` (flags, AI answer allowance, billing summary). The answer
never carries commands.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.health import get_checks
from app.core.logging import get_logger
from app.platform import announcements, heartbeat_apply, invoice_storage, usage
from app.platform.common import fleet_cfg, today_ist
from app.platform.fleet import (
    ANSWER,
    HEADER_DEPLOYMENT,
    HEADER_KEY_ID,
    HEADER_SIGNATURE,
    HEADER_SIGNATURE_V2,
    HEADER_TIMESTAMP,
    REQUEST,
    signature_headers,
    signature_matches,
)
from app.platform.schemas import HeartbeatIn, HeartbeatOut

log = get_logger(__name__)
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class HeartbeatNotConfigured(RuntimeError):
    pass


class AnswerRejected(Exception):
    """The control plane's answer failed verification; ``reason`` is a code, never content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def decode_key(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded)


# The host state mount (deploy/dedicated/compose.yaml): backup.sh writes backup.json there.
STATE_DIR_ENV = "SOS_HOST_STATE_DIR"
BACKUP_STATUS = {"ok": "ok", "failed": "failing"}


def check_s3(settings: Settings | None = None) -> bool:
    """The host's files bucket answers a HEAD (FR-PLT-025): reachable and permitted."""
    settings = settings or get_settings()
    try:
        client = invoice_storage.s3_client(settings, settings.s3_endpoint_url)
        client.head_bucket(Bucket=settings.s3_bucket_files)
        return True
    except Exception:
        return False


def host_checks() -> dict[str, Callable[[], bool]]:
    return {**get_checks(), "s3": check_s3}


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.tzinfo is not None else None


def backup_state(state_dir: Path | None) -> dict[str, Any]:
    """``backup.json`` of ``backup.sh`` mapped to ``HbBackup`` (NFR-FLT-001). A missing or
    unreadable file is reported as ``status: unknown`` with no backup time, never omitted, so
    the control plane does not take silence for a good backup."""
    if state_dir is None:
        log.warning("fleet.heartbeat.backup_state_missing", error_code="no_state_dir")
        return {"status": "unknown"}
    try:
        raw = json.loads((state_dir / "backup.json").read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("backup.json is not an object")
    except (OSError, ValueError) as exc:
        log.warning("fleet.heartbeat.backup_state_missing", error_code=type(exc).__name__)
        return {"status": "unknown"}
    out: dict[str, Any] = {"status": BACKUP_STATUS.get(str(raw.get("status")), "unknown")}
    for field in ("last_pg_dump_at", "last_base_backup_at"):
        value = _timestamp(raw.get(field))
        if value is not None:
            out[field] = value
    return out


def _state_dir() -> Path | None:
    value = os.environ.get(STATE_DIR_ENV)
    return Path(value) if value else None


def build_payload(
    settings: Settings,
    *,
    checks: Mapping[str, Callable[[], bool]] | None = None,
    usage_counts: Mapping[str, Any] | None = None,
    at: dt.datetime | None = None,
    state_dir: Path | None = None,
) -> HeartbeatIn:
    if not (settings.deployment_id and settings.dedicated_tenant_id):
        raise HeartbeatNotConfigured("SOS_DEPLOYMENT_ID and SOS_DEDICATED_TENANT_ID are required")
    results = {name: ("ok" if fn() else "down") for name, fn in (checks or host_checks()).items()}
    health = {
        "api": "ok",
        "worker": "ok",
        "beat": "ok",
        "db": results.get("database", "unknown"),
        "valkey": results.get("redis", "unknown"),
        "s3": results.get("s3", "unknown"),
    }
    body: dict[str, Any] = {
        "schema_version": 1,
        "deployment_id": settings.deployment_id,
        "tenant_id": settings.dedicated_tenant_id,
        "sent_at": (at or dt.datetime.now(dt.UTC)).isoformat(),
        "nonce": str(uuid.uuid4()),
        "app_version": settings.version,
        "health": health,
        "backup": backup_state(state_dir if state_dir is not None else _state_dir()),
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
                    "ai_answers",
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
        **signature_headers(key, REQUEST, timestamp, body),
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
    return handle_answer(settings, response.headers, response.content, request_nonce=payload.nonce)


def verify_answer(
    settings: Settings,
    headers: Mapping[str, str],
    body: bytes,
    *,
    request_nonce: uuid.UUID,
    now: float | None = None,
) -> HeartbeatOut:
    """The control plane's answer, verified (ADR-0058); :class:`AnswerRejected` otherwise."""
    if settings.heartbeat_key is None or settings.heartbeat_key_id is None:
        raise HeartbeatNotConfigured("SOS_HEARTBEAT_KEY_ID and SOS_HEARTBEAT_KEY are required")
    lower = {str(k).lower(): str(v) for k, v in headers.items()}  # HTTP names: any case
    signature = lower.get(HEADER_SIGNATURE.lower(), "")
    signature_v2 = lower.get(HEADER_SIGNATURE_V2.lower(), "")
    timestamp = lower.get(HEADER_TIMESTAMP.lower(), "")
    if not signature and not signature_v2:
        raise AnswerRejected("unsigned")
    if lower.get(HEADER_KEY_ID.lower(), "") != settings.heartbeat_key_id:
        raise AnswerRejected("unknown_key")
    if not timestamp.isdigit() or len(timestamp) > 12:
        raise AnswerRejected("bad_timestamp")
    current = now if now is not None else time.time()
    if abs(current - int(timestamp)) > int(fleet_cfg()["skew_seconds"]):
        raise AnswerRejected("stale")
    key = decode_key(settings.heartbeat_key.get_secret_value())
    # CP-03: the ``ans``-labelled v2 when sent; v1 only while the rollout setting allows it.
    if not signature_matches(
        key,
        ANSWER,
        timestamp,
        body,
        v1=signature,
        v2=signature_v2,
        accept_v1=settings.heartbeat_accept_v1_signature,
    ):
        raise AnswerRejected("bad_signature")
    try:
        answer = HeartbeatOut.model_validate_json(body)
    except ValidationError:
        raise AnswerRejected("schema") from None
    if answer.request_nonce != request_nonce:
        raise AnswerRejected("nonce_mismatch")
    return answer


def handle_answer(
    settings: Settings,
    headers: Mapping[str, str],
    body: bytes,
    *,
    request_nonce: uuid.UUID,
    now: float | None = None,
    store: heartbeat_apply.BillingSummaryStore | None = None,
    cache: announcements.AnnouncementCache | None = None,
) -> dict[str, Any]:
    """Verify the answer, then refresh announcements and apply it; on refusal keep the last
    good state. With ``SOS_HEARTBEAT_ANSWER_UNSIGNED_OK`` (one release only) an UNSIGNED answer
    still refreshes the announcements and applies nothing else; a wrong signature never."""
    try:
        answer = verify_answer(settings, headers, body, request_nonce=request_nonce, now=now)
    except AnswerRejected as exc:
        if exc.reason == "unsigned" and settings.heartbeat_answer_unsigned_ok:
            try:
                legacy = HeartbeatOut.model_validate_json(body)
            except ValidationError:
                log.warning(
                    "fleet.heartbeat.answer_rejected", error_code="schema", outcome="rejected"
                )
                return {"status": 200, "answer": "rejected", "reason": "schema"}
            announcements.cache_from_heartbeat(legacy.announcements, cache)
            log.warning(
                "fleet.heartbeat.answer_unsigned",
                count=len(legacy.announcements),
                outcome="announcements_only",
            )
            return {"status": 200, "answer": "unsigned", "announcements": len(legacy.announcements)}
        log.warning("fleet.heartbeat.answer_rejected", error_code=exc.reason, outcome="rejected")
        return {"status": 200, "answer": "rejected", "reason": exc.reason}
    announcements.cache_from_heartbeat(answer.announcements, cache)
    tenant_id = uuid.UUID(str(settings.dedicated_tenant_id))
    applied = heartbeat_apply.apply_answer(tenant_id, answer, store=store)
    log.info(
        "fleet.heartbeat.sent", status=200, count=len(answer.announcements), outcome="verified"
    )
    return {
        "status": 200,
        "answer": "verified",
        "announcements": len(answer.announcements),
        **applied,
    }
