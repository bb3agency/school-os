"""Per-school switches, monthly AI budget and rate limit, checked before every provider call.

Owner decisions (2026-09-27): the budget amount is the school's setting
``ai_monthly_budget_inr`` (tenant settings, FR-TEN-012) and both switches apply: the
``SOS_KB_ENABLED`` kill switch (settings, checked by the gateway) and the per-school
``kb.ask.enabled`` flag plus the school's ``ai_features_enabled`` setting. The gateway may not
import ``tenancy`` or ``platform`` (import-linter ``knowledge-gateway-isolated``,
``tenant-side-without-control-plane``), so the composition root (``knowledge.service``) reads
them through ``tenancy.service`` public functions and hands them in as a
:class:`TenantAiPolicy`.

Spend is metered in USD at list price (``models.yaml`` ``prices``) and compared with the INR
budget at ``budget.usd_inr_rate``. Budget months are calendar months in IST. At
``alert_fraction`` (80 %) the first crossing per month is reported once; at
``degrade_fraction`` (100 %) calls are refused with :class:`BudgetExhausted` and the caller
answers search-only until the month resets or the school raises its budget (FR-KB-011,
NFR-CST-001). The 100 % alert (owner decision 2026-10-03) is the FIRST reservation refused for
budget in the school's IST month: reservations stop recorded spend about one estimate below the
limit, so spend itself never reaches 100 %. It is claimed once per tenant and month with an
atomic set-if-absent key in the spend store (``sos:kb:exhausted:{tenant}:{YYYY-MM}``) and
logged like the 80 % alert (``kb.budget.alert_crossed``, ``action="exhausted"``, ids only). A
budget of 0 (no AI spend at all) is a setting, not a spent budget, and does not alert.

Reservations (FR-KB-011): before each provider call :meth:`BudgetGuard.admit` reserves the
call's worst-case cost (``models.yaml`` ``budget.reservation.input_tokens`` at the model's input
list price plus the role's ``max_output_tokens`` at its output price) in ONE atomic step that
succeeds only while ``spent + reserved + estimate`` stays within ``degrade_fraction`` of the
budget; otherwise the call is refused with :class:`BudgetExhausted` (search-only). After the call
:meth:`BudgetGuard.settle` turns the reservation into the real cost (lower or higher, and a
failed call the provider still billed is recorded at what it billed); :meth:`BudgetGuard.release`
frees it when nothing was billed. Every reservation has an id and a TTL
(``budget.reservation.ttl_s``) so an abandoned one lapses, and settling is idempotent. Concurrent
calls therefore cannot all pass the check and overshoot together; spend can exceed the budget
only by what calls use beyond their estimate. A reservation belongs to the IST month that
admitted it, and its cost is recorded in that month even when the call ends after midnight.

The per-minute rate limit is an atomic increment-then-compare (no check-then-act race); a call it
refuses releases its reservation.

Stores: :class:`SpendLedger` and the rate-limit counters live in Valkey in staging/prod
(shared by every API and worker process; the reservation steps are Lua scripts, atomic on the
single Valkey primary) and in memory locally (``authz.kv`` pattern, one lock).
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Final, Literal, Protocol, runtime_checkable

import redis

from app.authz.kv import KVStore, KVUnavailable
from app.core.ids import new_id
from app.core.logging import get_logger
from app.knowledge.config.llm import LlmConfig, RoleConfig
from app.knowledge.domain import Feature
from app.knowledge.gateway.errors import (
    AiDisabled,
    AiRateLimited,
    BudgetExhausted,
    ProviderUnavailable,
)

log = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30), "IST")
_MICRO = Decimal("1000000")
_SPEND_TTL_S = 62 * 24 * 3600  # a month plus margin; the key names the month


def budget_month(now: datetime) -> str:
    """``YYYY-MM`` of ``now`` in IST (budgets reset at IST midnight on the 1st)."""
    return now.astimezone(IST).strftime("%Y-%m")


@dataclass(frozen=True, slots=True)
class TenantAiSettings:
    ai_enabled: bool
    """The school's ``ai_features_enabled`` AND its ``kb.ask.enabled`` flag."""
    monthly_budget_inr: Decimal
    """``ai_monthly_budget_inr``; 0 means no AI spend at all."""


