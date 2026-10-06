"""API request rate limiting and failed-authentication backoff (audit 2026-10-05 P2-07).

Standards: OWASP API4:2023 (unrestricted resource consumption), ASVS 4.0.3 2.2.1 (anti-automation,
soft lockout) and 11.1.4 (business-flow limits), RFC 6585 §4 (429), RFC 9110 §10.2.3
(``Retry-After``), RFC 9457 (problem+json) and the IETF ``RateLimit-Policy`` / ``RateLimit``
header fields (draft-ietf-httpapi-ratelimit-headers-11). Budgets live in ``rate_limits.yaml``
(invariant 13); docs/09 §2.7 has the policy table.

**Algorithm.** GCRA, the "virtual scheduling" form of a token bucket: one theoretical arrival time
(TAT) per bucket, refilled evenly over the window, burst up to the quota, weighted cost per
request. :class:`ValkeyRateLimitStore` runs one Lua script per check that reads the server clock
(``TIME``), checks every bucket of the request plus any active backoff block, and charges all of
them or none: one round trip, no check-then-act race between API tasks. The in-memory store has
the same semantics under a lock (local/CI, and the per-process fallback below).

**Layers** (each its own budget): per client IP before authentication (middleware; client IP from
the trusted proxy chain only, :func:`client_ip`), then per signed-in person, per school and the
stricter per-route budgets in the route guard (``authz.require``), or per operator on
``/api/v1/platform/*``. Successful and refused responses carry the headers of the policies that
applied; aggregates shared with other callers (the IP and school layers) show only on the 429 they
cause, and nothing about another school is ever shown.

**Failure mode.** Valkey unreachable: ``fail: open`` policies allow the request (normal and
authenticated traffic; a throttled ``security.rate_limit.unavailable`` warning is logged and
alarmed), ``fail: closed`` policies and the authentication backoff use a per-process in-memory
limiter with the same budget, so an outage cannot be used to brute-force a sign-in path.

**Logging.** ``security.rate_limited`` and ``security.auth.failed`` carry IDs, the policy name
and ``ip_hash`` (HMAC-SHA256 of the address under a key derived from ``SOS_SERVICE_TOKEN_KEY``,
16 hex characters; the BFF computes the same value), never the address or any personal data
(invariant 5). Valkey keys hold the same hashes, never an address.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any, Final, Literal, Protocol

import redis
import yaml
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.types import Scope

from app.core.config import Settings, get_settings
from app.core.errors import RateLimited
from app.core.logging import get_logger

__all__ = [
    "CLIENT_IP_STATE",
    "RATE_LIMIT_STATE",
    "Bucket",
    "Decision",
    "InMemoryRateLimitStore",
    "PolicyState",
    "RateLimitConfig",
    "RateLimitExceeded",
    "RateLimiter",
    "StoreUnavailable",
    "ValkeyRateLimitStore",
    "clear_auth_failures",
    "client_ip",
    "client_ip_of",
    "enforce",
    "get_rate_limiter",
    "header_values",
    "ip_hash",
    "load_config",
    "principal_key",
    "record_auth_failure",
    "set_rate_limiter",
]

log = get_logger(__name__)

RATE_LIMIT_STATE: Final = "sos_rate_limit"
"""``request.state`` key: list of :class:`PolicyState` the response headers report."""
CLIENT_IP_STATE: Final = "sos_client_ip"
"""``request.state`` key: the client IP resolved by the middleware."""
_UNAVAILABLE_LOG_EVERY_S: Final = 10.0

Per = Literal["ip", "user", "school", "operator"]
FailMode = Literal["open", "closed"]
FailureScope = Literal["ip", "subject_ip"]


# --- configuration --------------------------------------------------------------------------


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = ""
    quota: int = Field(ge=1, le=1_000_000)
    window_s: int = Field(ge=1, le=86_400)
    per: Per
    fail: FailMode
    report: bool
    weighted: bool


class Cost(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    read: int = Field(ge=1, le=100)
    write: int = Field(ge=1, le=100)


class Backoff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    free_failures: int = Field(ge=1, le=1000)
    base_delay_s: int = Field(ge=1, le=3600)
    max_delay_s: int = Field(ge=1, le=86_400)
    reset_after_s: int = Field(ge=60, le=86_400)


class AuthFailures(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ip: Backoff
    subject_ip: Backoff


class RateLimitConfig(BaseModel):
    """``app/core/rate_limits.yaml``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    key_prefix: str = Field(pattern=r"^sos:rl:[a-z0-9]+$")
    cost: Cost
    exempt_paths: tuple[str, ...]
    machine_path_prefixes: tuple[str, ...]
    policies: dict[str, Policy]
    routes: dict[str, tuple[str, ...]]
    auth_failures: AuthFailures

    def model_post_init(self, _: Any) -> None:
        for name in ("ip", "machine_ip", "user", "school", "operator"):
            if name not in self.policies:
                raise ValueError(f"rate_limits.yaml needs policy {name!r}")
        for route, names in self.routes.items():
            method, _, path = route.partition(" ")
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not path.startswith("/"):
                raise ValueError(f"rate_limits.yaml route {route!r} is not 'METHOD /path'")
            for name in names:
                if name not in self.policies:
                    raise ValueError(f"rate_limits.yaml route {route!r}: unknown policy {name!r}")
                if self.policies[name].per == "ip":
                    raise ValueError(f"rate_limits.yaml route {route!r}: per-ip route policy")

    def policy(self, name: str) -> Policy:
        return self.policies[name].model_copy(update={"name": name})

    def route_policies(self, method: str, template: str | None) -> list[Policy]:
        if template is None:
            return []
        return [self.policy(n) for n in self.routes.get(f"{method.upper()} {template}", ())]

    def is_machine_path(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.machine_path_prefixes)


