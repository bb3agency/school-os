"""A provider call's cost is never lost when the spend store flaps (audit 2026-10-04 W3-10).

Before: when ``settle`` raised ``KVUnavailable`` after a successful provider call, the
reservation lapsed on its TTL and the real cost was never added to the school's month (a Valkey
flap under-counted spend, FR-KB-011, NFR-CST-001). Now the gateway marks the metering event
``unsettled``; the metering sink writes the ``kb.llm_calls`` row and an outbox event in one
transaction; the worker task ``knowledge.settle_spend`` settles the same reservation id later
(idempotent in the ledger) and retries while the store is down. Ids and numbers only.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine, text
from structlog.testing import capture_logs

from app.authz.kv import InMemoryKV, KVUnavailable
from app.core.db import tenant_session
from app.core.logging import ALLOWED_FIELDS
from app.knowledge import composition, policy, service, tasks
from app.knowledge.config.llm import load_llm_config
from app.knowledge.config.tools import load_tools_config
from app.knowledge.domain import Metering, UserMessage
from app.knowledge.gateway.budget import (
    BudgetGuard,
    InMemorySpendLedger,
    StaticAiPolicy,
    TenantAiSettings,
    budget_month,
)
from app.knowledge.gateway.gateway import Gateway
from app.knowledge.gateway.metering import MeteringEvent, RecordingSink, UnsettledSpend
from app.knowledge.gateway.transport import MessagesRequest
from app.knowledge.policy import LedgerMeteringSink

TENANT = uuid.UUID("0192f000-0000-7000-8000-0000000000c1")
NOW = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)
MONTH = budget_month(NOW)
QUESTION = "Synthetic question about the sports day circular of Synthetic Vidyalaya?"
CONFIG = load_llm_config().use_fallback()


class Answering:
    name = "answering"

    def send(self, request: MessagesRequest) -> Mapping[str, Any]:
        return {
            "model": "claude-sonnet-5",
            "content": [{"type": "text", "text": "Sports day is on 12/10/2026."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1000, "output_tokens": 50},
        }


class SettleDown(InMemorySpendLedger):
    """Reserves fine, then the store flaps before the settlement."""

    down = True

    def settle(self, *args: Any, **kwargs: Any) -> Any:
        if self.down:
            raise KVUnavailable("synthetic outage at 10.0.0.9")
        return super().settle(*args, **kwargs)


def guard(ledger: InMemorySpendLedger) -> BudgetGuard:
    return BudgetGuard(
        CONFIG,
        StaticAiPolicy(TenantAiSettings(True, Decimal(100_000))),
        ledger,
        InMemoryKV(),
        now=lambda: NOW,
    )


def run_call(ledger: InMemorySpendLedger) -> MeteringEvent:
    sink = RecordingSink()
    Gateway(
        config=CONFIG,
        tools_config=load_tools_config(),
        transport=Answering(),
        guard=guard(ledger),
        sink=sink,
        enabled=lambda: True,
        sleep=lambda _s: None,
    ).run_turn(Metering(TENANT, "ask"), "answer", "sys " + QUESTION, [UserMessage(QUESTION)], [])
    (event,) = sink.events
    return event


def test_W3_10_a_failed_settlement_is_handed_on_not_dropped() -> None:
    ledger = SettleDown()
    with capture_logs() as logs:
        event = run_call(ledger)
    assert event.cost_usd > 0
    assert event.month_spend_usd is None
    assert isinstance(event.unsettled, UnsettledSpend)
    assert event.unsettled.month == MONTH
    assert ledger.spent_usd(TENANT, MONTH) == 0  # not recorded yet
    (line,) = [e for e in logs if e["event"] == "kb.budget.settle_deferred"]
    assert set(line) - {"event", "log_level"} <= set(ALLOWED_FIELDS)
    assert "10.0.0.9" not in json.dumps(logs, default=str)


def test_W3_10_a_settled_call_carries_no_pending_settlement() -> None:
    event = run_call(InMemorySpendLedger())
    assert event.unsettled is None
    assert event.month_spend_usd == event.cost_usd


def test_W3_10_the_deferred_settlement_adds_the_cost_once() -> None:
    ledger = SettleDown()
    event = run_call(ledger)
    assert event.unsettled is not None
    ledger.down = False
    g = guard(ledger)
    first = g.settle_deferred(
        TENANT, event.unsettled.reservation_id, event.unsettled.month, event.cost_usd
    )
    assert first.applied
    assert ledger.spent_usd(TENANT, MONTH) == event.cost_usd
    again = g.settle_deferred(
        TENANT, event.unsettled.reservation_id, event.unsettled.month, event.cost_usd
    )
    assert not again.applied  # a retried task never counts the cost twice
    assert ledger.spent_usd(TENANT, MONTH) == event.cost_usd


def test_W3_10_the_task_retries_while_the_store_is_still_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SettleDown()
    monkeypatch.setattr(composition, "runtime", lambda: SimpleNamespace(budget=guard(ledger)))
    payload = {
        "reservation_id": str(uuid.uuid4()),
        "month": MONTH,
        "cost_micro_usd": 1234,
    }
    # Called directly (no worker), Task.retry re-raises the error: the worker would retry.
    with pytest.raises(KVUnavailable):
        tasks.settle_spend.run(str(TENANT), str(uuid.uuid4()), payload)
    ledger.down = False
    assert tasks.settle_spend.run(str(TENANT), str(uuid.uuid4()), payload) == "settled"
    assert ledger.spent_usd(TENANT, MONTH) == Decimal("0.001234")
    assert tasks.settle_spend.run(str(TENANT), str(uuid.uuid4()), payload) == "duplicate"
    assert ledger.spent_usd(TENANT, MONTH) == Decimal("0.001234")


@pytest.mark.parametrize(
    "payload",
    [
        {"reservation_id": "not-a-uuid", "month": MONTH, "cost_micro_usd": 1},
        {"reservation_id": str(uuid.UUID(int=1)), "month": "October", "cost_micro_usd": 1},
        {"reservation_id": str(uuid.UUID(int=1)), "month": MONTH, "cost_micro_usd": -5},
        {"reservation_id": str(uuid.UUID(int=1)), "month": MONTH},
    ],
)
def test_W3_10_a_malformed_payload_is_refused_without_touching_the_ledger(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    ledger = InMemorySpendLedger()
    monkeypatch.setattr(composition, "runtime", lambda: SimpleNamespace(budget=guard(ledger)))
    assert service.settle_spend(TENANT, payload) == "invalid"
    assert ledger.spent_usd(TENANT, MONTH) == 0


def test_W3_10_the_outbox_route_is_registered() -> None:
    from app.ops import service as ops

    assert ops.OUTBOX_ROUTES[policy.SETTLE_EVENT] == tasks.SETTLE_TASK


@pytest.mark.db
def test_W3_10_the_sink_queues_the_settlement_with_the_metering_row(
    admin_engine: Engine, app_engine: Engine
) -> None:
    tid = uuid.uuid4()
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'S', 'active')"
            ),
            {"i": tid, "c": f"w310-{tid.hex[:10]}"},
        )
    reservation = uuid.uuid4()
    LedgerMeteringSink().record(
        MeteringEvent(
            tenant_id=tid,
            feature="ask",
            role="answer",
            query_id=None,
            provider="fake",
            model="claude-sonnet-5",
            outcome="ok",
            attempts=1,
            latency_ms=12,
            input_tokens=100,
            output_tokens=20,
            cache_write_tokens=0,
            cache_read_tokens=0,
            cost_usd=Decimal("0.000400"),
            month_spend_usd=None,
            unsettled=UnsettledSpend(reservation, MONTH),
        )
    )
    with tenant_session(tid) as s:
        calls = s.execute(text("SELECT count(*) FROM kb.llm_calls")).scalar_one()
        rows = s.execute(
            text("SELECT event_type, payload FROM ops.outbox WHERE event_type = :e"),
            {"e": policy.SETTLE_EVENT},
        ).all()
    assert calls == 1
    assert [(r.event_type, r.payload) for r in rows] == [
        (
            policy.SETTLE_EVENT,
            {"reservation_id": str(reservation), "month": MONTH, "cost_micro_usd": 400},
        )
    ]
