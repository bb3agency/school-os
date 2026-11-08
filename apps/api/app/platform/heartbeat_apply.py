"""Dedicated host: apply a VERIFIED heartbeat answer locally (ADR-0058; docs/16 §19 Q5, Q7, Q19).

Called by :mod:`app.platform.heartbeat_client` only after the answer's signature, freshness and
request nonce were checked. Three independent steps; one failing does not stop the others, and
each keeps the last good state when it fails (logged with ids and codes only):

1. feature flags -> the school's overrides in the local ``platform.feature_flags``, written as
   ``sos_platform`` (:func:`app.platform.flags.apply_deployment_flags`; ``sos_app`` keeps
   read-only access), audited ``flag.override_set`` / ``flag.override_removed`` with
   ``source: heartbeat`` on the host's platform chain;
2. AI answer allowance -> ``tenancy.service.set_ai_answer_allowance`` (the lifecycle-style call
   of ADR-0020 amendment B3: one number in the school's own session, audited
   ``tenant.ai_allowance_set`` on the school chain);
3. billing summary -> the host's Valkey (no expiry; replaced by each verified answer), read by
   ``GET /tenant/billing`` on the Plan and billing page.

Idempotent: an answer that changes nothing writes and audits nothing.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import uuid
from functools import lru_cache
from typing import Any, Protocol

import redis

from app.core.config import get_settings
from app.core.logging import get_logger
from app.platform import flags
from app.platform.schemas import HbBillingSummary, HeartbeatOut
from app.tenancy import service as tenancy

log = get_logger(__name__)
BILLING_KEY = "sos:host:billing-summary:v1"


class BillingSummaryStore(Protocol):
    def put(self, payload: dict[str, Any]) -> None: ...

    def get(self) -> dict[str, Any] | None: ...


class InMemoryBillingSummaryStore:
    def __init__(self) -> None:
        self._value: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def put(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._value = dict(payload)

    def get(self) -> dict[str, Any] | None:
        with self._lock:
            return None if self._value is None else dict(self._value)


class RedisBillingSummaryStore:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def put(self, payload: dict[str, Any]) -> None:
        self._client.set(BILLING_KEY, json.dumps(payload))

    def get(self) -> dict[str, Any] | None:
        try:
            raw = self._client.get(BILLING_KEY)
        except redis.exceptions.RedisError:
            log.warning("fleet.billing_summary.unavailable", outcome="skipped")
            return None
        if raw is None:
            return None
        value: dict[str, Any] = json.loads(raw if isinstance(raw, bytes) else str(raw))
        return value


@lru_cache(maxsize=1)
def get_billing_store() -> BillingSummaryStore:
    settings = get_settings()
    if settings.is_production_like or settings.deployment_mode.value == "dedicated":
        client = redis.Redis.from_url(
            settings.redis_url.get_secret_value(), socket_timeout=0.5, socket_connect_timeout=0.5
        )
        return RedisBillingSummaryStore(client)
    return InMemoryBillingSummaryStore()


def stored_billing_summary(
    store: BillingSummaryStore | None = None,
) -> tuple[HbBillingSummary | None, dt.datetime] | None:
    """(summary or ``None`` for no live subscription, as of) from the last verified answer;
    ``None`` before the first one (or when the stored value no longer parses)."""
    raw = (store or get_billing_store()).get()
    if raw is None:
        return None
    try:
        as_of = dt.datetime.fromisoformat(str(raw["as_of"]))
        summary = raw.get("summary")
        return (None if summary is None else HbBillingSummary.model_validate(summary)), as_of
    except (KeyError, ValueError):
        log.warning("fleet.billing_summary.unreadable", outcome="skipped")
        return None


def _failed(step: str, tenant_id: uuid.UUID, exc: Exception) -> None:
    log.warning(
        "fleet.heartbeat.apply_failed",
        action=step,
        tenant_id=str(tenant_id),
        error_type=type(exc).__name__,
        outcome="kept_last_state",
    )


def apply_answer(
    tenant_id: uuid.UUID, answer: HeartbeatOut, *, store: BillingSummaryStore | None = None
) -> dict[str, Any]:
    """Apply a verified answer (see the module docstring). Returns what changed per step
    (``None`` for a step that failed or that the answer did not carry)."""
    result: dict[str, Any] = {"flags_changed": None, "allowance_changed": None, "billing": None}
    try:
        result["flags_changed"] = flags.apply_deployment_flags(tenant_id, answer.feature_flags)
    except Exception as exc:
        _failed("flags", tenant_id, exc)
    if answer.ai_allowance is not None:
        try:
            result["allowance_changed"] = tenancy.set_ai_answer_allowance(
                tenant_id, answer.ai_allowance.included_answers
            )
        except Exception as exc:
            _failed("ai_allowance", tenant_id, exc)
    try:
        (store or get_billing_store()).put(
            {
                "as_of": answer.received_at.isoformat(),
                "summary": None
                if answer.billing is None
                else answer.billing.model_dump(mode="json"),
            }
        )
        result["billing"] = "stored"
    except Exception as exc:
        _failed("billing", tenant_id, exc)
    return result
