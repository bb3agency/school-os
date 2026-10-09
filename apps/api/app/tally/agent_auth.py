"""Edge-agent authentication: the enrolment guard and request signing (ADR-0032 §2-§3, TB9).

Machine authentication for the five routes under ``/api/v1/edge/tally/``; no user session, no
BFF service token. Two FastAPI dependencies, both carrying ``sos_edge_agent`` so the
route-enumeration test can accept exactly them on exactly those routes (CLAUDE.md §6.2 as amended
by ADR-0032, Proposed):

- :func:`require_edge_agent_enrolment` (``POST /edge/tally/enrol``), in this order: at most
  ``max_attempts_per_address_per_hour`` attempts from one client address (any school, any
  outcome; charged first) -> the school id header, a timestamp within the skew window, the
  connector flag, a fresh nonce -> the school's failure budgets, which only wrong codes spend:
  ``max_failures_per_address_per_hour`` per school and client address, and the higher
  ``max_failures_per_school_per_hour`` for the school from all addresses (429 when either is
  used up). Someone who knows a tenant id can therefore lock out only their own address, not
  the school's office PC (audit 2026-10-04 AA-15). The one-time code itself, and that the school
  is active, are checked by the service in the school's transaction (row lock, used once), which
  records a wrong code with :meth:`EnrolmentCaller.failed`. A school whose connector flag is off,
  or that is not active, answers exactly like a wrong code (401), so an unauthenticated caller
  learns nothing about either. Counters live in Valkey under hashed addresses (never an address);
  Valkey unreachable -> 503 (fail closed).
- :func:`require_edge_agent_signature` (every other agent route), in this order, storing nothing
  on failure: headers well formed -> the device, its key and the school (read in that school's
  ``tenant_session``, RLS; no definer function) -> timestamp within ``skew_seconds`` -> body size
  -> ``X-SOS-Signature == "v1=" + hex(HMAC-SHA256(secret, canonical))`` in constant time (401 for
  any of these) -> connector flag on (404) -> nonce unseen (409 ``replay``) -> one accepted
  request per device and route window (429).

``canonical = "SOS-EDGE-HMAC-SHA256\\n" + METHOD + "\\n" + PATH + "\\n" + TIMESTAMP + "\\n" + NONCE
+ "\\n" + hex(SHA-256(body))``; the agent builds the same string
(``apps/edge-agent/sos_edge_agent/signing.py``; both are pinned to one test vector).

Log lines carry the device id and a reason code only (never headers, keys or bodies).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Final, Literal

import redis
from fastapi import Depends, Request

from app.authz.kv import InMemoryKV, KVStore, KVUnavailable, RedisKV
from app.core import ratelimit
from app.core.config import get_settings
from app.core.crypto import CryptoError, KeyWrapper, get_key_wrapper, unwrap_bound
from app.core.db import tenant_session
from app.core.errors import (
    Conflict,
    NotFound,
    RateLimited,
    ServiceUnavailable,
    Unauthenticated,
)
from app.core.feature_flags import is_enabled
from app.core.logging import get_logger
from app.identity.service_token import InMemoryReplayStore, RedisReplayStore, ReplayStore
from app.tally import repository as repo
from app.tally.config import VERSION_PATTERN, rules
from app.tenancy import service as tenancy

log = get_logger(__name__)

HEADER_TENANT: Final = "X-SOS-Tenant"
HEADER_DEVICE: Final = "X-SOS-Device"
HEADER_KEY_ID: Final = "X-SOS-Key-Id"
HEADER_TIMESTAMP: Final = "X-SOS-Timestamp"
HEADER_NONCE: Final = "X-SOS-Nonce"
HEADER_SIGNATURE: Final = "X-SOS-Signature"
HEADER_AGENT_VERSION: Final = "X-SOS-Agent-Version"
SCHEME: Final = "SOS-EDGE-HMAC-SHA256"
PSEUDO_PERMISSION: Final = "tally.agent"
"""Not in the permission catalog; no role can hold it (ADR-0032 §3)."""
MAX_BODY_BYTES: Final = 1024 * 1024
"""The API's request limit (``core.middleware``); checked again here for clarity."""

