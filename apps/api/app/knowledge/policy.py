"""The school's AI switch and budget, and where metering goes (composition-root adapters).

- :class:`SchoolAiPolicy` implements the gateway's :class:`TenantAiPolicy` (FR-KB-011, owner
  decision 2026-09-27): ``ai_enabled`` = the school's ``ai_features_enabled`` setting AND the
  per-school feature flag ``kb.ask.enabled``; the budget is derived from the school's AI answer
  bundle when it has one (owner decision 2026-10-03, ``models.yaml`` ``budget.bundle``), else
  its ``ai_monthly_budget_inr`` setting. Settings
  are read through ``tenancy.service`` in a short ``tenant_session`` of that school; the flag
  through :mod:`app.core.feature_flags` (``sos_app`` may read ``platform.feature_flags``;
  knowledge never imports the control plane). Answers are cached per school for a few seconds
  so the several model calls of one question read the database once; a switch-off therefore
  takes effect within :data:`POLICY_TTL_S`.
- :class:`LedgerMeteringSink` implements the gateway's :class:`MeteringSink`: every
  :class:`MeteringEvent` becomes a ``kb.llm_calls`` row (0024_kb_metering) in its own short
  transaction (the spend happened even if the question's transaction later fails). The ask loop
  totals tokens for ``kb.queries`` from the turns themselves. A failed write is logged with ids
  only and never breaks the answer.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from sqlalchemy.exc import SQLAlchemyError

from app.core import feature_flags
from app.core.db import tenant_session
from app.core.logging import get_logger
from app.knowledge import repository as repo
from app.knowledge.config.llm import LlmConfig, load_llm_config
from app.knowledge.gateway.budget import TenantAiSettings, bundle_budget_inr
from app.knowledge.gateway.metering import MeteringEvent
from app.ops import service as ops
from app.tenancy import service as tenancy

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

ASK_FLAG: Final = "kb.ask.enabled"
SETTLE_EVENT: Final = "kb.budget.settle_requested"
"""Outbox event: settle a billed call whose settlement the spend store refused (audit W3-10)."""
SETTLE_TASK: Final = "knowledge.settle_spend"
_MICRO_USD: Final = Decimal(1_000_000)
POLICY_TTL_S: Final = 15.0

SessionFactory = Callable[[uuid.UUID], AbstractContextManager["Session"]]


def _session(tenant_id: uuid.UUID) -> AbstractContextManager[Session]:
    return tenant_session(tenant_id)


def read_ai_settings(
    session: Session, tenant_id: uuid.UUID, *, config: LlmConfig | None = None
) -> TenantAiSettings:
    """The school's switch and budget, read in ``session`` (a tenant_session of the school).

    Budget (owner decision 2026-10-03): with an AI answer bundle (its included answers, handed
    to the school by the control plane through ``tenancy.set_ai_answer_allowance``), the budget
    is derived from it (:func:`bundle_budget_inr`); without one, the school's own
    ``ai_monthly_budget_inr`` setting applies."""
    school = tenancy.get_tenant(session).settings
    flag = feature_flags.is_enabled(ASK_FLAG, tenant_id, session=session)
    answers = tenancy.ai_answer_allowance(session)
    budget = (
        Decimal(school.ai_monthly_budget_inr)
        if answers is None
        else bundle_budget_inr(config or load_llm_config(), answers)
    )
    return TenantAiSettings(
        ai_enabled=bool(school.ai_features_enabled) and flag,
        monthly_budget_inr=budget,
    )


class SchoolAiPolicy:
    """:class:`~app.knowledge.gateway.budget.TenantAiPolicy` from settings + feature flag."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory = _session,
        ttl_s: float = POLICY_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session = session_factory
        self._ttl = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[uuid.UUID, tuple[float, TenantAiSettings]] = {}

    def settings_for(self, tenant_id: uuid.UUID) -> TenantAiSettings:
        now = self._clock()
        with self._lock:
            hit = self._cache.get(tenant_id)
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        with self._session(tenant_id) as s:
            settings = read_ai_settings(s, tenant_id)
        with self._lock:
            self._cache[tenant_id] = (now, settings)
        return settings

    def forget(self, tenant_id: uuid.UUID | None = None) -> None:
        with self._lock:
            if tenant_id is None:
                self._cache.clear()
            else:
                self._cache.pop(tenant_id, None)


class LedgerMeteringSink:
    """:class:`~app.knowledge.gateway.metering.MeteringSink` writing ``kb.llm_calls``; a call
    whose settlement the spend store refused (``event.unsettled``) also queues
    :data:`SETTLE_EVENT` in the same transaction (ids and the cost in micro-USD only)."""

    def __init__(self, *, session_factory: SessionFactory = _session) -> None:
        self._session = session_factory

    def record(self, event: MeteringEvent) -> None:
        try:
            with self._session(event.tenant_id) as s:
                repo.insert_llm_call(
                    s,
                    {
                        "feature": event.feature,
                        "role": event.role,
                        "query_id": event.query_id,
                        "document_id": event.document_id,
                        "provider": event.provider,
                        "model": event.model,
                        "outcome": event.outcome,
                        "attempts": event.attempts,
                        "latency_ms": event.latency_ms,
                        "input_tokens": event.input_tokens,
                        "output_tokens": event.output_tokens,
                        "cache_write_tokens": event.cache_write_tokens,
                        "cache_read_tokens": event.cache_read_tokens,
                        "cost_usd": event.cost_usd,
                    },
                )
                if event.unsettled is not None:
                    ops.enqueue_event(
                        s,
                        SETTLE_EVENT,
                        {
                            "reservation_id": event.unsettled.reservation_id,
                            "month": event.unsettled.month,
                            "cost_micro_usd": int(event.cost_usd * _MICRO_USD),
                        },
                    )
        except (SQLAlchemyError, ValueError) as exc:  # ValueError: a payload refused
            log.error(
                "kb.metering.unrecorded",
                tenant_id=event.tenant_id,
                error_type=type(exc).__name__,
                action=event.role,
            )


__all__ = [
    "ASK_FLAG",
    "POLICY_TTL_S",
    "SETTLE_EVENT",
    "SETTLE_TASK",
    "LedgerMeteringSink",
    "SchoolAiPolicy",
    "read_ai_settings",
]