@lru_cache(maxsize=1)
def load_config() -> RateLimitConfig:
    text = resources.files("app.core").joinpath("rate_limits.yaml").read_text("utf-8")
    return RateLimitConfig.model_validate(yaml.safe_load(text))


# --- stores ---------------------------------------------------------------------------------


class StoreUnavailable(RuntimeError):
    """Valkey could not be reached."""


@dataclass(frozen=True, slots=True)
class BucketSpec:
    key: str
    period_ms: int
    quota: int
    cost: int


@dataclass(frozen=True, slots=True)
class BucketResult:
    remaining: int
    retry_ms: int
    reset_ms: int


@dataclass(frozen=True, slots=True)
class RawDecision:
    allowed: bool
    block_ms: int
    buckets: tuple[BucketResult, ...]


class RateLimitStore(Protocol):
    def acquire(self, blocks: Sequence[str], buckets: Sequence[BucketSpec]) -> RawDecision: ...

    def fail(self, count_key: str, block_key: str, backoff: Backoff) -> tuple[int, int]:
        """Count one failure; returns ``(failures, delay_ms)`` (0 = no delay yet)."""
        ...

    def clear(self, *keys: str) -> None: ...


def _gcra(tat: float, now: float, spec: BucketSpec) -> tuple[float, BucketResult, bool]:
    """One GCRA step: (new TAT if allowed, result, allowed)."""
    interval = spec.period_ms / spec.quota
    tat = max(tat, now)
    new_tat = tat + interval * spec.cost
    allow_at = new_tat - spec.period_ms
    if allow_at > now:
        remaining = math.floor((spec.period_ms - (tat - now)) / interval + 1e-6)
        result = BucketResult(max(0, remaining), math.ceil(allow_at - now), math.ceil(tat - now))
        return new_tat, result, False
    remaining = math.floor((spec.period_ms - (new_tat - now)) / interval + 1e-6)
    return new_tat, BucketResult(max(0, remaining), 0, math.ceil(new_tat - now)), True


