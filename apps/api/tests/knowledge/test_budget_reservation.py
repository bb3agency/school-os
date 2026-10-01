"""Atomic budget reservations before every provider call (FR-KB-011, NFR-CST-001).

The gateway reserves each call's worst-case cost (list price of ``budget.reservation.input_tokens``
plus the role's ``max_output_tokens``, ``models.yaml``) before the call and settles it to the real
cost afterwards, so concurrent questions near a school's monthly budget can never all pass the
check and overshoot it together. Every ledger test runs against the in-memory ledger and a real
Valkey (testcontainers, ``redis`` marker). Synthetic tenants and text only.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import redis
from structlog.testing import capture_logs

from app.authz.kv import InMemoryKV, KVUnavailable
from app.core.logging import ALLOWED_FIELDS
from app.knowledge.config.llm import LlmConfig, load_llm_config
from app.knowledge.config.tools import load_tools_config
from app.knowledge.domain import Metering, UserMessage
from app.knowledge.gateway.budget import (
    Admission,
    BudgetGuard,
    InMemorySpendLedger,
    SpendLedger,
    StaticAiPolicy,
    TenantAiSettings,
    ValkeySpendLedger,
)
from app.knowledge.gateway.errors import (
    AiRateLimited,
    BudgetExhausted,
    ProviderUnavailable,
)
from app.knowledge.gateway.gateway import Gateway
from app.knowledge.gateway.metering import RecordingSink
from app.knowledge.gateway.transport import MessagesRequest, TransportError

TENANT = uuid.UUID("0192f000-0000-7000-8000-0000000000b1")
OTHER_TENANT = uuid.UUID("0192f000-0000-7000-8000-0000000000b2")
NOW = datetime(2026, 9, 15, 6, 0, tzinfo=UTC)
SEPT, OCT = "2026-09", "2026-10"
QUESTION = "Synthetic question about the class 7 picnic circular for Synthetic Vidyalaya?"
ANSWER_TEXT = "The synthetic picnic circular says buses leave at 08:00."
VALKEY_IMAGE = "valkey/valkey:8.1.10-alpine"  # the deploy/dedicated/compose.yaml image


def config(*, rate_limit: int = 10_000) -> LlmConfig:
    """``models.yaml`` with every role on its Anthropic fallback (the scripted transport speaks
    the Messages API) and, unless a test is about it, a rate limit out of the way."""
    base = load_llm_config().use_fallback()
    limits = base.rate_limit.model_copy(update={"requests_per_minute_per_tenant": rate_limit})
    return base.model_copy(update={"rate_limit": limits})


# --- ledgers ------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def valkey_client() -> Iterator[Any]:
    from testcontainers.community.valkey import ValkeyContainer

    with ValkeyContainer(VALKEY_IMAGE) as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port()
        client = redis.Redis(host=host, port=port, socket_timeout=5)
        yield client
        client.close()


@pytest.fixture(
    params=[pytest.param("memory"), pytest.param("valkey", marks=pytest.mark.redis)],
)
def ledger(request: pytest.FixtureRequest) -> SpendLedger:
    if request.param == "memory":
        return InMemorySpendLedger()
    client = request.getfixturevalue("valkey_client")
    client.flushdb()
    return ValkeySpendLedger(client)


class Now:
    def __init__(self, at: datetime = NOW) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def guard(
    ledger: SpendLedger,
    *,
    budget_inr: int = 84,  # 84 INR at 84.00 = $1.00
    now: Now | None = None,
    cfg: LlmConfig | None = None,
    counters: InMemoryKV | None = None,
) -> BudgetGuard:
    return BudgetGuard(
        cfg or config(),
        StaticAiPolicy(TenantAiSettings(True, Decimal(budget_inr))),
        ledger,
        counters or InMemoryKV(),
        now=now or Now(),
    )


def estimate(cfg: LlmConfig | None = None, role: str = "answer") -> Decimal:
    cfg = cfg or config()
    return guard(InMemorySpendLedger(), cfg=cfg).estimate_usd(cfg.roles[role])  # type: ignore[index]


def admit(g: BudgetGuard, tenant: uuid.UUID = TENANT, role: str = "answer") -> Admission:
    return g.admit(tenant, "ask", config().roles[role])  # type: ignore[index]


# --- the estimate comes from config -------------------------------------------------------------


def test_FR_KB_011_estimate_is_list_price_of_reserved_input_and_max_output() -> None:
    cfg = config()
    role = cfg.roles["answer"]
    price = cfg.prices[role.model]
    tokens_in = cfg.budget.reservation.input_tokens
    expected = (
        Decimal(tokens_in) * price.input_usd_per_mtok
        + Decimal(role.max_output_tokens) * price.output_usd_per_mtok
    ) / Decimal(1_000_000)
    assert estimate(cfg) == expected
    assert expected > 0
    # A cheaper role reserves less (router: small tier, 300 output tokens).
    assert estimate(cfg, "router") < expected


def test_FR_KB_011_reservation_ttl_outlives_the_longest_call() -> None:
    cfg = load_llm_config()
    client = cfg.client
    worst = client.request_timeout_s * (client.max_retries + 1)
    worst += client.backoff_max_s * client.max_retries
    assert cfg.budget.reservation.ttl_s > worst


# --- concurrency at the boundary ----------------------------------------------------------------


def test_FR_KB_011_parallel_requests_at_the_boundary_never_exceed_the_budget(
    ledger: SpendLedger,
) -> None:
    est = estimate()
    # Room for exactly three worst-case calls (and a half).
    ledger.add_usd(TENANT, SEPT, Decimal(1) - est * Decimal("3.5"))
    g = guard(ledger)
    start = threading.Barrier(24)

    def one(_: int) -> Admission | None:
        start.wait(timeout=10)
        try:
            return admit(g)
        except BudgetExhausted:
            return None

    with ThreadPoolExecutor(max_workers=24) as pool:
        results = list(pool.map(one, range(24)))
    admitted = [a for a in results if a is not None]
    assert len(admitted) == 3
    now_ms = int(NOW.timestamp() * 1000)
    held = ledger.spent_usd(TENANT, SEPT) + ledger.reserved_usd(TENANT, SEPT, now_ms)
    assert held <= Decimal(1)
    for a in admitted:  # every call really costs the worst case
        g.settle(a, est)
    assert ledger.spent_usd(TENANT, SEPT) <= Decimal(1)
    assert ledger.reserved_usd(TENANT, SEPT, now_ms) == 0
    # Another school's budget is untouched.
    assert ledger.spent_usd(OTHER_TENANT, SEPT) == 0
    admit(g, OTHER_TENANT)


class GatedTransport:
    """Holds every call until the test opens the gate, then answers with worst-case usage."""

    name = "gated"

    def __init__(self, usage: Mapping[str, int]) -> None:
        self.usage = dict(usage)
        self.gate = threading.Event()
        self.sent = 0
        self._lock = threading.Lock()

    def send(self, request: MessagesRequest) -> Mapping[str, Any]:
        with self._lock:
            self.sent += 1
        assert self.gate.wait(timeout=10)
        return {
            "model": "claude-sonnet-5",
            "content": [{"type": "text", "text": ANSWER_TEXT}],
            "stop_reason": "end_turn",
            "usage": self.usage,
        }


def gateway(g: BudgetGuard, transport: Any, cfg: LlmConfig | None = None) -> Gateway:
    return Gateway(
        config=cfg or config(),
        tools_config=load_tools_config(),
        transport=transport,
        guard=g,
        sink=RecordingSink(),
        enabled=lambda: True,
        sleep=lambda _s: None,
    )


def test_FR_KB_011_concurrent_questions_through_the_gateway_stay_within_budget() -> None:
    """Before reservations every question passed the check while none had recorded spend yet,
    so twelve concurrent worst-case calls overshot a nearly used budget twelve times over."""
    cfg = config()
    ledger = InMemorySpendLedger()
    est = estimate(cfg)
    ledger.add_usd(TENANT, SEPT, Decimal(1) - est * Decimal("2.5"))
    g = guard(ledger, cfg=cfg)
    worst = {
        "input_tokens": cfg.budget.reservation.input_tokens,
        "output_tokens": cfg.roles["answer"].max_output_tokens,
    }
    transport = GatedTransport(worst)
    gw = gateway(g, transport, cfg)
    refused = []
    lock = threading.Lock()

    def one() -> None:
        try:
            gw.run_turn(Metering(TENANT, "ask"), "answer", "sys", [UserMessage(QUESTION)], [])
        except BudgetExhausted:
            with lock:
                refused.append(1)

    threads = [threading.Thread(target=one) for _ in range(12)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 10
    while transport.sent + len(refused) < 12 and time.monotonic() < deadline:
        time.sleep(0.01)
    transport.gate.set()
    for t in threads:
        t.join(timeout=10)
    assert transport.sent == 2
    assert len(refused) == 10
    assert ledger.spent_usd(TENANT, SEPT) <= Decimal(1)


def test_SEC_020_rate_limit_is_atomic_under_concurrency() -> None:
    """The per-minute limit is an atomic increment-then-compare, not check-then-act."""
    ledger = InMemorySpendLedger()
    g = guard(ledger, budget_inr=840_000, cfg=config(rate_limit=30))
    start = threading.Barrier(60)

    def one(_: int) -> bool:
        start.wait(timeout=10)
        try:
            a = admit(g)
        except AiRateLimited:
            return False
        g.release(a)
        return True

    with ThreadPoolExecutor(max_workers=60) as pool:
        results = list(pool.map(one, range(60)))
    assert sum(results) == 30
    # A refused (rate-limited) call does not keep its reservation.
    assert ledger.reserved_usd(TENANT, SEPT, int(NOW.timestamp() * 1000)) == 0


# --- settle, release, TTL, idempotency ----------------------------------------------------------


def test_FR_KB_011_settle_lower_than_the_estimate_records_the_real_cost(
    ledger: SpendLedger,
) -> None:
    g = guard(ledger)
    a = admit(g)
    after = g.settle(a, Decimal("0.004"))
    assert after.applied
    assert after.total_usd == Decimal("0.004")
    assert ledger.spent_usd(TENANT, SEPT) == Decimal("0.004")
    assert ledger.reserved_usd(TENANT, SEPT, int(NOW.timestamp() * 1000)) == 0


def test_FR_KB_011_settle_higher_than_the_estimate_records_the_real_cost(
    ledger: SpendLedger,
) -> None:
    g = guard(ledger)
    a = admit(g)
    actual = a.reservation.estimate_usd * 3
    with capture_logs() as logs:
        after = g.settle(a, actual)
    assert after.total_usd == actual
    assert ledger.spent_usd(TENANT, SEPT) == actual
    over = [e for e in logs if e["event"] == "kb.budget.over_estimate"]
    assert len(over) == 1
    assert over[0]["tenant_id"] == TENANT


def test_FR_KB_011_settle_reports_the_alert_and_exhausted_levels(ledger: SpendLedger) -> None:
    g = guard(ledger)
    ledger.add_usd(TENANT, SEPT, Decimal("0.79"))
    after = g.settle(admit(g), Decimal("0.02"))
    assert (after.level, after.alert_crossed) == ("alert", True)
    after = g.settle(admit(g), Decimal("0.01"))
    assert (after.level, after.alert_crossed) == ("alert", False)
    ledger.add_usd(TENANT, SEPT, Decimal("0.18"))
    with pytest.raises(BudgetExhausted):
        admit(g)


def test_FR_KB_011_release_on_error_frees_the_reservation(ledger: SpendLedger) -> None:
    est = estimate()
    ledger.add_usd(TENANT, SEPT, Decimal(1) - est * Decimal("1.5"))
    g = guard(ledger)
    a = admit(g)
    with pytest.raises(BudgetExhausted):  # the reservation holds the last slot
        admit(g)
    g.release(a)
    assert ledger.spent_usd(TENANT, SEPT) == Decimal(1) - est * Decimal("1.5")
    g.release(admit(g))  # the slot is free again


def test_FR_KB_011_a_failed_call_the_provider_bills_is_recorded(ledger: SpendLedger) -> None:
    g = guard(ledger)
    a = admit(g)
    g.settle(a, Decimal("0.0021"))  # e.g. a stream cut after its first event was billed
    g.release(a)  # the gateway's safety net afterwards is a no-op
    assert ledger.spent_usd(TENANT, SEPT) == Decimal("0.0021")


def test_FR_KB_011_gateway_releases_on_timeout_and_on_unexpected_errors() -> None:
    ledger = InMemorySpendLedger()
    g = guard(ledger)
    now_ms = int(NOW.timestamp() * 1000)

    class Failing:
        name = "failing"

        def __init__(self, error: Exception) -> None:
            self.error = error

        def send(self, request: MessagesRequest) -> Mapping[str, Any]:
            raise self.error

    with pytest.raises(ProviderUnavailable):
        gateway(g, Failing(TransportError("timeout"))).run_turn(
            Metering(TENANT, "ask"), "answer", "sys", [UserMessage(QUESTION)], []
        )
    assert ledger.reserved_usd(TENANT, SEPT, now_ms) == 0
    with pytest.raises(RuntimeError):
        gateway(g, Failing(RuntimeError("synthetic bug"))).run_turn(
            Metering(TENANT, "ask"), "answer", "sys", [UserMessage(QUESTION)], []
        )
    assert ledger.reserved_usd(TENANT, SEPT, now_ms) == 0
    assert ledger.spent_usd(TENANT, SEPT) == 0


def test_FR_KB_011_rate_limited_call_releases_its_reservation(ledger: SpendLedger) -> None:
    g = guard(ledger, cfg=config(rate_limit=1))
    g.release(admit(g))
    with pytest.raises(AiRateLimited):
        admit(g)
    assert ledger.reserved_usd(TENANT, SEPT, int(NOW.timestamp() * 1000)) == 0


def test_FR_KB_011_abandoned_reservations_expire_after_their_ttl(ledger: SpendLedger) -> None:
    est = estimate()
    ledger.add_usd(TENANT, SEPT, Decimal(1) - est * Decimal("1.5"))
    now = Now()
    g = guard(ledger, now=now)
    abandoned = admit(g)  # the process died: never settled or released
    with pytest.raises(BudgetExhausted):
        admit(g)
    ttl = config().budget.reservation.ttl_s
    now.at = NOW + timedelta(seconds=ttl - 1)
    with pytest.raises(BudgetExhausted):
        admit(g)
    now.at = NOW + timedelta(seconds=ttl + 1)
    later = admit(g)  # the abandoned reservation has lapsed
    assert later.reservation.id != abandoned.reservation.id
    # A settle that arrives after expiry still records the real spend, once.
    with capture_logs() as logs:
        after = g.settle(abandoned, Decimal("0.003"))
    assert after.applied
    assert [e["event"] for e in logs if e["event"].startswith("kb.budget")] == [
        "kb.budget.settled_late"
    ]
    assert not g.settle(abandoned, Decimal("0.003")).applied
    assert ledger.spent_usd(TENANT, SEPT) == Decimal(1) - est * Decimal("1.5") + Decimal("0.003")


def test_FR_KB_011_settle_is_idempotent(ledger: SpendLedger) -> None:
    g = guard(ledger)
    a = admit(g)
    first = g.settle(a, Decimal("0.004"))
    second = g.settle(a, Decimal("0.004"))
    g.release(a)
    assert first.applied
    assert not second.applied
    assert not second.alert_crossed
    assert second.total_usd == Decimal("0.004")
    assert ledger.spent_usd(TENANT, SEPT) == Decimal("0.004")


def test_FR_KB_011_month_rollover(ledger: SpendLedger) -> None:
    est = estimate()
    now = Now(datetime(2026, 9, 30, 18, 29, tzinfo=UTC))  # 30 Sep 23:59 IST
    g = guard(ledger, now=now)
    ledger.add_usd(TENANT, SEPT, Decimal(1) - est * Decimal("1.5"))
    september = admit(g)
    assert september.reservation.month == SEPT
    with pytest.raises(BudgetExhausted):
        admit(g)
    now.at = datetime(2026, 9, 30, 18, 31, tzinfo=UTC)  # 1 Oct 00:01 IST: a fresh budget
    october = [admit(g) for _ in range(3)]
    assert {a.reservation.month for a in october} == {OCT}
    # The September call finishes after midnight: its cost stays in the month that admitted it.
    g.settle(september, Decimal("0.004"))
    assert ledger.spent_usd(TENANT, SEPT) == Decimal(1) - est * Decimal("1.5") + Decimal("0.004")
    assert ledger.spent_usd(TENANT, OCT) == 0
    for a in october:
        g.settle(a, Decimal("0.001"))
    assert ledger.spent_usd(TENANT, OCT) == Decimal("0.003")


def test_FR_KB_011_spend_store_down_fails_closed() -> None:
    class Down(InMemorySpendLedger):
        def reserve(self, *args: Any, **kwargs: Any) -> bool:
            raise KVUnavailable("synthetic outage")

    with pytest.raises(ProviderUnavailable):
        admit(guard(Down()))


def test_FR_KB_011_zero_budget_reserves_nothing(ledger: SpendLedger) -> None:
    with pytest.raises(BudgetExhausted):
        admit(guard(ledger, budget_inr=0))
    assert ledger.reserved_usd(TENANT, SEPT, int(NOW.timestamp() * 1000)) == 0


# --- invariant 5: new log lines carry ids and numbers only --------------------------------------


def test_invariant_5_budget_log_lines_carry_ids_only() -> None:
    now = Now()
    ledger = InMemorySpendLedger()
    g = guard(ledger, now=now)
    cfg = config()

    class Flaky(InMemorySpendLedger):
        def settle(self, *args: Any, **kwargs: Any) -> Any:
            raise KVUnavailable("synthetic outage at 10.0.0.9")

    class Answering:
        name = "answering"

        def send(self, request: MessagesRequest) -> Mapping[str, Any]:
            return {
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": ANSWER_TEXT}],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": cfg.budget.reservation.input_tokens * 2,
                    "output_tokens": 10,
                },
            }

    with capture_logs() as logs:
        gateway(g, Answering(), cfg).run_turn(
            Metering(TENANT, "ask"), "answer", "sys " + QUESTION, [UserMessage(QUESTION)], []
        )  # over the estimate
        late = admit(g)
        now.at = NOW + timedelta(seconds=cfg.budget.reservation.ttl_s + 5)
        g.settle(late, Decimal("0.001"))  # settled after expiry
        flaky = guard(Flaky())
        flaky.release(admit(flaky))  # the store fails on release
    events = {e["event"] for e in logs}
    assert {
        "kb.budget.over_estimate",
        "kb.budget.settled_late",
        "kb.budget.release_failed",
    } <= events
    budget_lines = [e for e in logs if e["event"].startswith("kb.budget")]
    for line in budget_lines:
        assert set(line) - {"event", "log_level"} <= set(ALLOWED_FIELDS), line
        assert line["tenant_id"] == TENANT
        if "resource_id" in line:
            uuid.UUID(str(line["resource_id"]))
    rendered = json.dumps(logs, default=str)
    for text in (QUESTION, ANSWER_TEXT, "Synthetic Vidyalaya", "picnic", "10.0.0.9"):
        assert text not in rendered
