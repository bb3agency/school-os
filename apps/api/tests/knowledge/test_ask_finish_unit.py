"""Ask finish-up (R2.4) without a database: the budget state the usage route reports, IST month
bounds, search-only reasons of stored answers, and where ``sos://`` sources open.

Requirements: FR-KB-040, FR-KB-041, FR-KB-042 (US-2501..US-2503); ADR-0038 decision 4.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from structlog.testing import capture_logs

from app.authz.kv import KVUnavailable
from app.knowledge import sources
from app.knowledge.config.llm import load_llm_config
from app.knowledge.gateway.budget import (
    BudgetGuard,
    InMemorySpendLedger,
    StaticAiPolicy,
    TenantAiSettings,
)
from app.knowledge.resolve import HREFS, href_for
from app.knowledge.service import search_only_reason
from app.knowledge.usage import month_bounds
from app.platform.common import config as platform_config

TENANT = uuid.UUID("0192f000-0000-7000-8000-0000000000c1")
NOW = datetime(2026, 9, 15, 6, 0, tzinfo=UTC)
MONTH = "2026-09"
CFG = load_llm_config()
ONE_USD_INR = CFG.budget.usd_inr_rate
ANSWER = CFG.roles["answer"]


class _Kv:
    def incr(self, key: str, *, ttl_s: int) -> int:
        raise AssertionError("status must not count against the rate limit")


def _guard(
    ledger: InMemorySpendLedger, *, budget_inr: Decimal = ONE_USD_INR, enabled: bool = True
) -> BudgetGuard:
    policy = StaticAiPolicy(TenantAiSettings(enabled, budget_inr))
    return BudgetGuard(CFG, policy, ledger, _Kv(), now=lambda: NOW)  # type: ignore[arg-type]


def test_FR_KB_040_status_ok_alert_exhausted_follow_the_reservation_rule() -> None:
    ledger = InMemorySpendLedger()
    g = _guard(ledger)
    assert g.status(TENANT, ANSWER).state == "ok"
    assert g.status(TENANT, ANSWER).month == MONTH
    alert = Decimal(str(CFG.budget.alert_fraction))
    ledger.add_usd(TENANT, MONTH, alert)  # 80 % of $1.00
    assert g.status(TENANT, ANSWER).state == "alert"
    # Spend one estimate below the limit: the next answer call would be refused.
    limit = Decimal(str(CFG.budget.degrade_fraction))
    ledger.add_usd(TENANT, MONTH, limit - alert - g.estimate_usd(ANSWER) / 2)
    assert g.status(TENANT, ANSWER).state == "exhausted"


def test_FR_KB_040_status_counts_live_reservations_and_reserves_nothing() -> None:
    ledger = InMemorySpendLedger()
    g = _guard(ledger)
    now_ms = int(NOW.timestamp() * 1000)
    assert ledger.reserve(TENANT, MONTH, "r1", Decimal("0.99"), Decimal(1), now_ms=now_ms, ttl_ms=1)
    held = ledger.reserved_usd(TENANT, MONTH, now_ms)
    assert g.status(TENANT, ANSWER).state == "exhausted"
    assert ledger.reserved_usd(TENANT, MONTH, now_ms) == held  # nothing new held
    assert ledger.spent_usd(TENANT, MONTH) == 0


def test_FR_KB_040_status_disabled_zero_budget_and_store_down() -> None:
    assert _guard(InMemorySpendLedger(), enabled=False).status(TENANT, ANSWER).state == "disabled"
    assert _guard(InMemorySpendLedger(), budget_inr=Decimal(0)).status(TENANT, ANSWER).state == (
        "exhausted"
    )

    class Down(InMemorySpendLedger):
        def spent_usd(self, tenant_id: uuid.UUID, month: str) -> Decimal:
            raise KVUnavailable("down")

    with capture_logs() as logs:
        assert _guard(Down()).status(TENANT, ANSWER).state == "unavailable"
    assert logs == []  # a read for display: no alert, no log


@pytest.mark.parametrize(
    ("now", "month", "start", "end"),
    [
        (datetime(2026, 9, 30, 18, 29, tzinfo=UTC), "2026-09", (2026, 9), (2026, 10)),
        (datetime(2026, 9, 30, 18, 30, tzinfo=UTC), "2026-10", (2026, 10), (2026, 11)),
        (datetime(2026, 12, 31, 20, 0, tzinfo=UTC), "2027-01", (2027, 1), (2027, 2)),
        (datetime(2026, 12, 1, 0, 0, tzinfo=UTC), "2026-12", (2026, 12), (2027, 1)),
    ],
)
def test_FR_KB_040_usage_month_is_the_ist_calendar_month(
    now: datetime, month: str, start: tuple[int, int], end: tuple[int, int]
) -> None:
    got, s, e = month_bounds(now)
    assert got == month
    assert (s.year, s.month, s.day, s.hour) == (*start, 1, 0)
    assert (e.year, e.month, e.day, e.hour) == (*end, 1, 0)
    assert s.utcoffset() == e.utcoffset() == timedelta(hours=5, minutes=30)


def test_ADR_0038_billable_statuses_equal_the_control_planes() -> None:
    """The usage route counts what the billing collector counts (ADR-0038 decision 4)."""
    billing = tuple(str(v) for v in platform_config()["ai_answers"]["billable_statuses"])
    assert CFG.budget.bundle.billable_statuses == billing == ("answered",)


@pytest.mark.parametrize(
    ("mode", "error", "reason"),
    [
        ("full", None, None),
        ("full", "ai_unavailable", None),
        ("search_only", "ai_budget_exhausted", "ai_budget_exhausted"),
        ("search_only", "ai_disabled", "ai_disabled"),
        ("search_only", "ai_rate_limited", "ai_rate_limited"),
        ("search_only", "ai_unavailable", "ai_unavailable"),
        ("search_only", "ai_request_rejected", "ai_unavailable"),
        ("search_only", "ai_invalid_output", "ai_unavailable"),
        ("search_only", "ai_gateway_misuse", "ai_unavailable"),
        ("search_only", None, "answer_unchecked"),
    ],
)
def test_FR_KB_041_stored_search_only_answers_carry_their_reason(
    mode: str, error: str | None, reason: str | None
) -> None:
    assert search_only_reason(mode, error) == reason


def test_FR_KB_042_every_source_kind_has_its_screen_or_none() -> None:
    i, q = uuid.uuid4(), uuid.uuid4()
    scope = "0" * 16
    cases = {
        sources.document_page(i, version_no=2, page=3): f"/documents/{i}",
        sources.student_field(i, attribute="dob", source="admission_register"): f"/students/{i}",
        sources.finding(i): f"/findings/{i}",
        sources.change_request(i): f"/change-requests/{i}",
        sources.verified_answer(i): "/ask/verified",
        sources.fee_dues(i, scope=scope): "/fees",
        sources.conversation_question(i, q): f"/ask/c/{i}#m-{q}",
        sources.student_count(i, scope=scope): None,
    }
    for uri, href in cases.items():
        assert href_for(sources.parse(uri)) == href, uri
    assert set(HREFS) | {"count"} == {sources.parse(u).kind for u in cases}