class InMemoryRateLimitStore:
    """Per-process store with the Lua script's semantics (local/CI, fail-closed fallback).

    Bounded: expired entries are dropped when the store grows past ``max_entries``."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = lambda: time.time() * 1000,
        max_entries: int = 100_000,
    ) -> None:
        self.clock = clock
        self._tat: dict[str, tuple[float, float]] = {}  # key -> (value, expires_at_ms)
        self._lock = threading.Lock()
        self._max = max_entries

    def _get(self, key: str, now: float) -> float | None:
        item = self._tat.get(key)
        if item is None:
            return None
        if item[1] <= now:
            del self._tat[key]
            return None
        return item[0]

    def _put(self, key: str, value: float, expires_at: float, now: float) -> None:
        if len(self._tat) >= self._max:
            for k in [k for k, (_, exp) in self._tat.items() if exp <= now]:
                del self._tat[k]
            if len(self._tat) >= self._max:
                # Keep working rather than fail: drop the oldest half.
                for k in list(self._tat)[: self._max // 2]:
                    del self._tat[k]
        self._tat[key] = (value, expires_at)

    def acquire(self, blocks: Sequence[str], buckets: Sequence[BucketSpec]) -> RawDecision:
        with self._lock:
            now = self.clock()
            block_ms = 0
            for key in blocks:
                until = self._get(key, now)
                if until is not None and until - now > block_ms:
                    block_ms = math.ceil(until - now)
            allowed = block_ms <= 0
            results: list[BucketResult] = []
            tats: list[float] = []
            for spec in buckets:
                new_tat, result, ok = _gcra(self._get(spec.key, now) or 0.0, now, spec)
                allowed = allowed and ok
                results.append(result)
                tats.append(new_tat)
            if allowed:
                for spec, new_tat in zip(buckets, tats, strict=True):
                    self._put(spec.key, new_tat, new_tat, now)
            return RawDecision(allowed, block_ms, tuple(results))

    def fail(self, count_key: str, block_key: str, backoff: Backoff) -> tuple[int, int]:
        with self._lock:
            now = self.clock()
            count = int(self._get(count_key, now) or 0) + 1
            self._put(count_key, count, now + backoff.reset_after_s * 1000, now)
            if count <= backoff.free_failures:
                return count, 0
            delay = _delay_ms(count, backoff)
            self._put(block_key, now + delay, now + delay, now)
            return count, delay

    def clear(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._tat.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._tat.clear()


def _delay_ms(count: int, backoff: Backoff) -> int:
    exponent = min(count - backoff.free_failures - 1, 40)
    return int(min(backoff.base_delay_s * 1000 * (1 << exponent), backoff.max_delay_s * 1000))


# KEYS[1..nb]: backoff block keys (value = blocked-until, ms). KEYS[nb+1..]: GCRA buckets (TAT, ms).
# ARGV[1] = nb, then per bucket: period_ms, quota, cost. Returns {allowed, block_ms, then per
# bucket remaining, retry_ms, reset_ms}. All buckets are charged together or not at all.
_ACQUIRE_LUA: Final = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local nb = tonumber(ARGV[1])
local block_ms = 0
for i = 1, nb do
  local untl = tonumber(redis.call('GET', KEYS[i]) or '0') or 0
  if untl - now > block_ms then block_ms = untl - now end
end
local allowed = block_ms <= 0
local out = {0, math.ceil(block_ms)}
local tats = {}
for i = nb + 1, #KEYS do
  local j = (i - nb - 1) * 3
  local period = tonumber(ARGV[j + 2])
  local quota = tonumber(ARGV[j + 3])
  local cost = tonumber(ARGV[j + 4])
  local interval = period / quota
  local tat = tonumber(redis.call('GET', KEYS[i]) or '0') or 0
  if tat < now then tat = now end
  local new_tat = tat + interval * cost
  local allow_at = new_tat - period
  local remaining, retry, reset
  if allow_at > now then
    allowed = false
    remaining = math.floor((period - (tat - now)) / interval + 0.000001)
    retry = math.ceil(allow_at - now)
    reset = math.ceil(tat - now)
  else
    remaining = math.floor((period - (new_tat - now)) / interval + 0.000001)
    retry = 0
    reset = math.ceil(new_tat - now)
  end
  if remaining < 0 then remaining = 0 end
  tats[#tats + 1] = new_tat
  out[#out + 1] = remaining
  out[#out + 1] = retry
  out[#out + 1] = reset
end
if allowed then
  for i = nb + 1, #KEYS do
    local nt = tats[i - nb]
    redis.call('SET', KEYS[i], string.format('%.3f', nt), 'PX', math.max(1, math.ceil(nt - now)))
  end
  out[1] = 1
end
return out
"""

