"""The :class:`~app.knowledge.interfaces.LlmGateway` implementation (ADR-0005; docs/06 §5, §12).

Every call runs the same controls, in this order:

1. ``SOS_KB_ENABLED`` kill switch, then the role's rules (the offline eval judge only for
   ``feature="eval"``; tools only from the ADR-0008 whitelist in ``tools.yaml``; tool results
   within the docs/06 §12 context budget).
2. :class:`BudgetGuard`: the school's AI switch and flag, its monthly budget (100 % -> refuse,
   the caller answers search-only) and the per-tenant rate limit.
3. The circuit breaker; then the redacted request (:mod:`.wire`) goes to the transport with the
   role's model, output cap, thinking setting and timeout from ``models.yaml``; retries with
   backoff and jitter on 429/5xx/529 only.
4. Metering: tokens and list-price cost per tenant and feature into the spend ledger, the
   :class:`MeteringSink` and the ``llm.call`` span; a log line with ids, role, outcome, attempts
   and latency. Never prompt or completion text (invariant 5).
"""

from __future__ import annotations

import itertools
import json
import time
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span

from app.authz.kv import KVUnavailable
from app.core.logging import get_logger
from app.knowledge.config.llm import LlmConfig, RoleConfig
from app.knowledge.config.tools import ToolsConfig
from app.knowledge.domain import (
    ConversationItem,
    Metering,
    ModelRole,
    ModelTurn,
    TextDelta,
    ToolSpec,
    TurnEvent,
)
from app.knowledge.gateway import wire
from app.knowledge.gateway.budget import BudgetGuard, TenantAiSettings
from app.knowledge.gateway.errors import (
    AiDisabled,
    GatewayMisuse,
    InvalidModelOutput,
    ProviderRejected,
    ProviderUnavailable,
)
from app.knowledge.gateway.metering import MeteringEvent, MeteringSink, Outcome, cost_usd
from app.knowledge.gateway.resilience import CircuitBreaker, backoff_delay
from app.knowledge.gateway.schema_check import SchemaViolation, validate
from app.knowledge.gateway.streaming import AadhaarStreamMasker
from app.knowledge.gateway.transport import (
    MessagesRequest,
    StreamingTransport,
    Transport,
    TransportError,
)

log = get_logger(__name__)
tracer = trace.get_tracer("app.knowledge.gateway")


@dataclass(frozen=True, slots=True)
class _Call:
    """One model call's context for metering and the span."""

    metering: Metering
    role: ModelRole
    role_config: RoleConfig
    settings: TenantAiSettings
    started: float
    span: Span | None = None


@dataclass(frozen=True, slots=True)
class _Sent:
    response: Mapping[str, Any]
    attempts: int
    latency_ms: int