def bundle_budget_inr(config: LlmConfig, included_answers: int) -> Decimal:
    """The monthly budget of a school with an AI answer bundle (owner decision 2026-10-03):
    included answers x ``budget.bundle.cost_per_answer_usd`` x (1 + headroom), in INR at
    ``budget.usd_inr_rate``, rounded to a rupee."""
    if included_answers <= 0:
        raise ValueError("included_answers must be positive")
    b = config.budget
    usd = (
        Decimal(included_answers)
        * b.bundle.cost_per_answer_usd
        * (1 + Decimal(str(b.bundle.overage_headroom_fraction)))
    )
    return (usd * b.usd_inr_rate).quantize(Decimal(1))


@runtime_checkable
class TenantAiPolicy(Protocol):
    def settings_for(self, tenant_id: uuid.UUID) -> TenantAiSettings: ...


@dataclass(frozen=True, slots=True)
class StaticAiPolicy:
    """One answer for every tenant (local/CI, tests, the offline eval)."""

    settings: TenantAiSettings = TenantAiSettings(True, Decimal(5000))

    def settings_for(self, tenant_id: uuid.UUID) -> TenantAiSettings:
        return self.settings


# --- spend ledger -------------------------------------------------------------------------------

SettleState = Literal["settled", "late", "duplicate"]
"""``settled``: the reservation was held and is now spend. ``late``: it had already lapsed (TTL)
but the real cost is still recorded, once. ``duplicate``: already settled; nothing changes."""


@dataclass(frozen=True, slots=True)
class Settlement:
    total_usd: Decimal
    """The month's spend after this settlement."""
    state: SettleState


class SpendLedger(Protocol):
    def spent_usd(self, tenant_id: uuid.UUID, month: str) -> Decimal: ...

    def add_usd(self, tenant_id: uuid.UUID, month: str, amount: Decimal) -> Decimal:
        """Add ``amount`` and return the new month total."""
        ...

    def reserved_usd(self, tenant_id: uuid.UUID, month: str, now_ms: int) -> Decimal:
        """The sum of the month's live (unexpired, unsettled) reservations."""
        ...

    def reserve(
        self,
        tenant_id: uuid.UUID,
        month: str,
        reservation_id: str,
        amount: Decimal,
        limit: Decimal,
        *,
        now_ms: int,
        ttl_ms: int,
    ) -> bool:
        """Atomically hold ``amount`` if ``spent + reserved + amount <= limit``."""
        ...

    def settle(
        self,
        tenant_id: uuid.UUID,
        month: str,
        reservation_id: str,
        actual: Decimal,
        *,
        now_ms: int,
        keep_ms: int,
    ) -> Settlement:
        """Atomically drop the reservation and add ``actual`` to the month's spend, once per id
        (the id is remembered for ``keep_ms``)."""
        ...

    def mark_exhausted(self, tenant_id: uuid.UUID, month: str) -> bool:
        """Atomically record that the month's budget refused a call; ``True`` only for the
        first caller of the month (set-if-absent), so the 100 % alert fires once."""
        ...


def _key(tenant_id: uuid.UUID, month: str) -> str:
    return f"sos:kb:spend:{tenant_id}:{month}"


def _held_key(tenant_id: uuid.UUID, month: str) -> str:
    return f"sos:kb:resv:{tenant_id}:{month}"


def _expiry_key(tenant_id: uuid.UUID, month: str) -> str:
    return f"sos:kb:resv_exp:{tenant_id}:{month}"


def _done_key(tenant_id: uuid.UUID, month: str) -> str:
    return f"sos:kb:resv_done:{tenant_id}:{month}"


def _exhausted_key(tenant_id: uuid.UUID, month: str) -> str:
    return f"sos:kb:exhausted:{tenant_id}:{month}"


def _micro(amount: Decimal) -> int:
    return int((amount * _MICRO).to_integral_value())


def _usd(micro: int) -> Decimal:
    return Decimal(micro) / _MICRO