# KEYS[1] failure count, KEYS[2] block. ARGV: free, base_ms, max_ms, reset_ms.
_FAIL_LUA: Final = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local n = redis.call('INCR', KEYS[1])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[4]))
local free = tonumber(ARGV[1])
if n <= free then return {n, 0} end
local exponent = n - free - 1
if exponent > 40 then exponent = 40 end
local delay = tonumber(ARGV[2]) * (2 ^ exponent)
if delay > tonumber(ARGV[3]) then delay = tonumber(ARGV[3]) end
redis.call('SET', KEYS[2], string.format('%d', now + delay), 'PX', math.max(1, delay))
return {n, delay}
"""


class ValkeyRateLimitStore:
    """Valkey store: one atomic Lua call per check. Every failure is :class:`StoreUnavailable`."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._acquire = client.register_script(_ACQUIRE_LUA)
        self._fail = client.register_script(_FAIL_LUA)

    def acquire(self, blocks: Sequence[str], buckets: Sequence[BucketSpec]) -> RawDecision:
        args: list[int] = [len(blocks)]
        for spec in buckets:
            args += [spec.period_ms, spec.quota, spec.cost]
        try:
            raw = self._acquire(keys=[*blocks, *(b.key for b in buckets)], args=args)
        except redis.RedisError as exc:
            raise StoreUnavailable("valkey rate limit check failed") from exc
        values = [int(v) for v in raw]
        results = tuple(
            BucketResult(values[i], values[i + 1], values[i + 2]) for i in range(2, len(values), 3)
        )
        return RawDecision(values[0] == 1, values[1], results)

    def fail(self, count_key: str, block_key: str, backoff: Backoff) -> tuple[int, int]:
        args = [
            backoff.free_failures,
            backoff.base_delay_s * 1000,
            backoff.max_delay_s * 1000,
            backoff.reset_after_s * 1000,
        ]
        try:
            count, delay = self._fail(keys=[count_key, block_key], args=args)
        except redis.RedisError as exc:
            raise StoreUnavailable("valkey failure count failed") from exc
        return int(count), int(delay)

    def clear(self, *keys: str) -> None:
        if not keys:
            return
        try:
            self._client.delete(*keys)
        except redis.RedisError as exc:
            raise StoreUnavailable("valkey delete failed") from exc


# --- decisions ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bucket:
    policy: Policy
    partition: str
    cost: int = 1


@dataclass(frozen=True, slots=True)
class PolicyState:
    """What the RateLimit headers say about one policy."""

    policy: Policy
    remaining: int
    reset_s: int
    show: bool


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    retry_after_s: int
    states: tuple[PolicyState, ...]
    denied: Policy | None = None
    blocked: bool = False
    degraded: bool = False


@dataclass
class _UnavailableLog:
    lock: threading.Lock = field(default_factory=threading.Lock)
    last: float = 0.0
    pending: int = 0
    total: int = 0


_unavailable = _UnavailableLog()


def _note_unavailable() -> None:
    """Throttled warning + counter (the alarm reads the log line; docs/10 §12)."""
    with _unavailable.lock:
        _unavailable.pending += 1
        _unavailable.total += 1
        now = time.monotonic()
        if now - _unavailable.last < _UNAVAILABLE_LOG_EVERY_S and _unavailable.last:
            return
        count, _unavailable.pending, _unavailable.last = _unavailable.pending, 0, now
    log.warning("security.rate_limit.unavailable", count=count)


def unavailable_total() -> int:
    """How many checks found Valkey unreachable in this process (tests, diagnostics)."""
    return _unavailable.total


def _ceil_s(ms: int) -> int:
    return max(1, math.ceil(ms / 1000)) if ms > 0 else 0