class Gateway:
    """One per process (the breaker is shared by every tenant's calls)."""

    def __init__(
        self,
        *,
        config: LlmConfig,
        tools_config: ToolsConfig,
        transport: Transport,
        guard: BudgetGuard,
        sink: MeteringSink,
        enabled: Callable[[], bool],
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._whitelist = frozenset(tools_config.tools)
        self._transport = transport
        self._guard = guard
        self._sink = sink
        self._enabled = enabled
        self._breaker = breaker or CircuitBreaker(config.client, clock=clock)
        self._sleep = sleep
        self._clock = clock

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    # --- LlmGateway ---------------------------------------------------------------------------

    def run_turn(
        self,
        metering: Metering,
        role: ModelRole,
        system: str,
        conversation: Sequence[ConversationItem],
        tools: Sequence[ToolSpec],
    ) -> ModelTurn:
        with tracer.start_as_current_span("llm.call"):
            return self._run_turn(metering, role, system, conversation, tools)

    def generate_json(
        self,
        metering: Metering,
        role: ModelRole,
        system: str,
        text: str,
        schema: Mapping[str, object],
    ) -> Mapping[str, object]:
        with tracer.start_as_current_span("llm.call"):
            return self._generate_json(metering, role, system, text, schema)

    def stream_turn(
        self,
        metering: Metering,
        role: ModelRole,
        system: str,
        conversation: Sequence[ConversationItem],
        tools: Sequence[ToolSpec],
    ) -> Generator[TurnEvent, None, None]:
        """:class:`~app.knowledge.interfaces.StreamingLlmGateway`: the same controls as
        :meth:`run_turn`, text deltas (Aadhaar-masked) as they arrive, then the whole turn."""
        # The span is not made current: a generator resumes in whichever thread iterates it.
        span = tracer.start_span("llm.call")
        try:
            role_config, offered, body = self._prepare_turn(
                metering, role, system, conversation, tools
            )
            settings = self._guard.check(metering.tenant_id, metering.feature)
            call = _Call(metering, role, role_config, settings, self._clock(), span)
            transport = self._transport
            if isinstance(transport, StreamingTransport):
                yield from self._streamed(transport, call, body, offered)
            else:
                yield from self._unstreamed(call, body, offered)
        finally:
            span.end()

    def _prepare_turn(
        self,
        metering: Metering,
        role: ModelRole,
        system: str,
        conversation: Sequence[ConversationItem],
        tools: Sequence[ToolSpec],
    ) -> tuple[RoleConfig, frozenset[str], dict[str, Any]]:
        role_config = self._role(metering, role)
        offered = frozenset(t.name for t in tools)
        outside = sorted(offered - self._whitelist)
        if outside:
            raise GatewayMisuse(f"tools outside the ADR-0008 whitelist: {outside}")
        limits = self._config.limits
        used = wire.tool_result_tokens(conversation, limits.chars_per_token_estimate)
        if used > limits.tool_result_context_tokens:
            raise GatewayMisuse("tool results exceed the context budget; send fewer blocks")
        body = wire.turn_request(self._config, role_config, system, conversation, tools)
        return role_config, offered, body

    def _run_turn(
        self,
        metering: Metering,
        role: ModelRole,
        system: str,
        conversation: Sequence[ConversationItem],
        tools: Sequence[ToolSpec],
    ) -> ModelTurn:
        role_config, offered, body = self._prepare_turn(metering, role, system, conversation, tools)
        settings = self._guard.check(metering.tenant_id, metering.feature)
        sent = self._send(metering, role, role_config, settings, body)
        call = _Call(metering, role, role_config, settings, self._clock())
        return self._finish_turn(call, sent, offered)

    def _finish_turn(self, call: _Call, sent: _Sent, offered: frozenset[str]) -> ModelTurn:
        try:
            turn, usage = wire.parse_turn(sent.response, offered, call.role_config.model)
        except InvalidModelOutput:
            self._meter_call(call, sent, "invalid_output", wire.usage(sent.response))
            raise
        outcome: Outcome = "ok"
        if turn.stop_reason == "refusal":
            outcome = "refused"
        elif turn.stop_reason == "max_tokens":
            outcome = "max_tokens"
        self._meter_call(call, sent, outcome, usage)
        return turn

    def _unstreamed(
        self, call: _Call, body: Mapping[str, Any], offered: frozenset[str]
    ) -> Iterator[TurnEvent]:
        """A transport without streaming: the whole text as one delta, then the turn."""
        sent = self._send(call.metering, call.role, call.role_config, call.settings, body)
        turn = self._finish_turn(call, sent, offered)
        text = "".join(s.text for s in turn.segments)
        if text:
            yield TextDelta(text)
        yield turn

    def _streamed(
        self,
        transport: StreamingTransport,
        call: _Call,
        body: Mapping[str, Any],
        offered: frozenset[str],
    ) -> Iterator[TurnEvent]:
        request = MessagesRequest(body=body, timeout_s=self._config.client.request_timeout_s)
        events, first, attempts = self._open_stream(transport, request, call)
        assembler = wire.StreamAssembler()
        masker = AadhaarStreamMasker()

        def sent() -> _Sent:
            return _Sent(assembler.response(), attempts, self._elapsed_ms(call.started))

        def partial(outcome: Outcome) -> None:
            so_far = sent()
            self._meter_call(call, so_far, outcome, wire.usage(so_far.response))

        try:
            for event in itertools.chain((first,), events):
                text = assembler.feed(event)
                shown = masker.feed(text) if text else ""
                if shown:
                    yield TextDelta(shown)
            if not assembler.complete:
                raise TransportError("connection")  # the stream ended without message_stop
        except TransportError as exc:
            if exc.kind != "rejected" and self._breaker.record_failure():
                log.warning("kb.llm.circuit_opened", action=call.role, error_code=exc.kind)
            partial("unavailable")
            raise ProviderUnavailable("AI answers are temporarily unavailable") from None
        except InvalidModelOutput:
            partial("invalid_output")
            raise
        except GeneratorExit:
            # The client went away: the provider call is closed below; meter what was used.
            partial("cancelled")
            raise
        finally:
            close = getattr(events, "close", None)
            if callable(close):
                close()
        self._breaker.record_success()
        tail = masker.flush()
        if tail:
            yield TextDelta(tail)
        yield self._finish_turn(call, sent(), offered)

    def _open_stream(
        self, transport: StreamingTransport, request: MessagesRequest, call: _Call
    ) -> tuple[Iterator[Mapping[str, Any]], Mapping[str, Any], int]:
        """Start the stream and read its first event, retrying like :meth:`_send` (a failure
        before any event arrived is retried; once events flow it is not)."""
        attempt = 0
        while True:
            if not self._breaker.allow():
                self._meter_call(call, _Sent({}, attempt, self._elapsed_ms(call.started)))
                raise ProviderUnavailable("AI answers are temporarily unavailable")
            attempt += 1
            try:
                events = iter(transport.stream(request))
                try:
                    first = next(events)
                except StopIteration:
                    raise TransportError("connection") from None
            except TransportError as exc:
                self._failed(call, exc, attempt)
                continue
            return events, first, attempt

    def _failed(self, call: _Call, exc: TransportError, attempt: int) -> None:
        """One failed attempt: sleep before a retry, or meter and raise the gateway error."""
        client = self._config.client
        sent = _Sent({}, attempt, self._elapsed_ms(call.started))
        if exc.kind == "rejected":
            self._breaker.record_success()  # reachable: our request was wrong
            self._meter_call(call, sent, "rejected")
            raise ProviderRejected("The AI provider rejected the request") from None
        if self._breaker.record_failure():
            log.warning("kb.llm.circuit_opened", action=call.role, error_code=exc.kind)
        if exc.retryable and attempt <= client.max_retries:
            delay = backoff_delay(client, attempt - 1, retry_after_s=exc.retry_after_s)
            log.info(
                "kb.llm.retry",
                tenant_id=call.metering.tenant_id,
                action=call.role,
                attempt=attempt,
                error_code=exc.kind,
                duration_ms=int(delay * 1000),
            )
            self._sleep(delay)
            return
        self._meter_call(call, sent)
        raise ProviderUnavailable("AI answers are temporarily unavailable") from None

    def _meter_call(
        self,
        call: _Call,
        sent: _Sent,
        outcome: Outcome = "unavailable",
        usage: wire.RawUsage | None = None,
    ) -> None:
        self._meter(
            call.metering,
            call.role,
            call.role_config,
            call.settings,
            sent=sent,
            outcome=outcome,
            usage=usage,
            span=call.span,
        )

    def _generate_json(
        self,
        metering: Metering,
        role: ModelRole,
        system: str,
        text: str,
        schema: Mapping[str, object],
    ) -> Mapping[str, object]:
        role_config = self._role(metering, role)
        body = wire.json_request(role_config, system, text, schema)
        settings = self._guard.check(metering.tenant_id, metering.feature)
        sent = self._send(metering, role, role_config, settings, body)
        usage = wire.usage(sent.response)
        try:
            if sent.response.get("stop_reason") == "refusal":
                raise InvalidModelOutput("the model declined")
            value = json.loads(wire.response_text(sent.response))
            validate(value, schema)
            if not isinstance(value, Mapping):
                raise InvalidModelOutput("structured output is not an object")
        except (ValueError, InvalidModelOutput) as exc:
            self._meter(
                metering,
                role,
                role_config,
                settings,
                sent=sent,
                outcome="invalid_output",
                usage=usage,
            )
            if isinstance(exc, InvalidModelOutput):
                raise
            where = str(exc) if isinstance(exc, SchemaViolation) else "not JSON"
            raise InvalidModelOutput(f"structured output rejected: {where}") from None
        self._meter(metering, role, role_config, settings, sent=sent, outcome="ok", usage=usage)
        result: Mapping[str, object] = wire.redact_payload(value)
        return result

    # --- controls -----------------------------------------------------------------------------

    def _role(self, metering: Metering, role: ModelRole) -> RoleConfig:
        if not self._enabled():
            raise AiDisabled("AI answers are switched off")
        role_config = self._config.roles[role]
        if role_config.offline_only and metering.feature != "eval":
            raise GatewayMisuse(f"role {role} is for offline evaluation only")
        return role_config

    def _send(
        self,
        metering: Metering,
        role: ModelRole,
        role_config: RoleConfig,
        settings: TenantAiSettings,
        body: Mapping[str, Any],
    ) -> _Sent:
        client = self._config.client
        request = MessagesRequest(body=body, timeout_s=client.request_timeout_s)
        started = self._clock()
        attempt = 0
        while True:
            if not self._breaker.allow():
                sent = _Sent({}, attempt, self._elapsed_ms(started))
                self._meter(metering, role, role_config, settings, sent=sent, outcome="unavailable")
                raise ProviderUnavailable("AI answers are temporarily unavailable")
            attempt += 1
            try:
                with tracer.start_as_current_span("llm.attempt"):
                    response = self._transport.send(request)
            except TransportError as exc:
                self._failed(_Call(metering, role, role_config, settings, started), exc, attempt)
                continue
            self._breaker.record_success()
            return _Sent(response, attempt, self._elapsed_ms(started))

    def _elapsed_ms(self, started: float) -> int:
        return max(0, int((self._clock() - started) * 1000))

    def _meter(
        self,
        metering: Metering,
        role: ModelRole,
        role_config: RoleConfig,
        settings: TenantAiSettings,
        *,
        sent: _Sent,
        outcome: Outcome,
        usage: wire.RawUsage | None = None,
        span: Span | None = None,
    ) -> None:
        usage = usage or wire.RawUsage(0, 0)
        model = role_config.model
        cost = cost_usd(self._config.prices[model], self._config.cache_price_multipliers, usage)
        month_spend: Decimal | None = None
        if cost > 0:
            try:
                after = self._guard.record(metering.tenant_id, settings, cost)
            except KVUnavailable:
                log.error("kb.budget.spend_unrecorded", tenant_id=metering.tenant_id)
            else:
                month_spend = after.total_usd
                if after.alert_crossed:
                    # NFR-CST-001: first crossing of the alert threshold this month.
                    log.warning(
                        "kb.budget.alert_crossed", tenant_id=metering.tenant_id, action=after.level
                    )
        event = MeteringEvent(
            tenant_id=metering.tenant_id,
            feature=metering.feature,
            role=role,
            query_id=metering.query_id,
            provider=self._transport.name,
            model=model,
            outcome=outcome,
            attempts=sent.attempts,
            latency_ms=sent.latency_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cost_usd=cost,
            month_spend_usd=month_spend,
            document_id=metering.document_id,
        )
        (span or trace.get_current_span()).set_attributes(
            {
                "llm.provider": event.provider,
                "llm.model": model,
                "llm.role": role,
                "llm.feature": metering.feature,
                "llm.outcome": outcome,
                "llm.attempts": sent.attempts,
                "llm.latency_ms": sent.latency_ms,
                "llm.input_tokens": usage.total_input,
                "llm.output_tokens": usage.output_tokens,
                "llm.cost_usd": float(cost),
            }
        )
        self._sink.record(event)
        ids: dict[str, Any] = {"tenant_id": metering.tenant_id}
        if metering.query_id is not None:
            ids |= {"resource_type": "kb_query", "resource_id": metering.query_id}
        log.info(
            "kb.llm.call",
            **ids,
            action=role,
            outcome=outcome,
            attempt=sent.attempts,
            duration_ms=sent.latency_ms,
            count=usage.total_input + usage.output_tokens,
        )


__all__ = ["Gateway"]