class InMemorySpendLedger:
    """Per process (local/CI and tests); one lock makes every step atomic."""

    def __init__(self) -> None:
        self._data: dict[str, int] = {}
        self._held: dict[str, dict[str, tuple[int, int]]] = {}
        """Per month key: reservation id -> (micro-USD, expires at ms)."""
        self._done: dict[str, dict[str, int]] = {}
        """Per month key: settled reservation id -> remembered until ms."""
        self._exhausted: set[str] = set()
        self._lock = threading.Lock()

    def _prune(self, key: str, now_ms: int) -> dict[str, tuple[int, int]]:
        held = self._held.setdefault(key, {})
        for rid in [rid for rid, (_, exp) in held.items() if exp <= now_ms]:
            del held[rid]
        done = self._done.setdefault(key, {})
        for rid in [rid for rid, until in done.items() if until <= now_ms]:
            del done[rid]
        return held

    def spent_usd(self, tenant_id: uuid.UUID, month: str) -> Decimal:
        with self._lock:
            return _usd(self._data.get(_key(tenant_id, month), 0))

    def add_usd(self, tenant_id: uuid.UUID, month: str, amount: Decimal) -> Decimal:
        with self._lock:
            key = _key(tenant_id, month)
            self._data[key] = self._data.get(key, 0) + _micro(amount)
            return _usd(self._data[key])

    def reserved_usd(self, tenant_id: uuid.UUID, month: str, now_ms: int) -> Decimal:
        with self._lock:
            held = self._prune(_key(tenant_id, month), now_ms)
            return _usd(sum(micro for micro, _ in held.values()))

    def reserve(
        self,
        tenant_id: uuid.UUID,
        month: str,
        reservation_id: str,
        amount: Decimal,
        limit: Decimal,
        *,
        now_ms: int,
        ttl_ms: int,
    ) -> bool:
        with self._lock:
            key = _key(tenant_id, month)
            held = self._prune(key, now_ms)
            spent = self._data.get(key, 0)
            reserved = sum(micro for micro, _ in held.values())
            if spent + reserved + _micro(amount) > _micro(limit):
                return False
            held[reservation_id] = (_micro(amount), now_ms + ttl_ms)
            return True

    def settle(
        self,
        tenant_id: uuid.UUID,
        month: str,
        reservation_id: str,
        actual: Decimal,
        *,
        now_ms: int,
        keep_ms: int,
    ) -> Settlement:
        with self._lock:
            key = _key(tenant_id, month)
            held = self._prune(key, now_ms)
            done = self._done[key]
            if reservation_id in done:
                return Settlement(_usd(self._data.get(key, 0)), "duplicate")
            state: SettleState = "settled" if held.pop(reservation_id, None) else "late"
            done[reservation_id] = now_ms + keep_ms
            self._data[key] = self._data.get(key, 0) + _micro(actual)
            return Settlement(_usd(self._data[key]), state)

    def mark_exhausted(self, tenant_id: uuid.UUID, month: str) -> bool:
        with self._lock:
            key = _exhausted_key(tenant_id, month)
            if key in self._exhausted:
                return False
            self._exhausted.add(key)
            return True


# KEYS: spend, held (hash id -> micro), expiry (zset id -> expires ms), done (zset id -> keep ms)
_PRUNE_LUA = """
local expired = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', ARGV[1])
for _, rid in ipairs(expired) do redis.call('HDEL', KEYS[2], rid) end
if #expired > 0 then redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', ARGV[1]) end
redis.call('ZREMRANGEBYSCORE', KEYS[4], '-inf', ARGV[1])
"""

# ARGV: now_ms, id, amount, limit, expires_ms, key_ttl_s -> 1 reserved, 0 refused
_RESERVE_LUA = (
    _PRUNE_LUA
    + """
local spent = tonumber(redis.call('GET', KEYS[1]) or '0')
local reserved = 0
for _, v in ipairs(redis.call('HVALS', KEYS[2])) do reserved = reserved + tonumber(v) end
if spent + reserved + tonumber(ARGV[3]) > tonumber(ARGV[4]) then return 0 end
redis.call('HSET', KEYS[2], ARGV[2], ARGV[3])
redis.call('ZADD', KEYS[3], ARGV[5], ARGV[2])
redis.call('EXPIRE', KEYS[2], ARGV[6])
redis.call('EXPIRE', KEYS[3], ARGV[6])
return 1
"""
)

