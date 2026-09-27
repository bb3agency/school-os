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
NFR-CST-001). A call already running when the budget runs out still completes and is metered,
so spend can overshoot by at most one call's cost.

Stores: :class:`SpendLedger` and the rate-limit counters live in Valkey in staging/prod
(shared by every API and worker process) and in memory locally (``authz.kv`` pattern).
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable

import redis

from app.authz.kv import KVStore, KVUnavailable
from app.knowledge.config.llm import LlmConfig
from app.knowledge.domain import Feature
from app.knowledge.gateway.errors import (
    AiDisabled,
    AiRateLimited,
    BudgetExhausted,
    ProviderUnavailable,
)

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


class SpendLedger(Protocol):
    def spent_usd(self, tenant_id: uuid.UUID, month: str) -> Decimal: ...

    def add_usd(self, tenant_id: uuid.UUID, month: str, amount: Decimal) -> Decimal:
        """Add ``amount`` and return the new month total."""
        ...


def _key(tenant_id: uuid.UUID, month: str) -> str:
    return f"sos:kb:spend:{tenant_id}:{month}"


def _micro(amount: Decimal) -> int:
    return int((amount * _MICRO).to_integral_value())


class InMemorySpendLedger:
    """Per process (local/CI and tests)."""

    def __init__(self) -> None:
        self._data: dict[str, int] = {}
        self._lock = threading.Lock()

    def spent_usd(self, tenant_id: uuid.UUID, month: str) -> Decimal:
        with self._lock:
            return Decimal(self._data.get(_key(tenant_id, month), 0)) / _MICRO

    def add_usd(self, tenant_id: uuid.UUID, month: str, amount: Decimal) -> Decimal:
        with self._lock:
            key = _key(tenant_id, month)
            self._data[key] = self._data.get(key, 0) + _micro(amount)
            return Decimal(self._data[key]) / _MICRO


class ValkeySpendLedger:
    """Shared across processes: an integer micro-USD counter per tenant and IST month."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def spent_usd(self, tenant_id: uuid.UUID, month: str) -> Decimal:
        try:
            value = self._client.get(_key(tenant_id, month))
        except redis.RedisError as exc:
            raise KVUnavailable("valkey get failed") from exc
        return Decimal(int(value or 0)) / _MICRO

    def add_usd(self, tenant_id: uuid.UUID, month: str, amount: Decimal) -> Decimal:
        key = _key(tenant_id, month)
        try:
            pipe = self._client.pipeline()
            pipe.incrby(key, _micro(amount))
            pipe.expire(key, _SPEND_TTL_S, nx=True)
            total, _ = pipe.execute()
        except redis.RedisError as exc:
            raise KVUnavailable("valkey incrby failed") from exc
        return Decimal(int(total)) / _MICRO


# --- the guard ----------------------------------------------------------------------------------

BudgetLevel = Literal["ok", "alert", "exhausted"]


@dataclass(frozen=True, slots=True)
class SpendAfter:
    total_usd: Decimal
    level: BudgetLevel
    alert_crossed: bool
    """This call took the month's spend over the alert threshold (report once)."""


class BudgetGuard:
    """Checks switches, budget and rate limit before a call; records spend after it."""

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

    def _level(self, spent: Decimal, budget: Decimal) -> BudgetLevel:
        b = self._config.budget
        if budget <= 0 or spent >= budget * Decimal(str(b.degrade_fraction)):
            return "exhausted"
        if spent >= budget * Decimal(str(b.alert_fraction)):
            return "alert"
        return "ok"

    def check(self, tenant_id: uuid.UUID, feature: Feature) -> TenantAiSettings:
        """Raise :class:`AiDisabled`, :class:`BudgetExhausted` or :class:`AiRateLimited`."""
        settings = self._policy.settings_for(tenant_id)
        if not settings.ai_enabled:
            raise AiDisabled("AI features are switched off for this school")
        try:
            spent = self._ledger.spent_usd(tenant_id, budget_month(self._now()))
        except KVUnavailable as exc:
            # Fail closed: without the month's spend the budget cannot be enforced.
            raise ProviderUnavailable("AI answers are temporarily unavailable") from exc
        if self._level(spent, self.budget_usd(settings)) == "exhausted":
            raise BudgetExhausted("This month's AI budget is used up")
        self._rate_limit(tenant_id, feature)
        return settings

    def _rate_limit(self, tenant_id: uuid.UUID, feature: Feature) -> None:
        minute = self._now().strftime("%Y%m%d%H%M")
        key = f"sos:rl:kb:{tenant_id}:{feature}:{minute}"
        try:
            count = self._counters.incr(key, ttl_s=120)
        except KVUnavailable:
            return  # fail open: the monthly budget still caps spend (docs/07 T15)
        if count > self._config.rate_limit.requests_per_minute_per_tenant:
            raise AiRateLimited("Too many AI requests. Wait a minute and try again.")

    def record(
        self, tenant_id: uuid.UUID, settings: TenantAiSettings, cost_usd: Decimal
    ) -> SpendAfter:
        month = budget_month(self._now())
        total = self._ledger.add_usd(tenant_id, month, cost_usd)
        budget = self.budget_usd(settings)
        level = self._level(total, budget)
        before = self._level(total - cost_usd, budget)
        return SpendAfter(total, level, alert_crossed=before == "ok" and level != "ok")


__all__ = [
    "IST",
    "BudgetGuard",
    "BudgetLevel",
    "InMemorySpendLedger",
    "SpendAfter",
    "SpendLedger",
    "StaticAiPolicy",
    "TenantAiPolicy",
    "TenantAiSettings",
    "ValkeySpendLedger",
    "budget_month",
]