_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_KEY_ID = re.compile(r"^tdk-[a-z]{20}$")
_VERSION = re.compile(VERSION_PATTERN)
_REJECTED: Final = "Edge agent request rejected"

RouteName = Literal["config", "catalog", "sync", "key_rotation"]


# --- signing (shared definition with the agent) -------------------------------------------------


def canonical(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return "\n".join((SCHEME, method.upper(), path, timestamp, nonce, digest)).encode("utf-8")


def sign(secret: bytes, *, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    mac = hmac.new(secret, canonical(method, path, timestamp, nonce, body), hashlib.sha256)
    return "v1=" + mac.hexdigest()


# --- stores and key wrapper -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentStores:
    nonces: ReplayStore
    rate: ReplayStore
    enrolment: KVStore
    """Enrolment counters (fixed hour windows; keys ``sos:tally-enrol:...``, AA-15)."""


@lru_cache(maxsize=1)
def get_agent_stores() -> AgentStores:
    settings = get_settings()
    if settings.is_production_like:
        client = redis.Redis.from_url(
            settings.redis_url.get_secret_value(), socket_timeout=0.5, socket_connect_timeout=0.5
        )
        return AgentStores(
            RedisReplayStore(client, prefix="sos:tally-nonce:"),
            RedisReplayStore(client, prefix="sos:tally-rate:"),
            RedisKV(client),
        )
    return AgentStores(InMemoryReplayStore(), InMemoryReplayStore(), InMemoryKV())


@lru_cache(maxsize=1)
def get_agent_key_wrapper() -> KeyWrapper:
    return get_key_wrapper(get_settings())


# --- verified callers -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentCaller:
    """A device whose request passed :func:`require_edge_agent_signature`."""

    tenant_id: uuid.UUID
    device_id: uuid.UUID
    key_id: str
    used_next_key: bool
    agent_version: str | None


@dataclass(frozen=True, slots=True)
class EnrolmentCaller:
    """An enrolment attempt that passed :func:`require_edge_agent_enrolment` (code unchecked)."""

    tenant_id: uuid.UUID
    agent_version: str | None
    failure_keys: tuple[str, ...] = ()
    """The school's failure counters this attempt spends if its code is wrong (AA-15)."""
    counters: KVStore | None = None

    def failed(self) -> None:
        """Count a wrong, used or expired code against the school's failure budgets."""
        if self.counters is None:
            return
        try:
            for key in self.failure_keys:
                self.counters.incr(key, ttl_s=_WINDOW_S)
        except KVUnavailable:
            log.warning("tally.agent.counter_unavailable", error_code="kv_unavailable")


def _reject(reason: str, device_id: uuid.UUID | None = None) -> Unauthenticated:
    log.warning(
        "tally.agent.rejected",
        error_code=reason,
        resource_type="tally_device",
        resource_id=device_id,
        outcome="rejected",
    )
    return Unauthenticated(_REJECTED)


def _tenant(headers: Mapping[str, str]) -> uuid.UUID:
    try:
        return uuid.UUID(headers.get(HEADER_TENANT, ""))
    except ValueError:
        raise _reject("bad_tenant_header") from None


def _check_time_and_nonce(headers: Mapping[str, str], at: dt.datetime) -> tuple[str, str]:
    timestamp = headers.get(HEADER_TIMESTAMP, "")
    nonce = headers.get(HEADER_NONCE, "")
    if not timestamp.isdigit() or len(timestamp) > 12:
        raise _reject("bad_timestamp")
    if abs(at.timestamp() - int(timestamp)) > rules().signing.skew_seconds:
        raise _reject("clock_skew")
    if not _NONCE.fullmatch(nonce):
        raise _reject("bad_nonce")
    return timestamp, nonce


def _agent_version(headers: Mapping[str, str]) -> str | None:
    value = headers.get(HEADER_AGENT_VERSION)
    return value if value is not None and _VERSION.fullmatch(value) else None


def _claim_nonce(stores: AgentStores, tenant_id: uuid.UUID, nonce: str) -> None:
    ttl = dt.timedelta(seconds=rules().signing.nonce_ttl_seconds)
    if not stores.nonces.claim(f"{tenant_id}:{nonce}", ttl):
        log.warning("tally.agent.rejected", error_code="replay", outcome="rejected")
        raise Conflict("This request was already received.", code="replay")


def _valid_keys(device: repo.DeviceKeys, at: dt.datetime) -> dict[str, tuple[bytes, bool]]:
    """key id -> (wrapped secret, is the next key). The current key stops working after the
    rotation overlap; the next key works from the moment it was issued."""
    keys: dict[str, tuple[bytes, bool]] = {}
    overlap = dt.timedelta(days=rules().signing.key_rotation_overlap_days)
    started = device.rotation_started_at
    if device.key_id and device.key_ciphertext and (started is None or at - started <= overlap):
        keys[device.key_id] = (device.key_ciphertext, False)
    if device.next_key_id and device.next_key_ciphertext:
        keys[device.next_key_id] = (device.next_key_ciphertext, True)
    return keys


def verify_signed(
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes,
    route: RouteName,
    *,
    wrapper: KeyWrapper,
    stores: AgentStores,
    at: dt.datetime | None = None,
) -> AgentCaller:
    current = at or dt.datetime.now(dt.UTC)
    tenant_id = _tenant(headers)
    try:
        device_id = uuid.UUID(headers.get(HEADER_DEVICE, ""))
    except ValueError:
        raise _reject("bad_device_header") from None
    key_id = headers.get(HEADER_KEY_ID, "")
    if not _KEY_ID.fullmatch(key_id):
        raise _reject("bad_key_id", device_id)
    with tenant_session(tenant_id) as session:
        device = repo.device_keys(session, device_id)
        school = tenancy.get_tenant(session) if device is not None else None
        flag_on = is_enabled(rules().flag, tenant_id, session=session)
    if device is None or device.status != "active":
        raise _reject("unknown_device", device_id)
    if school is None or school.status != "active":
        raise _reject("school_not_active", device_id)
    found = _valid_keys(device, current).get(key_id)
    if found is None:
        raise _reject("unknown_key", device_id)
    timestamp, nonce = _check_time_and_nonce(headers, current)
    if len(body) > MAX_BODY_BYTES:
        raise _reject("body_too_large", device_id)
    try:
        secret = unwrap_bound(
            wrapper, found[0], tenant_id=tenant_id, resource=f"tally_device/{device_id}"
        )
    except CryptoError:
        raise _reject("key_unwrap_failed", device_id) from None
    expected = sign(secret, method=method, path=path, timestamp=timestamp, nonce=nonce, body=body)
    # Bytes, not str: compare_digest refuses str with non-ASCII characters (headers are latin-1).
    given = headers.get(HEADER_SIGNATURE, "").encode("utf-8", "surrogateescape")
    if not hmac.compare_digest(expected.encode("ascii"), given):
        raise _reject("bad_signature", device_id)
    if not flag_on:
        raise NotFound()
    _claim_nonce(stores, tenant_id, nonce)
    window = dt.timedelta(seconds=getattr(rules().rate_limits_seconds, route))
    if not stores.rate.claim(f"{device_id}:{route}", window):
        raise RateLimited(
            "Too many requests from this Tally agent; try again later.",
            retry_after_s=int(window.total_seconds()),
        )
    return AgentCaller(
        tenant_id=tenant_id,
        device_id=device_id,
        key_id=key_id,
        used_next_key=found[1],
        agent_version=_agent_version(headers),
    )


def verify_enrolment(
    headers: Mapping[str, str],
    *,
    stores: AgentStores,
    client_ip: str,
    at: dt.datetime | None = None,
) -> EnrolmentCaller:
    current = at or _utcnow()
    cfg = rules().enrolment
    hour = int(current.timestamp()) // _WINDOW_S
    retry = _WINDOW_S - int(current.timestamp()) % _WINDOW_S
    address = ratelimit.ip_hash(client_ip)
    counters = stores.enrolment
    try:
        # 1. Every attempt from one address, before anything else is checked.
        if counters.incr(f"{_ENROL}ip:{address}:{hour}", ttl_s=_WINDOW_S) > (
            cfg.max_attempts_per_address_per_hour
        ):
            raise _enrolment_limited("address", retry)
        tenant_id = _tenant(headers)
        _, nonce = _check_time_and_nonce(headers, current)
        if not is_enabled(rules().flag, tenant_id):
            raise _reject("connector_off")
        _claim_nonce(stores, tenant_id, nonce)
        # 2. The school's failure budgets, spent only by wrong codes (EnrolmentCaller.failed).
        per_address = f"{_ENROL}fail:{tenant_id}:{address}:{hour}"
        per_school = f"{_ENROL}fail:{tenant_id}:{hour}"
        if _count(counters, per_address) >= cfg.max_failures_per_address_per_hour:
            raise _enrolment_limited("school_address", retry)
        if _count(counters, per_school) >= cfg.max_failures_per_school_per_hour:
            raise _enrolment_limited("school", retry)
    except KVUnavailable:
        log.warning("tally.agent.rejected", error_code="kv_unavailable", outcome="rejected")
        raise ServiceUnavailable() from None
    return EnrolmentCaller(
        tenant_id=tenant_id,
        agent_version=_agent_version(headers),
        failure_keys=(per_address, per_school),
        counters=counters,
    )


_ENROL: Final = "sos:tally-enrol:"
_WINDOW_S: Final = 3600


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _client_ip(request: Request) -> str:
    """The client address from the trusted proxy chain (``core.ratelimit``); tests replace it."""
    return ratelimit.client_ip_of(request)


def _count(counters: KVStore, key: str) -> int:
    raw = counters.get(key)
    return int(raw) if raw is not None else 0


def _enrolment_limited(reason: str, retry_after_s: int) -> RateLimited:
    log.warning("tally.agent.rejected", error_code=f"enrol_limited_{reason}", outcome="rejected")
    return RateLimited(
        "Too many enrolment attempts; try again in an hour.", retry_after_s=retry_after_s
    )


# --- FastAPI dependencies ---------------------------------------------------------------------


class RequireEdgeAgentSignature:
    """Dependency for the signed agent routes (machine auth; no user token)."""

    # Machine authentication, not a catalog permission: the route-enumeration test accepts this
    # guard only on the agent routes under /api/v1/edge/tally/ (ADR-0032 §3).
    sos_permission = PSEUDO_PERMISSION
    sos_edge_agent = "signature"
    sos_step_up = False
    sos_scope = None

    def __init__(self, route: RouteName) -> None:
        self.route: RouteName = route

    async def __call__(
        self,
        request: Request,
        wrapper: Annotated[KeyWrapper, Depends(get_agent_key_wrapper)],
        stores: Annotated[AgentStores, Depends(get_agent_stores)],
    ) -> AgentCaller:
        body = await request.body()
        return verify_signed(
            request.method,
            request.url.path,
            request.headers,
            body,
            self.route,
            wrapper=wrapper,
            stores=stores,
        )


class RequireEdgeAgentEnrolment:
    """Dependency for ``POST /api/v1/edge/tally/enrol`` (the one-time code is in the body)."""

    sos_permission = PSEUDO_PERMISSION
    sos_edge_agent = "enrolment"
    sos_step_up = False
    sos_scope = None

    async def __call__(
        self,
        request: Request,
        stores: Annotated[AgentStores, Depends(get_agent_stores)],
    ) -> EnrolmentCaller:
        return verify_enrolment(request.headers, stores=stores, client_ip=_client_ip(request))


def require_edge_agent_signature(route: RouteName) -> RequireEdgeAgentSignature:
    return RequireEdgeAgentSignature(route)


def require_edge_agent_enrolment() -> RequireEdgeAgentEnrolment:
    return RequireEdgeAgentEnrolment()


__all__ = [
    "HEADER_AGENT_VERSION",
    "HEADER_DEVICE",
    "HEADER_KEY_ID",
    "HEADER_NONCE",
    "HEADER_SIGNATURE",
    "HEADER_TENANT",
    "HEADER_TIMESTAMP",
    "PSEUDO_PERMISSION",
    "AgentCaller",
    "AgentStores",
    "EnrolmentCaller",
    "RequireEdgeAgentEnrolment",
    "RequireEdgeAgentSignature",
    "canonical",
    "get_agent_key_wrapper",
    "get_agent_stores",
    "require_edge_agent_enrolment",
    "require_edge_agent_signature",
    "sign",
    "verify_enrolment",
    "verify_signed",
]