# ARGV: now_ms, id, actual, keep_until_ms, key_ttl_s -> {state, total}
# state: 1 settled, 2 late (the reservation had lapsed), 0 duplicate
_SETTLE_LUA = (
    _PRUNE_LUA
    + """
if redis.call('ZSCORE', KEYS[4], ARGV[2]) then
  return {0, tonumber(redis.call('GET', KEYS[1]) or '0')}
end
local state = 2
if redis.call('HDEL', KEYS[2], ARGV[2]) == 1 then state = 1 end
redis.call('ZREM', KEYS[3], ARGV[2])
redis.call('ZADD', KEYS[4], ARGV[4], ARGV[2])
redis.call('EXPIRE', KEYS[4], ARGV[5])
local total = redis.call('INCRBY', KEYS[1], ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[5], 'NX')
return {state, total}
"""
)

_STATES: dict[int, SettleState] = {0: "duplicate", 1: "settled", 2: "late"}


class ValkeySpendLedger:
    """Shared across processes: an integer micro-USD counter per tenant and IST month, and the
    month's reservations (a hash of amounts and a sorted set of expiry times), changed only by
    the Lua scripts above so each step is atomic."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._reserve = client.register_script(_RESERVE_LUA)
        self._settle = client.register_script(_SETTLE_LUA)

    @staticmethod
    def _keys(tenant_id: uuid.UUID, month: str) -> list[str]:
        return [
            _key(tenant_id, month),
            _held_key(tenant_id, month),
            _expiry_key(tenant_id, month),
            _done_key(tenant_id, month),
        ]

    def spent_usd(self, tenant_id: uuid.UUID, month: str) -> Decimal:
        try:
            value = self._client.get(_key(tenant_id, month))
        except redis.RedisError as exc:
            raise KVUnavailable("valkey get failed") from exc
        return _usd(int(value or 0))

    def add_usd(self, tenant_id: uuid.UUID, month: str, amount: Decimal) -> Decimal:
        key = _key(tenant_id, month)
        try:
            pipe = self._client.pipeline()
            pipe.incrby(key, _micro(amount))
            pipe.expire(key, _SPEND_TTL_S, nx=True)
            total, _ = pipe.execute()
        except redis.RedisError as exc:
            raise KVUnavailable("valkey incrby failed") from exc
        return _usd(int(total))

    def reserved_usd(self, tenant_id: uuid.UUID, month: str, now_ms: int) -> Decimal:
        held, expiry = _held_key(tenant_id, month), _expiry_key(tenant_id, month)
        try:
            live = self._client.zrangebyscore(expiry, f"({now_ms}", "+inf")
            values = self._client.hmget(held, live) if live else []
        except redis.RedisError as exc:
            raise KVUnavailable("valkey read failed") from exc
        return _usd(sum(int(v) for v in values if v is not None))

    def reserve(
        self,
        tenant_id: uuid.UUID,
        month: str,
        reservation_id: str,
        amount: Decimal,
        limit: Decimal,
        *,
        now_ms: int,
        ttl_ms: int,
    ) -> bool:
        args = [now_ms, reservation_id, _micro(amount), _micro(limit), now_ms + ttl_ms]
        try:
            ok = self._reserve(keys=self._keys(tenant_id, month), args=[*args, _SPEND_TTL_S])
        except redis.RedisError as exc:
            raise KVUnavailable("valkey reserve failed") from exc
        return int(ok) == 1

    def settle(
        self,
        tenant_id: uuid.UUID,
        month: str,
        reservation_id: str,
        actual: Decimal,
        *,
        now_ms: int,
        keep_ms: int,
    ) -> Settlement:
        args = [now_ms, reservation_id, _micro(actual), now_ms + keep_ms, _SPEND_TTL_S]
        try:
            state, total = self._settle(keys=self._keys(tenant_id, month), args=args)
        except redis.RedisError as exc:
            raise KVUnavailable("valkey settle failed") from exc
        return Settlement(_usd(int(total)), _STATES[int(state)])

    def mark_exhausted(self, tenant_id: uuid.UUID, month: str) -> bool:
        """``SET key 1 NX EX`` (one atomic command on the primary): true for the first caller."""
        try:
            first = self._client.set(
                _exhausted_key(tenant_id, month), b"1", nx=True, ex=_SPEND_TTL_S
            )
        except redis.RedisError as exc:
            raise KVUnavailable("valkey set failed") from exc
        return bool(first)


# --- the guard ----------------------------------------------------------------------------------

BudgetLevel = Literal["ok", "alert", "exhausted"]


@dataclass(frozen=True, slots=True)
class SpendAfter:
    total_usd: Decimal
    level: BudgetLevel
    alert_crossed: bool
    """This call took the month's spend over the alert threshold (report once)."""
    applied: bool = True
    """False when the reservation had already been settled (nothing changed)."""