class RateLimiter:
    def __init__(
        self,
        config: RateLimitConfig,
        store: RateLimitStore,
        *,
        enabled: bool = True,
        fallback: InMemoryRateLimitStore | None = None,
        hash_key: bytes = b"",
    ) -> None:
        self.config = config
        self.store = store
        self.enabled = enabled
        self.fallback = fallback or (
            store if isinstance(store, InMemoryRateLimitStore) else InMemoryRateLimitStore()
        )
        self._hash_key = hash_key

    # keys

    def _key(self, *parts: str) -> str:
        return ":".join((self.config.key_prefix, *parts))

    def ip_hash(self, ip: str) -> str:
        return hmac.new(
            self._hash_key, f"sos-ip-hash-v1|{ip}".encode(), hashlib.sha256
        ).hexdigest()[:16]

    def _spec(self, bucket: Bucket) -> BucketSpec:
        return BucketSpec(
            self._key(bucket.policy.name, bucket.partition),
            bucket.policy.window_s * 1000,
            bucket.policy.quota,
            bucket.cost,
        )

    def _failure_keys(self, scope: FailureScope, partition: str) -> tuple[str, str]:
        base = self._key("fail", scope, partition)
        return f"{base}:n", f"{base}:block"

    def block_key(self, scope: FailureScope, partition: str) -> str:
        return self._failure_keys(scope, partition)[1]

    # checks

    def check(self, buckets: Sequence[Bucket], blocks: Sequence[str] = ()) -> Decision:
        """Charge every bucket (or none) unless a block is active. Never raises."""
        if not self.enabled or (not buckets and not blocks):
            return Decision(True, 0, ())
        try:
            raw = self.store.acquire(blocks, [self._spec(b) for b in buckets])
            return self._decision(buckets, raw, degraded=False)
        except StoreUnavailable:
            _note_unavailable()
        closed = [b for b in buckets if b.policy.fail == "closed"]
        # Backoff blocks always fail closed (credential paths).
        raw = self.fallback.acquire(blocks, [self._spec(b) for b in closed])
        return self._decision(closed, raw, degraded=True)

    def _decision(self, buckets: Sequence[Bucket], raw: RawDecision, *, degraded: bool) -> Decision:
        states: list[PolicyState] = []
        denied: Policy | None = None
        worst = 0
        for bucket, result in zip(buckets, raw.buckets, strict=True):
            if result.retry_ms > worst:
                worst, denied = result.retry_ms, bucket.policy
            states.append(
                PolicyState(
                    bucket.policy,
                    result.remaining,
                    _ceil_s(result.reset_ms),
                    bucket.policy.report or result.retry_ms > 0,
                )
            )
        blocked = raw.block_ms > 0
        retry_ms = max(worst, raw.block_ms)
        if raw.allowed:
            return Decision(True, 0, tuple(states), degraded=degraded)
        return Decision(
            False,
            _ceil_s(retry_ms),
            tuple(states),
            denied=None if blocked and raw.block_ms >= worst else denied,
            blocked=blocked,
            degraded=degraded,
        )

    def record_failure(self, scope: FailureScope, partition: str) -> int:
        """Count one failed authentication; returns the delay (s) now imposed (0 = none)."""
        backoff = getattr(self.config.auth_failures, scope)
        count_key, block_key = self._failure_keys(scope, partition)
        try:
            _, delay = self.store.fail(count_key, block_key, backoff)
        except StoreUnavailable:
            _note_unavailable()
            _, delay = self.fallback.fail(count_key, block_key, backoff)
        return _ceil_s(delay)

    def clear_failures(self, scope: FailureScope, partition: str) -> None:
        keys = self._failure_keys(scope, partition)
        try:
            self.store.clear(*keys)
        except StoreUnavailable:
            _note_unavailable()
        self.fallback.clear(*keys)


# --- process-wide limiter -------------------------------------------------------------------


def _hash_key(settings: Settings) -> bytes:
    return hmac.new(
        settings.service_token_key.get_secret_value().encode(),
        b"sos-rate-limit-ip-hash",
        hashlib.sha256,
    ).digest()


