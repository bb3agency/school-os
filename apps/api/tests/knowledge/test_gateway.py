"""LLM gateway controls (ADR-0005, ADR-0033; docs/06 §5, §7, §12, §15; K4).

These run the Messages API wire with every role switched to its evaluated Anthropic fallback
(``LlmConfig.use_fallback``); ``test_gateway_gemini.py`` runs the same controls on the Gemini wire
(the default provider).

Offline and deterministic: a scripted transport stands in for the provider, and clock, sleep
and the budget month are injected. Covers redaction before send (invariant 4), no text in logs
(invariant 5), metering and cost (FR-KB-009, NFR-CST-001), the monthly budget with its 80 %
alert and 100 % stop (FR-KB-011), switches, the rate limit, retries with backoff, the circuit
breaker (NFR-AVL-004), read-only whitelisted tools and tool-round caps (invariant 9, SEC-020),
and the request shape the models accept (models.yaml, invariant 13).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from structlog.testing import capture_logs

from app.authz.kv import InMemoryKV
from app.core.redaction import verhoeff_check_digit, verhoeff_valid
from app.knowledge.config.llm import load_llm_config
from app.knowledge.config.tools import load_tools_config
from app.knowledge.domain import (
    AnswerSegment,
    AssistantMessage,
    ConversationItem,
    Metering,
    ModelTurn,
    SearchResultBlock,
    ToolCall,
    ToolOutcome,
    ToolResultsMessage,
    ToolSpec,
    Usage,
    UserMessage,
)
from app.knowledge.gateway.budget import (
    BudgetGuard,
    InMemorySpendLedger,
    StaticAiPolicy,
    TenantAiSettings,
    budget_month,
)
from app.knowledge.gateway.errors import (
    AiDisabled,
    AiRateLimited,
    BudgetExhausted,
    GatewayMisuse,
    InvalidModelOutput,
    ProviderRejected,
    ProviderUnavailable,
)
from app.knowledge.gateway.gateway import Gateway
from app.knowledge.gateway.metering import RecordingSink
from app.knowledge.gateway.transport import MessagesRequest, TransportError

TENANT = uuid.UUID("0192f000-0000-7000-8000-000000000001")
OTHER_TENANT = uuid.UUID("0192f000-0000-7000-8000-000000000002")
QUERY = uuid.UUID("0192f000-0000-7000-8000-0000000000aa")
DOC = uuid.UUID("0192f000-0000-7000-8000-0000000000d1")
SOURCE = f"sos://doc/{DOC}/v1#p2"
NOW = datetime(2026, 9, 15, 6, 0, tzinfo=UTC)
ONE_USD_INR = load_llm_config().budget.usd_inr_rate
"""A budget of exactly $1.00 at the configured rate (``models.yaml`` ``budget.usd_inr_rate``)."""

# A synthetic 12-digit number that passes Verhoeff (never a real Aadhaar number).
AADHAAR = "23456789012" + verhoeff_check_digit("23456789012")
assert verhoeff_valid(AADHAAR)
AADHAAR_SPACED = f"{AADHAAR[:4]} {AADHAAR[4:8]} {AADHAAR[8:]}"
MASKED = f"XXXX XXXX {AADHAAR[-4:]}"

QUESTION = "When does the synthetic exam timing circular start for class 9?"
ANSWER_TEXT = "The synthetic circular says exams start at 09:30 on 12/10/2026."
SEARCH_TOOL = ToolSpec(
    name="search_documents",
    description="Search the school's documents the user can read.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    permission="document.read",
)


# --- doubles ------------------------------------------------------------------------------------


def text_response(
    text: str = ANSWER_TEXT,
    *,
    citations: Sequence[Mapping[str, Any]] = (),
    stop: str = "end_turn",
    usage: Mapping[str, int] | None = None,
    model: str = "claude-sonnet-5",
) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if citations:
        block["citations"] = list(citations)
    return {
        "model": model,
        "content": [block],
        "stop_reason": stop,
        "usage": dict(usage or {"input_tokens": 1000, "output_tokens": 200}),
    }


class ScriptedTransport:
    """Returns (or raises) the scripted steps in order and records every request body."""

    name = "scripted"

    def __init__(self, *steps: Mapping[str, Any] | TransportError) -> None:
        self.steps = list(steps)
        self.sent: list[Mapping[str, Any]] = []

    def send(self, request: MessagesRequest) -> Mapping[str, Any]:
        self.sent.append(request.body)
        assert request.timeout_s == 60  # models.yaml client.request_timeout_s
        step = self.steps.pop(0) if self.steps else text_response()
        if isinstance(step, TransportError):
            raise step
        return step


@dataclass
class Clock:
    t: float = 1000.0
    sleeps: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


@dataclass
class Rig:
    gateway: Gateway
    transport: ScriptedTransport
    sink: RecordingSink
    ledger: InMemorySpendLedger
    clock: Clock
    counters: InMemoryKV
    now: list[datetime]


def rig(
    *steps: Mapping[str, Any] | TransportError,
    budget_inr: Decimal | int = 5000,
    ai_enabled: bool = True,
    kill_switch_on: bool = True,
    transport: ScriptedTransport | None = None,
) -> Rig:
    # The Messages-API doubles below run every role on its evaluated Anthropic fallback
    # (ADR-0033: config-selectable); the same controls on the Gemini wire are in
    # test_gateway_gemini.py.
    config = load_llm_config().use_fallback()
    clock = Clock()
    now = [NOW]
    ledger = InMemorySpendLedger()
    counters = InMemoryKV(clock=clock)
    guard = BudgetGuard(
        config,
        StaticAiPolicy(TenantAiSettings(ai_enabled, Decimal(budget_inr))),
        ledger,
        counters,
        now=lambda: now[0],
    )
    transport = transport if transport is not None else ScriptedTransport(*steps)
    sink = RecordingSink()
    gateway = Gateway(
        config=config,
        tools_config=load_tools_config(),
        transport=transport,
        guard=guard,
        sink=sink,
        enabled=lambda: kill_switch_on,
        sleep=clock.sleep,
        clock=clock,
    )
    return Rig(gateway, transport, sink, ledger, clock, counters, now)


def ask(r: Rig, conversation: Sequence[ConversationItem] | None = None, **kw: Any) -> ModelTurn:
    return r.gateway.run_turn(
        kw.pop("metering", Metering(tenant_id=TENANT, feature="ask", query_id=QUERY)),
        kw.pop("role", "answer"),
        kw.pop("system", "You are the records assistant for Synthetic Vidyalaya."),
        conversation or [UserMessage(QUESTION)],
        kw.pop("tools", [SEARCH_TOOL]),
    )


def grounded_conversation(text: str = "Exams start at 09:30.") -> list[ConversationItem]:
    call = ToolCall("toolu_01", "search_documents", {"query": QUESTION})
    first = ModelTurn("claude-sonnet-5", "tool_use", (), (call,), Usage(10, 5))
    block = SearchResultBlock(source=SOURCE, title="Circular · Exam timings", text=text)
    return [
        UserMessage(QUESTION),
        AssistantMessage(first),
        ToolResultsMessage((ToolOutcome("toolu_01", (block,)),)),
    ]


def breaker_state(r: Rig) -> str:
    return r.gateway.breaker.state


def sent_text(r: Rig) -> str:
    return json.dumps(r.transport.sent, ensure_ascii=False)


# --- invariant 4: redaction before send -----------------------------------------------------------


def test_invariant_4_verhoeff_number_never_reaches_the_provider() -> None:
    r = rig()
    call = ToolCall("toolu_01", "search_documents", {"query": f"aadhaar {AADHAAR}"})
    earlier = ModelTurn(
        "claude-sonnet-5",
        "tool_use",
        (AnswerSegment(f"Looking up {AADHAAR_SPACED}"),),
        (call,),
        Usage(1, 1),
    )
    block = SearchResultBlock(
        source=SOURCE, title=f"Register row {AADHAAR}", text=f"UID {AADHAAR_SPACED} as printed"
    )
    conversation: list[ConversationItem] = [
        UserMessage(f"Whose Aadhaar is {AADHAAR}?"),
        AssistantMessage(earlier),
        ToolResultsMessage((ToolOutcome("toolu_01", (block,)),)),
    ]
    ask(r, conversation, system=f"School helpline example {AADHAAR}")

    body = sent_text(r)
    assert AADHAAR not in body
    assert AADHAAR_SPACED not in body
    assert MASKED in body
    # Tool call ids still pair with their results after masking.
    messages = r.transport.sent[0]["messages"]
    assert messages[1]["content"][-1]["id"] == messages[2]["content"][0]["tool_use_id"]


def test_invariant_4_structured_output_input_is_redacted_too() -> None:
    r = rig(text_response('{"title": "x"}'))
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
        "additionalProperties": False,
    }
    r.gateway.generate_json(
        Metering(TENANT, "metadata"), "metadata", "Extract metadata.", f"Ref {AADHAAR}", schema
    )
    assert AADHAAR not in sent_text(r)
    assert MASKED in sent_text(r)


def test_invariant_4_model_output_is_masked_on_the_way_back() -> None:
    cite = {"type": "search_result_location", "source": SOURCE, "cited_text": f"UID {AADHAAR}"}
    r = rig(text_response(f"The number is {AADHAAR}.", citations=[cite]))
    turn = ask(r, grounded_conversation())
    assert AADHAAR not in turn.segments[0].text
    assert MASKED in turn.segments[0].text
    assert turn.segments[0].citations[0].cited_text == f"UID {MASKED}"


# --- invariant 5: no prompt or completion text in logs --------------------------------------------


def test_invariant_5_logs_carry_ids_and_numbers_only() -> None:
    r = rig(text_response(ANSWER_TEXT), TransportError("overloaded", status=529))
    with capture_logs() as logs:
        ask(r, [UserMessage(QUESTION)])
        r.transport.steps = [TransportError("overloaded", status=529), text_response(ANSWER_TEXT)]
        ask(r, grounded_conversation())
        with pytest.raises(BudgetExhausted):
            rig(budget_inr=0).gateway.run_turn(
                Metering(TENANT, "ask"), "answer", "sys", [UserMessage(QUESTION)], []
            )
    assert logs, "the gateway logs every call"
    rendered = json.dumps(logs, default=str)
    for text in (QUESTION, ANSWER_TEXT, "Exams start", "records assistant", "Synthetic Vidyalaya"):
        assert text not in rendered
    calls = [e for e in logs if e["event"] == "kb.llm.call"]
    assert {e["outcome"] for e in calls} == {"ok"}
    assert all(e["tenant_id"] == TENANT and e["action"] == "answer" for e in calls)
    assert any(e["event"] == "kb.llm.retry" for e in logs)


def test_invariant_5_errors_carry_no_provider_text() -> None:
    r = rig(TransportError("rejected", status=400))
    with pytest.raises(ProviderRejected) as info:
        ask(r)
    assert QUESTION not in str(info.value)
    assert info.value.__cause__ is None


# --- metering (FR-KB-009, NFR-CST-001) ------------------------------------------------------------


def test_NFR_CST_001_cost_per_tenant_and_feature_at_list_price() -> None:
    r = rig(text_response(usage={"input_tokens": 1000, "output_tokens": 200}))
    turn = ask(r)
    assert turn.usage == Usage(1000, 200)
    (event,) = r.sink.events
    # claude-sonnet-5: $2.00 in / $10.00 out per MTok -> 0.002 + 0.002.
    assert event.cost_usd == Decimal("0.004")
    assert (event.tenant_id, event.feature, event.role, event.query_id) == (
        TENANT,
        "ask",
        "answer",
        QUERY,
    )
    assert (event.model, event.provider, event.outcome, event.attempts) == (
        "claude-sonnet-5",
        "scripted",
        "ok",
        1,
    )
    assert event.month_spend_usd == Decimal("0.004")
    assert r.ledger.spent_usd(TENANT, "2026-09") == Decimal("0.004")
    assert r.ledger.spent_usd(OTHER_TENANT, "2026-09") == 0


def test_NFR_CST_001_prompt_cache_tokens_are_priced_separately() -> None:
    usage = {
        "input_tokens": 100,
        "output_tokens": 0,
        "cache_creation_input_tokens": 1000,
        "cache_read_input_tokens": 10_000,
    }
    r = rig(text_response(usage=usage))
    turn = ask(r)
    assert turn.usage.input_tokens == 11_100
    # (100 + 1000 * 1.25 + 10000 * 0.10) * $2 / 1e6
    assert r.sink.events[0].cost_usd == Decimal("0.004700")


def test_FR_KB_009_failed_calls_are_metered_without_cost() -> None:
    r = rig(TransportError("timeout"))
    with pytest.raises(ProviderUnavailable):
        ask(r)
    (event,) = r.sink.events
    assert event.outcome == "unavailable"
    assert event.cost_usd == 0
    assert event.month_spend_usd is None


# --- budget (FR-KB-011) --------------------------------------------------------------------------


def test_FR_KB_011_alert_at_80_percent_is_reported_once_per_month() -> None:
    r = rig(budget_inr=ONE_USD_INR)  # $1.00 at the configured rate
    r.ledger.add_usd(TENANT, "2026-09", Decimal("0.797"))
    with capture_logs() as logs:
        ask(r)  # +0.004 -> 0.801: crosses 80 %
        ask(r)  # +0.004 -> 0.805: already above
    alerts = [e for e in logs if e["event"] == "kb.budget.alert_crossed"]
    assert len(alerts) == 1
    assert alerts[0]["tenant_id"] == TENANT


def test_FR_KB_011_at_100_percent_calls_stop_and_the_caller_degrades_to_search_only() -> None:
    r = rig(budget_inr=ONE_USD_INR)
    r.ledger.add_usd(TENANT, "2026-09", Decimal("1.00"))
    with pytest.raises(BudgetExhausted) as info:
        ask(r)
    assert r.transport.sent == []
    err = info.value
    assert (err.status, err.code, err.message_key, err.search_only) == (
        429,
        "ai_budget_exhausted",
        "kb.errors.budget",
        True,
    )
    with pytest.raises(BudgetExhausted):
        r.gateway.generate_json(Metering(TENANT, "metadata"), "metadata", "s", "t", {})
    # Another school is unaffected.
    ask(r, metering=Metering(OTHER_TENANT, "ask"))
    assert len(r.transport.sent) == 1


def test_FR_KB_011_the_last_call_that_fits_completes_then_the_next_is_refused() -> None:
    # Since reservations (FR-KB-011) a call is admitted only while its worst-case estimate still
    # fits: list price of budget.reservation.input_tokens + the role's max_output_tokens
    # (claude-sonnet-5, answer: 16000 * $2 + 1500 * $10 per MTok = $0.047).
    r = rig(budget_inr=ONE_USD_INR)
    r.ledger.add_usd(TENANT, "2026-09", Decimal("0.953"))
    ask(r)  # 0.953 + 0.047 = 1.000: fits exactly; it really costs 0.004
    assert r.ledger.spent_usd(TENANT, "2026-09") == Decimal("0.957")
    with pytest.raises(BudgetExhausted):
        ask(r)  # 0.957 + 0.047 > 1.000
    assert len(r.transport.sent) == 1
    assert r.ledger.spent_usd(TENANT, "2026-09") <= Decimal(1)


def test_FR_KB_011_zero_budget_means_no_ai() -> None:
    r = rig(budget_inr=0)
    with pytest.raises(BudgetExhausted):
        ask(r)
    assert r.transport.sent == []


def test_FR_KB_011_budget_months_reset_on_the_first_in_ist() -> None:
    assert budget_month(datetime(2026, 9, 30, 18, 29, tzinfo=UTC)) == "2026-09"
    assert budget_month(datetime(2026, 9, 30, 18, 30, tzinfo=UTC)) == "2026-10"
    r = rig(budget_inr=ONE_USD_INR)
    r.ledger.add_usd(TENANT, "2026-09", Decimal("5"))
    r.now[0] = datetime(2026, 9, 30, 19, 0, tzinfo=UTC)  # 1 Oct 00:30 IST
    ask(r)
    assert r.ledger.spent_usd(TENANT, "2026-10") == Decimal("0.004")


# --- switches and rate limit ---------------------------------------------------------------------


def test_SEC_020_kill_switch_stops_every_call() -> None:
    r = rig(kill_switch_on=False)
    with pytest.raises(AiDisabled) as info:
        ask(r)
    with pytest.raises(AiDisabled):
        r.gateway.generate_json(Metering(TENANT, "metadata"), "metadata", "s", "t", {})
    assert r.transport.sent == []
    assert info.value.search_only


def test_SEC_020_school_switch_or_flag_off_stops_calls() -> None:
    r = rig(ai_enabled=False)
    with pytest.raises(AiDisabled):
        ask(r)
    assert r.transport.sent == []


def test_SEC_020_rate_limit_per_tenant_and_feature_per_minute() -> None:
    r = rig()
    limit = load_llm_config().rate_limit.requests_per_minute_per_tenant
    assert limit == 30
    for _ in range(limit):
        ask(r)
    with pytest.raises(AiRateLimited):
        ask(r)
    ask(r, metering=Metering(OTHER_TENANT, "ask"))  # other school: own counter
    r.transport.steps = [text_response("{}")]
    r.gateway.generate_json(Metering(TENANT, "metadata"), "metadata", "s", "t", {})  # own feature
    r.now[0] = datetime(2026, 9, 15, 6, 1, tzinfo=UTC)  # next minute
    ask(r)


# --- retries, backoff, circuit breaker (NFR-AVL-004) ----------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        TransportError("rate_limited", status=429),
        TransportError("overloaded", status=529),
        TransportError("server", status=500),
        TransportError("server", status=503),
    ],
)
def test_NFR_AVL_004_transient_failures_are_retried_with_backoff(failure: TransportError) -> None:
    r = rig(failure, failure, text_response())
    ask(r)
    assert len(r.transport.sent) == 3
    first, second = r.clock.sleeps
    assert 0.5 <= first < 1.0  # base 0.5 * 2^0 + jitter [0, 0.5)
    assert 1.0 <= second < 1.5  # base 0.5 * 2^1 + jitter
    assert r.sink.events[0].attempts == 3
    assert r.sink.events[0].latency_ms >= 1500


def test_NFR_AVL_004_retries_stop_after_two_then_search_only() -> None:
    busy = TransportError("overloaded", status=529)
    r = rig(busy, busy, busy, text_response())
    with pytest.raises(ProviderUnavailable) as info:
        ask(r)
    assert len(r.transport.sent) == 3
    assert info.value.search_only
    assert r.sink.events[-1].outcome == "unavailable"


def test_NFR_AVL_004_retry_after_is_honoured_up_to_the_cap() -> None:
    r = rig(
        TransportError("rate_limited", status=429, retry_after_s=3),
        TransportError("rate_limited", status=429, retry_after_s=120),
        text_response(),
    )
    ask(r)
    assert r.clock.sleeps == [3, 8]  # backoff_max_s = 8


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (TransportError("timeout"), ProviderUnavailable),
        (TransportError("connection"), ProviderUnavailable),
        (TransportError("rejected", status=400), ProviderRejected),
        (TransportError("rejected", status=401), ProviderRejected),
    ],
)
def test_NFR_AVL_004_timeouts_connection_errors_and_4xx_are_not_retried(
    failure: TransportError, error: type[Exception]
) -> None:
    r = rig(failure, text_response())
    with pytest.raises(error):
        ask(r)
    assert len(r.transport.sent) == 1
    assert r.clock.sleeps == []


def test_NFR_AVL_004_circuit_opens_after_five_failures_for_sixty_seconds() -> None:
    down = TransportError("timeout")
    r = rig(down, down, down, down, down)
    for _ in range(5):
        with pytest.raises(ProviderUnavailable):
            ask(r)
    assert breaker_state(r) == "open"
    with pytest.raises(ProviderUnavailable):
        ask(r)
    assert len(r.transport.sent) == 5  # refused without a call
    assert (r.sink.events[-1].outcome, r.sink.events[-1].attempts) == ("unavailable", 0)

    r.clock.t += 60
    assert breaker_state(r) == "half_open"
    r.transport.steps = [down]
    with pytest.raises(ProviderUnavailable):
        ask(r)  # the one trial fails: open again
    assert breaker_state(r) == "open"
    assert len(r.transport.sent) == 6

    r.clock.t += 60
    r.transport.steps = [text_response()]
    ask(r)  # trial succeeds: closed
    assert breaker_state(r) == "closed"


def test_NFR_AVL_004_rejected_requests_do_not_open_the_circuit() -> None:
    bad = TransportError("rejected", status=400)
    r = rig(*([bad] * 6))
    for _ in range(6):
        with pytest.raises(ProviderRejected):
            ask(r)
    assert breaker_state(r) == "closed"


def test_NFR_AVL_004_retries_count_towards_the_breaker() -> None:
    busy = TransportError("overloaded", status=529)
    r = rig(*([busy] * 6))
    with pytest.raises(ProviderUnavailable):
        ask(r)  # 3 attempts
    with pytest.raises(ProviderUnavailable):
        ask(r)  # 2 more attempts open it; the third retry is refused by the breaker
    assert breaker_state(r) == "open"
    assert len(r.transport.sent) == 5


# --- tools: read-only whitelist and rounds (invariant 9, SEC-020, ADR-0008) ----------------------


def test_invariant_9_only_whitelisted_read_only_tools_reach_the_model() -> None:
    r = rig()
    write_tool = ToolSpec("update_student", "Change a record", {"type": "object"}, "student.write")
    with pytest.raises(GatewayMisuse):
        ask(r, tools=[SEARCH_TOOL, write_tool])
    assert r.transport.sent == []


def test_invariant_9_a_call_to_a_tool_not_offered_is_rejected() -> None:
    rogue = {
        "model": "claude-sonnet-5",
        "content": [{"type": "tool_use", "id": "toolu_9", "name": "find_students", "input": {}}],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    r = rig(rogue)
    with pytest.raises(InvalidModelOutput):
        ask(r)
    assert r.sink.events[0].outcome == "invalid_output"
    assert r.sink.events[0].cost_usd > 0  # billed tokens are still metered


def test_SEC_020_after_three_tool_rounds_the_model_must_answer() -> None:
    r = rig()
    conversation: list[ConversationItem] = [UserMessage(QUESTION)]
    ask(r, conversation)
    assert "tool_choice" not in r.transport.sent[-1]  # auto
    for n in range(3):
        call = ToolCall(f"toolu_{n}", "search_documents", {"query": "q"})
        conversation += [
            AssistantMessage(ModelTurn("m", "tool_use", (), (call,), Usage(1, 1))),
            ToolResultsMessage((ToolOutcome(f"toolu_{n}", ()),)),
        ]
    ask(r, conversation)
    assert r.transport.sent[-1]["tool_choice"] == {"type": "none"}


def test_SEC_020_tool_choice_is_never_forced() -> None:
    r = rig()
    ask(r)
    ask(r, grounded_conversation())
    for body in r.transport.sent:
        assert body.get("tool_choice", {"type": "auto"})["type"] in {"auto", "none"}


def test_SEC_020_tool_results_beyond_the_context_budget_are_refused() -> None:
    r = rig()
    with pytest.raises(GatewayMisuse):
        ask(r, grounded_conversation("x" * 40_000))  # > 12000 tokens at 3 chars/token
    assert r.transport.sent == []


def test_eval_judge_is_refused_for_product_traffic() -> None:
    r = rig(text_response("{}"))
    with pytest.raises(GatewayMisuse):
        r.gateway.generate_json(Metering(TENANT, "ask"), "eval_judge", "s", "t", {})
    r.gateway.generate_json(Metering(TENANT, "eval"), "eval_judge", "s", "t", {})
    body = r.transport.sent[0]
    assert body["model"] == "claude-opus-5-5"
    assert "thinking" not in body  # Opus 5.5 cannot turn thinking off
    assert body["output_config"]["effort"] == "low"


# --- request shape (docs/06 §7, §12; models.yaml) -----------------------------------------------


def test_FR_KB_005_request_uses_search_result_blocks_with_citations() -> None:
    r = rig()
    ask(r, grounded_conversation())
    body = r.transport.sent[0]
    assert body["model"] == "claude-sonnet-5"
    assert body["max_tokens"] == 1500
    assert body["thinking"] == {"type": "disabled"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    result = body["messages"][2]["content"][0]
    assert result["type"] == "tool_result"
    (block,) = result["content"]
    assert block == {
        "type": "search_result",
        "source": SOURCE,
        "title": "Circular · Exam timings",
        "content": [{"type": "text", "text": "Exams start at 09:30."}],
        "citations": {"enabled": True},
    }


@pytest.mark.parametrize(
    ("role", "max_tokens"),
    [("router", 300), ("metadata", 500), ("translation", 1500), ("extraction", 2000)],
)
def test_SEC_020_output_caps_per_role(role: str, max_tokens: int) -> None:
    r = rig(text_response("{}"))
    r.gateway.generate_json(Metering(TENANT, "metadata"), role, "s", "t", {"type": "object"})  # type: ignore[arg-type]
    body = r.transport.sent[0]
    assert body["max_tokens"] == max_tokens
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}
    assert body["thinking"] == {"type": "disabled"}


def test_FR_KB_005_citations_and_tool_calls_are_parsed() -> None:
    cite = {
        "type": "search_result_location",
        "source": SOURCE,
        "title": "Circular",
        "cited_text": "Exams start at 09:30.",
        "search_result_index": 0,
        "start_block_index": 0,
        "end_block_index": 1,
    }
    response = {
        "model": "claude-sonnet-5",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Exams start at 09:30", "citations": [cite]},
            {"type": "text", "text": "."},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 50, "output_tokens": 10},
    }
    turn = ask(rig(response), grounded_conversation())
    assert turn.stop_reason == "end_turn"
    assert turn.tool_calls == ()
    assert [s.text for s in turn.segments] == ["Exams start at 09:30", "."]
    assert turn.segments[0].citations[0].source == SOURCE
    assert turn.segments[1].citations == ()

    call = {
        "type": "tool_use",
        "id": "toolu_2",
        "name": "search_documents",
        "input": {"query": "q"},
    }
    turn = ask(rig({**response, "content": [call], "stop_reason": "tool_use"}))
    assert turn.stop_reason == "tool_use"
    assert turn.tool_calls == (ToolCall("toolu_2", "search_documents", {"query": "q"}),)


@pytest.mark.parametrize(
    ("stop", "outcome"), [("refusal", "refused"), ("max_tokens", "max_tokens")]
)
def test_FR_KB_009_refusals_and_truncation_are_metered(stop: str, outcome: str) -> None:
    r = rig(text_response("", stop=stop))
    turn = ask(r)
    assert turn.stop_reason == stop
    assert r.sink.events[0].outcome == outcome


# --- structured output ----------------------------------------------------------------------------

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": ["circular", "letter"]},
        "issued_on": {"type": ["string", "null"]},
    },
    "required": ["doc_type", "issued_on"],
    "additionalProperties": False,
}


def test_FR_KB_003_generate_json_returns_validated_output() -> None:
    r = rig(text_response('{"doc_type": "circular", "issued_on": null}'))
    out = r.gateway.generate_json(Metering(TENANT, "metadata"), "metadata", "s", "t", SCHEMA)
    assert out == {"doc_type": "circular", "issued_on": None}
    assert r.sink.events[0].outcome == "ok"


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"doc_type": "memo", "issued_on": null}',
        '{"doc_type": "circular"}',
        '{"doc_type": "circular", "issued_on": null, "extra": 1}',
        "[]",
    ],
)
def test_FR_KB_003_invalid_structured_output_is_rejected_and_metered(text: str) -> None:
    r = rig(text_response(text))
    with pytest.raises(InvalidModelOutput) as info:
        r.gateway.generate_json(Metering(TENANT, "metadata"), "metadata", "s", "t", SCHEMA)
    assert "memo" not in str(info.value)
    assert r.sink.events[0].outcome == "invalid_output"
    assert r.sink.events[0].cost_usd > 0