@dataclass(frozen=True, slots=True)
class Reservation:
    id: uuid.UUID
    tenant_id: uuid.UUID
    month: str
    """The IST month that admitted the call; its cost is recorded there."""
    estimate_usd: Decimal


@dataclass(frozen=True, slots=True)
class Admission:
    """A call the guard let through, with the budget it holds until settled or released."""

    settings: TenantAiSettings
    reservation: Reservation


_RESERVATION = "kb_budget_reservation"
DEFERRED_KEEP_S: Final = 2 * 24 * 3600
"""How long a deferred settlement's id is remembered (longer than the task's retries), so it is
never counted twice."""


class BudgetGuard:
    """Checks switches, reserves budget and applies the rate limit before a call; settles the
    reservation to the real cost after it."""

    def __init__(
        self,
        config: LlmConfig,
        policy: TenantAiPolicy,
        ledger: SpendLedger,
        counters: KVStore,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._policy = policy
        self._ledger = ledger
        self._counters = counters
        self._now = now

    def budget_usd(self, settings: TenantAiSettings) -> Decimal:
        return settings.monthly_budget_inr / self._config.budget.usd_inr_rate

    def estimate_usd(self, role_config: RoleConfig) -> Decimal:
        """Worst-case list price of one call of this role (no cache discount)."""
        price = self._config.prices[role_config.model]
        tokens_in = Decimal(self._config.budget.reservation.input_tokens)
        tokens_out = Decimal(role_config.max_output_tokens)
        return (
            tokens_in * price.input_usd_per_mtok + tokens_out * price.output_usd_per_mtok
        ) / _MICRO

    def _level(self, spent: Decimal, budget: Decimal) -> BudgetLevel:
        b = self._config.budget
        if budget <= 0 or spent >= budget * Decimal(str(b.degrade_fraction)):
            return "exhausted"
        if spent >= budget * Decimal(str(b.alert_fraction)):
            return "alert"
        return "ok"

    def _now_ms(self) -> int:
        return int(self._now().timestamp() * 1000)

    def _ttl_ms(self) -> int:
        return self._config.budget.reservation.ttl_s * 1000

    def admit(self, tenant_id: uuid.UUID, feature: Feature, role_config: RoleConfig) -> Admission:
        """Reserve this call's estimate, or raise :class:`AiDisabled`,
        :class:`BudgetExhausted`, :class:`AiRateLimited` or :class:`ProviderUnavailable`."""
        settings = self._policy.settings_for(tenant_id)
        if not settings.ai_enabled:
            raise AiDisabled("AI features are switched off for this school")
        now = self._now()
        limit = self.budget_usd(settings) * Decimal(str(self._config.budget.degrade_fraction))
        reservation = Reservation(
            new_id(), tenant_id, budget_month(now), self.estimate_usd(role_config)
        )
        if limit <= 0:
            raise BudgetExhausted("This month's AI budget is used up")
        try:
            held = self._ledger.reserve(
                tenant_id,
                reservation.month,
                str(reservation.id),
                reservation.estimate_usd,
                limit,
                now_ms=int(now.timestamp() * 1000),
                ttl_ms=self._ttl_ms(),
            )
        except KVUnavailable as exc:
            # Fail closed: without the month's spend the budget cannot be enforced.
            raise ProviderUnavailable("AI answers are temporarily unavailable") from exc
        if not held:
            self._report_exhausted(tenant_id, reservation.month)
            raise BudgetExhausted("This month's AI budget is used up")
        admission = Admission(settings, reservation)
        try:
            self._rate_limit(tenant_id, feature)
        except BaseException:
            self.release(admission)
            raise
        return admission

    def _report_exhausted(self, tenant_id: uuid.UUID, month: str) -> None:
        """NFR-CST-001 100 % alert (owner decision 2026-10-03): the first reservation refused
        for budget in the school's IST month, reported once through the 80 % alert's event.
        Never raises: the refusal stands even when the alert cannot be recorded."""
        try:
            first = self._ledger.mark_exhausted(tenant_id, month)
        except KVUnavailable:
            log.error("kb.budget.alert_unrecorded", tenant_id=tenant_id, action="exhausted")
            return
        if first:
            log.warning("kb.budget.alert_crossed", tenant_id=tenant_id, action="exhausted")

    def _rate_limit(self, tenant_id: uuid.UUID, feature: Feature) -> None:
        minute = self._now().strftime("%Y%m%d%H%M")
        key = f"sos:rl:kb:{tenant_id}:{feature}:{minute}"
        try:
            count = self._counters.incr(key, ttl_s=120)
        except KVUnavailable:
            return  # fail open: the monthly budget still caps spend (docs/07 T15)
        if count > self._config.rate_limit.requests_per_minute_per_tenant:
            raise AiRateLimited("Too many AI requests. Wait a minute and try again.")

    def settle(
        self, admission: Admission, cost_usd: Decimal, *, keep_ms: int | None = None
    ) -> SpendAfter:
        """Turn the reservation into the real cost (idempotent). Raises :class:`KVUnavailable`
        when the store is down (the gateway then queues :meth:`settle_deferred`)."""
        r = admission.reservation
        done = self._ledger.settle(
            r.tenant_id,
            r.month,
            str(r.id),
            cost_usd,
            now_ms=self._now_ms(),
            keep_ms=max(self._ttl_ms(), keep_ms or 0),
        )
        ids = {"tenant_id": r.tenant_id, "resource_type": _RESERVATION, "resource_id": r.id}
        applied = done.state != "duplicate"
        if done.state == "late":
            log.warning("kb.budget.settled_late", **ids)
        if applied and cost_usd > r.estimate_usd:
            log.info("kb.budget.over_estimate", **ids, count=_micro(cost_usd - r.estimate_usd))
        budget = self.budget_usd(admission.settings)
        level = self._level(done.total_usd, budget)
        before = self._level(done.total_usd - cost_usd, budget) if applied else level
        return SpendAfter(
            done.total_usd, level, alert_crossed=before == "ok" and level != "ok", applied=applied
        )

    def settle_deferred(
        self, tenant_id: uuid.UUID, reservation_id: uuid.UUID, month: str, cost_usd: Decimal
    ) -> SpendAfter:
        """Settle a billed call whose settlement the store refused at the time (worker task
        ``knowledge.settle_spend``, audit W3-10). The same reservation id, so a retry or a
        settlement that did land before the error counts once (the ledger remembers the id for
        :data:`DEFERRED_KEEP_S`). Raises :class:`KVUnavailable` while the store is down (the
        task retries). Reports the alert crossing like a settlement on the call's path."""
        reservation = Reservation(reservation_id, tenant_id, month, cost_usd)
        admission = Admission(self._policy.settings_for(tenant_id), reservation)
        after = self.settle(admission, cost_usd, keep_ms=DEFERRED_KEEP_S * 1000)
        if after.alert_crossed:
            log.warning("kb.budget.alert_crossed", tenant_id=tenant_id, action=after.level)
        return after

    def release(self, admission: Admission) -> None:
        """Free a reservation nothing was billed for; a no-op once settled. Never raises for the
        store: an unreleased reservation lapses on its TTL."""
        r = admission.reservation
        try:
            self._ledger.settle(
                r.tenant_id,
                r.month,
                str(r.id),
                Decimal(0),
                now_ms=self._now_ms(),
                keep_ms=self._ttl_ms(),
            )
        except KVUnavailable:
            log.error(
                "kb.budget.release_failed",
                tenant_id=r.tenant_id,
                resource_type=_RESERVATION,
                resource_id=r.id,
            )


__all__ = [
    "DEFERRED_KEEP_S",
    "IST",
    "Admission",
    "BudgetGuard",
    "BudgetLevel",
    "InMemorySpendLedger",
    "Reservation",
    "Settlement",
    "SpendAfter",
    "SpendLedger",
    "StaticAiPolicy",
    "TenantAiPolicy",
    "TenantAiSettings",
    "ValkeySpendLedger",
    "budget_month",
    "bundle_budget_inr",
]