def build_rate_limiter(settings: Settings) -> RateLimiter:
    store: RateLimitStore
    if settings.is_production_like:
        client = redis.Redis.from_url(
            settings.redis_url.get_secret_value(), socket_timeout=0.5, socket_connect_timeout=0.5
        )
        store = ValkeyRateLimitStore(client)
    else:
        store = InMemoryRateLimitStore()
    return RateLimiter(
        load_config(), store, enabled=settings.rate_limit_enabled, hash_key=_hash_key(settings)
    )


@lru_cache(maxsize=1)
def _default_limiter() -> RateLimiter:
    return build_rate_limiter(get_settings())


_override: list[RateLimiter] = []


def set_rate_limiter(limiter: RateLimiter | None) -> None:
    """Replace the process-wide limiter (tests). ``None`` restores the default."""
    _override.clear()
    if limiter is not None:
        _override.append(limiter)


def get_rate_limiter() -> RateLimiter:
    return _override[0] if _override else _default_limiter()


def ip_hash(ip: str) -> str:
    return get_rate_limiter().ip_hash(ip)


# --- client IP ------------------------------------------------------------------------------

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _trusted(value: str, networks: Iterable[Network]) -> bool:
    ip = _parse_ip(value)
    return ip is not None and any(ip in net for net in networks)


def client_ip(scope: Scope, trusted: Sequence[Network]) -> tuple[str, bool]:
    """``(client address, internal)`` for a request.

    ``X-Forwarded-For`` counts only when the TCP peer is a trusted proxy (``SOS_TRUSTED_PROXIES``:
    the ALB and BFF subnets, Caddy and web on a dedicated host). The list is then read from the
    right, skipping trusted hops; the first untrusted address is the client. A header from any
    other peer is ignored, so a client cannot choose its own bucket. ``internal`` is true for a
    trusted peer that names no client (the BFF's own server-side calls), which the per-IP layer
    skips; they are still limited per person, school and route."""
    peer_info = scope.get("client")
    peer = str(peer_info[0]) if peer_info else "unknown"
    if not trusted or not _trusted(peer, trusted):
        return peer, False
    values: list[str] = []
    for name, value in scope.get("headers", ()):
        if name.lower() == b"x-forwarded-for":
            values.extend(v.strip() for v in value.decode("latin-1").split(","))
    hops = [v for v in values if v]
    if not hops:
        return peer, True
    for hop in reversed(hops):
        ip = _parse_ip(hop)
        if ip is None:
            return peer, False  # malformed chain: fall back to the trusted peer itself
        if not any(ip in net for net in trusted):
            return str(ip), False
    return str(_parse_ip(hops[0])), True


def client_ip_of(request: Request) -> str:
    value = getattr(request.state, CLIENT_IP_STATE, None)
    if isinstance(value, str):
        return value
    peer = request.client
    return peer.host if peer else "unknown"


# --- request integration ----------------------------------------------------------------------


class RateLimitExceeded(RateLimited):
    """429 ``rate_limited`` with ``Retry-After`` and the RateLimit headers (docs/09 §2.7)."""

    def __init__(self, retry_after_s: int) -> None:
        seconds = max(1, retry_after_s)
        super().__init__(
            f"Too many requests. Wait {seconds} seconds and try again.", retry_after_s=seconds
        )


def principal_key(kind: str, issuer: str, subject: str) -> str:
    """Partition of one signed-in caller (IDs only, hashed so keys hold no raw subject)."""
    return hashlib.sha256(f"{kind}|{issuer}|{subject}".encode()).hexdigest()[:32]


def remember(scope: Scope, decision: Decision) -> None:
    """Keep the decision's reportable policies for the response headers."""
    state = scope.setdefault("state", {})
    states = state.setdefault(RATE_LIMIT_STATE, [])
    states.extend(s for s in decision.states if s.show)


def _route_template(request: Request) -> str | None:
    path = getattr(request.scope.get("route"), "path", None)
    return path if isinstance(path, str) else None


def _cost(config: RateLimitConfig, method: str) -> int:
    return config.cost.read if method.upper() in {"GET", "HEAD"} else config.cost.write


def enforce(
    request: Request,
    *,
    principal: str,
    tenant_id: uuid.UUID | None,
    layer: Literal["user", "operator"],
    subject_ip_block: bool = False,
) -> None:
    """Layers 2-4 for an authenticated request (called by the route guards).

    ``principal``: :func:`principal_key`. ``tenant_id``: the resolved school (never the
    unverified header). ``subject_ip_block``: also refuse while this person + IP is in sign-in
    backoff (sign-in and operator routes). Raises :class:`RateLimitExceeded`."""
    limiter = get_rate_limiter()
    if not limiter.enabled:
        return
    config = limiter.config
    method = request.method
    cost = _cost(config, method)
    base = config.policy(layer)
    buckets = [Bucket(base, principal, cost if base.weighted else 1)]
    if tenant_id is not None:
        school = config.policy("school")
        buckets.append(Bucket(school, str(tenant_id), cost if school.weighted else 1))
    ip = client_ip_of(request)
    hashed_ip = limiter.ip_hash(ip)
    for policy in config.route_policies(method, _route_template(request)):
        if policy.per == "school":
            if tenant_id is None:
                continue
            partition = str(tenant_id)
        else:
            partition = principal
        buckets.append(Bucket(policy, partition, 1))
    blocks = (
        [limiter.block_key("subject_ip", f"{principal}:{hashed_ip}")] if subject_ip_block else []
    )
    decision = limiter.check(buckets, blocks)
    remember(request.scope, decision)
    if not decision.allowed:
        _log_limited(request, decision, hashed_ip)
        raise RateLimitExceeded(decision.retry_after_s)


def _log_limited(request: Request, decision: Decision, hashed_ip: str) -> None:
    template = _route_template(request)
    log.warning(
        "security.rate_limited",
        policy=decision.denied.name if decision.denied else "auth_backoff",
        route=f"{request.method.upper()} {template or 'unmatched'}",
        ip_hash=hashed_ip,
        retry_after_s=decision.retry_after_s,
    )


def record_auth_failure(request: Request, *, reason: str, principal: str | None = None) -> int:
    """A failed authentication (``security.auth.failed``, IDs and ip_hash only) and its backoff.

    Without ``principal`` the failure counts against the client IP; with it, against that
    person from that IP. Returns the delay now imposed in seconds (0 = none yet)."""
    limiter = get_rate_limiter()
    hashed_ip = limiter.ip_hash(client_ip_of(request))
    delay = 0
    if limiter.enabled:
        if principal is None:
            delay = limiter.record_failure("ip", hashed_ip)
        else:
            delay = limiter.record_failure("subject_ip", f"{principal}:{hashed_ip}")
    template = _route_template(request)
    log.warning(
        "security.auth.failed",
        error_code=reason,
        route=f"{request.method.upper()} {template or 'unmatched'}",
        ip_hash=hashed_ip,
        retry_after_s=delay,
    )
    return delay


def clear_auth_failures(request: Request, *, principal: str) -> None:
    """A successful sign-in clears this person + IP's failure count."""
    limiter = get_rate_limiter()
    if limiter.enabled:
        hashed_ip = limiter.ip_hash(client_ip_of(request))
        limiter.clear_failures("subject_ip", f"{principal}:{hashed_ip}")


def header_values(states: Sequence[PolicyState]) -> tuple[str, str] | None:
    """``(RateLimit-Policy, RateLimit)`` structured-field lists, one item per policy (the first
    state of each policy wins)."""
    seen: dict[str, PolicyState] = {}
    for state in states:
        seen.setdefault(state.policy.name, state)
    if not seen:
        return None
    policy = ", ".join(f'"{n}";q={s.policy.quota};w={s.policy.window_s}' for n, s in seen.items())
    current = ", ".join(f'"{n}";r={s.remaining};t={s.reset_s}' for n, s in seen.items())
    return policy, current
