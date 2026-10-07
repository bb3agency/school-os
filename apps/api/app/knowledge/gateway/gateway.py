"""The :class:`~app.knowledge.interfaces.LlmGateway` implementation (ADR-0005, ADR-0033; docs/06
§5, §12).

Every call runs the same controls, in this order, whichever provider serves the role:

1. ``SOS_KB_ENABLED`` kill switch, then the role's rules (the offline eval judge only for
   ``feature="eval"``; tools only from the ADR-0008 whitelist in ``tools.yaml``; tool results
   within the docs/06 §12 context budget; images only for a role that accepts them).
2. :class:`BudgetGuard`: the school's AI switch and flag, an atomic reservation of the call's
   worst-case cost against its monthly budget (no room -> refuse, the caller answers
   search-only) and the per-tenant rate limit. The reservation is settled to the real cost when
   the call is metered, and released in a ``finally`` whatever else happens (FR-KB-011).
3. The role's provider (``models.yaml`` ``roles.<role>.provider``) picks the transport and, by
   the transport's wire format, the codec (:mod:`.codec`: Anthropic Messages API or Gemini
   ``generateContent``). Then that transport's circuit breaker; then the redacted request goes
   out with the role's model, output cap, thinking setting and timeout from ``models.yaml``;
   retries with backoff and jitter on 429/5xx/529 only.
4. Metering: tokens and list-price cost per tenant and feature settled into the spend ledger, the
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
from app.knowledge.config.llm import PROVIDERS, LlmConfig, Provider, RoleConfig
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
from app.knowledge.gateway.budget import Admission, BudgetGuard
from app.knowledge.gateway.codec import AnthropicCodec, Codec, ImageInput, Prepared
from app.knowledge.gateway.errors import (
    AiDisabled,
    GatewayMisuse,
    InvalidModelOutput,
    ProviderRejected,
    ProviderUnavailable,
)
from app.knowledge.gateway.gemini_wire import GeminiCodec
from app.knowledge.gateway.metering import (
    MeteringEvent,
    MeteringSink,
    Outcome,
    UnsettledSpend,
    cost_usd,
)
from app.knowledge.gateway.resilience import CircuitBreaker, backoff_delay
from app.knowledge.gateway.schema_check import SchemaViolation, validate
from app.knowledge.gateway.streaming import AadhaarStreamMasker
from app.knowledge.gateway.transport import (
    MessagesRequest,
    StreamingTransport,
    Transport,
    TransportError,
    Wire,
    wire_of,
)

log = get_logger(__name__)
tracer = trace.get_tracer("app.knowledge.gateway")


def _codec_for(wire_format: Wire) -> Codec:
    return GeminiCodec() if wire_format == "gemini" else AnthropicCodec()


@dataclass(frozen=True, slots=True)
class _Route:
    """Where one role's calls go: the transport, its codec and its circuit breaker."""

    transport: Transport
    codec: Codec
    breaker: CircuitBreaker


@dataclass(frozen=True, slots=True)
class _Call:
    """One model call's context for metering and the span."""

    metering: Metering
    role: ModelRole
    role_config: RoleConfig
    admission: Admission
    started: float
    route: _Route
    span: Span | None = None


@dataclass(frozen=True, slots=True)
class _Sent:
    response: Mapping[str, Any]
    attempts: int
    latency_ms: int


class Gateway:
    """One per process (each transport's breaker is shared by every tenant's calls).

    ``transports`` maps each provider to its transport; ``transport`` (tests, the eval bridge)
    serves every provider. A provider without a transport is a deployment error that surfaces
    as :class:`ProviderRejected` for that role only."""

    def __init__(
        self,
        *,
        config: LlmConfig,
        tools_config: ToolsConfig,
        guard: BudgetGuard,
        sink: MeteringSink,
        enabled: Callable[[], bool],
        transport: Transport | None = None,
        transports: Mapping[Provider, Transport] | None = None,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        chosen: dict[Provider, Transport] = dict(transports or {})
        if transport is not None:
            for provider in PROVIDERS:
                chosen.setdefault(provider, transport)
        if not chosen:
            raise ValueError("the gateway needs at least one transport")
        self._config = config
        self._whitelist = frozenset(tools_config.tools)
        self._guard = guard
        self._sink = sink
        self._enabled = enabled
        self._sleep = sleep
        self._clock = clock
        breakers: dict[int, CircuitBreaker] = {}
        self._routes: dict[Provider, _Route] = {}
        for provider, t in chosen.items():
            shared = breaker or breakers.setdefault(
                id(t), CircuitBreaker(config.client, clock=clock)
            )
            self._routes[provider] = _Route(t, _codec_for(wire_of(t)), shared)
        self._default = self._routes.get(config.default_provider) or next(
            iter(self._routes.values())
        )

    @property
    def breaker(self) -> CircuitBreaker:
        """The circuit breaker of the default provider's transport."""
        return self._default.breaker

    def breaker_for(self, provider: Provider) -> CircuitBreaker:
        return self._routes[provider].breaker

    @property
    def guard(self) -> BudgetGuard:
        """The budget guard (the worker settles deferred spend through it, audit W3-10)."""
        return self._guard

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
        *,
        images: Sequence[ImageInput] = (),
    ) -> Mapping[str, object]:
        """Strict JSON validated against ``schema``. ``images`` (register pages) only for a
        role with ``accepts_images``; the caller must have blacked out Aadhaar numbers."""
        with tracer.start_as_current_span("llm.call"):
            return self._generate_json(metering, role, system, text, schema, images=images)

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
        admission: Admission | None = None
        try:
            role_config, route, offered, prepared = self._prepare_turn(
                metering, role, system, conversation, tools
            )
            admission = self._guard.admit(metering.tenant_id, metering.feature, role_config)
            call = _Call(metering, role, role_config, admission, self._clock(), route, span)
            transport = route.transport
            if isinstance(transport, StreamingTransport):
                yield from self._streamed(transport, call, prepared, offered)
            else:
                yield from self._unstreamed(call, prepared, offered)
        finally:
            if admission is not None:
                self._guard.release(admission)  # a no-op once metering settled it
            span.end()

    def _prepare_turn(
        self,
        metering: Metering,
        role: ModelRole,
        system: str,
        conversation: Sequence[ConversationItem],
        tools: Sequence[ToolSpec],
    ) -> tuple[RoleConfig, _Route, frozenset[str], Prepared]:
        role_config = self._role(metering, role)
        route = self._route(role_config)
        offered = frozenset(t.name for t in tools)
        outside = sorted(offered - self._whitelist)
        if outside:
            raise GatewayMisuse(f"tools outside the ADR-0008 whitelist: {outside}")
        limits = self._config.limits
        used = wire.tool_result_tokens(conversation, limits.chars_per_token_estimate)
        if used > limits.tool_result_context_tokens:
            raise GatewayMisuse("tool results exceed the context budget; send fewer blocks")
        prepared = route.codec.turn_request(self._config, role_config, system, conversation, tools)
        return role_config, route, offered, prepared

    def _run_turn(
        self,
        metering: Metering,
        role: ModelRole,
        system: str,
        conversation: Sequence[ConversationItem],
        tools: Sequence[ToolSpec],
    ) -> ModelTurn:
        role_config, route, offered, prepared = self._prepare_turn(
            metering, role, system, conversation, tools
        )
        admission = self._guard.admit(metering.tenant_id, metering.feature, role_config)
        try:
            call = _Call(metering, role, role_config, admission, self._clock(), route)
            sent = self._send(call, prepared)
            return self._finish_turn(call, sent, offered, prepared)
        finally:
            self._guard.release(admission)  # a no-op once metering settled it

    def _finish_turn(
        self, call: _Call, sent: _Sent, offered: frozenset[str], prepared: Prepared
    ) -> ModelTurn:
        codec = call.route.codec
        try:
            parsed = codec.parse_turn(
                self._config, sent.response, offered, call.role_config.model, prepared
            )
        except InvalidModelOutput:
            self._meter_call(call, sent, "invalid_output", codec.usage(sent.response))
            raise
        turn = parsed.turn
        outcome: Outcome = "ok"
        if turn.stop_reason == "refusal":
            outcome = "refused"
        elif turn.stop_reason == "max_tokens":
            outcome = "max_tokens"
        if parsed.dropped_markers:
            ids: dict[str, Any] = {"tenant_id": call.metering.tenant_id}
            if call.metering.query_id is not None:
                ids |= {"resource_type": "kb_query", "resource_id": call.metering.query_id}
            log.info(
                "kb.llm.citation_markers_dropped",
                **ids,
                action=call.role,
                count=parsed.dropped_markers,
            )
        self._meter_call(call, sent, outcome, parsed.usage)
        return turn

    def _unstreamed(
        self, call: _Call, prepared: Prepared, offered: frozenset[str]
    ) -> Iterator[TurnEvent]:
        """A transport without streaming: the whole text as one delta, then the turn."""
        sent = self._send(call, prepared)
        turn = self._finish_turn(call, sent, offered, prepared)
        text = "".join(s.text for s in turn.segments)
        if text:
            yield TextDelta(text)
        yield turn

    def _request(self, call: _Call, prepared: Prepared) -> MessagesRequest:
        return MessagesRequest(
            body=prepared.body,
            timeout_s=self._config.client.request_timeout_s,
            model=call.role_config.model,
            location=call.role_config.location,
            static_prefix=prepared.static_prefix,
        )

    def _streamed(
        self,
        transport: StreamingTransport,
        call: _Call,
        prepared: Prepared,
        offered: frozenset[str],
    ) -> Iterator[TurnEvent]:
        request = self._request(call, prepared)
        events, first, attempts = self._open_stream(transport, request, call)
        assembler = call.route.codec.assembler(self._config, prepared)
        masker = AadhaarStreamMasker()
        breaker = call.route.breaker

        def sent() -> _Sent:
            return _Sent(assembler.response(), attempts, self._elapsed_ms(call.started))

        def partial(outcome: Outcome) -> None:
            so_far = sent()
            self._meter_call(call, so_far, outcome, call.route.codec.usage(so_far.response))

        try:
            for event in itertools.chain((first,), events):
                text = assembler.feed(event)
                shown = masker.feed(text) if text else ""
                if shown:
                    yield TextDelta(shown)
            if not assembler.complete:
                raise TransportError("connection")  # the stream ended without its last event
        except TransportError as exc:
            if exc.kind != "rejected" and breaker.record_failure():
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
        breaker.record_success()
        tail = masker.feed(assembler.flush()) + masker.flush()
        # The call is complete: meter it before the last delta, so a client that leaves while
        # that delta is pending cannot make a billed call vanish from the month's spend.
        turn = self._finish_turn(call, sent(), offered, prepared)
        if tail:
            yield TextDelta(tail)
        yield turn

    def _open_stream(
        self, transport: StreamingTransport, request: MessagesRequest, call: _Call
    ) -> tuple[Iterator[Mapping[str, Any]], Mapping[str, Any], int]:
        """Start the stream and read its first event, retrying like :meth:`_send` (a failure
        before any event arrived is retried; once events flow it is not)."""
        attempt = 0
        while True:
            if not call.route.breaker.allow():
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
        breaker = call.route.breaker
        sent = _Sent({}, attempt, self._elapsed_ms(call.started))
        if exc.kind == "rejected":
            breaker.record_success()  # reachable: our request (or its setup) was wrong
            self._meter_call(call, sent, "rejected")
            raise ProviderRejected("The AI provider rejected the request") from None
        if breaker.record_failure():
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
        self._meter(call, sent=sent, outcome=outcome, usage=usage)

    def _generate_json(
        self,
        metering: Metering,
        role: ModelRole,
        system: str,
        text: str,
        schema: Mapping[str, object],
        *,
        images: Sequence[ImageInput],
    ) -> Mapping[str, object]:
        role_config = self._role(metering, role)
        if images and not role_config.accepts_images:
            raise GatewayMisuse(f"role {role} does not take images")
        route = self._route(role_config)
        prepared = route.codec.json_request(
            self._config, role_config, system, text, schema, images=images
        )
        admission = self._guard.admit(metering.tenant_id, metering.feature, role_config)
        try:
            call = _Call(metering, role, role_config, admission, self._clock(), route)
            return self._json_call(call, prepared, schema)
        finally:
            self._guard.release(admission)  # a no-op once metering settled it

    def _json_call(
        self, call: _Call, prepared: Prepared, schema: Mapping[str, object]
    ) -> Mapping[str, object]:
        codec = call.route.codec
        sent = self._send(call, prepared)
        usage = codec.usage(sent.response)
        try:
            value = json.loads(codec.json_text(sent.response))
            validate(value, schema)
            if not isinstance(value, Mapping):
                raise InvalidModelOutput("structured output is not an object")
        except (ValueError, InvalidModelOutput) as exc:
            self._meter(call, sent=sent, outcome="invalid_output", usage=usage)
            if isinstance(exc, InvalidModelOutput):
                raise
            where = str(exc) if isinstance(exc, SchemaViolation) else "not JSON"
            raise InvalidModelOutput(f"structured output rejected: {where}") from None
        self._meter(call, sent=sent, outcome="ok", usage=usage)
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

    def _route(self, role_config: RoleConfig) -> _Route:
        route = self._routes.get(role_config.provider)
        if route is None:
            log.error("kb.llm.provider_not_configured", action=role_config.provider)
            raise ProviderRejected("The AI provider for this feature is not configured")
        return route

    def _send(self, call: _Call, prepared: Prepared) -> _Sent:
        request = self._request(call, prepared)
        breaker = call.route.breaker
        attempt = 0
        while True:
            if not breaker.allow():
                sent = _Sent({}, attempt, self._elapsed_ms(call.started))
                self._meter(call, sent=sent, outcome="unavailable")
                raise ProviderUnavailable("AI answers are temporarily unavailable")
            attempt += 1
            try:
                with tracer.start_as_current_span("llm.attempt"):
                    response = call.route.transport.send(request)
            except TransportError as exc:
                self._failed(call, exc, attempt)
                continue
            breaker.record_success()
            return _Sent(response, attempt, self._elapsed_ms(call.started))

    def _elapsed_ms(self, started: float) -> int:
        return max(0, int((self._clock() - started) * 1000))

    def _meter(
        self,
        call: _Call,
        *,
        sent: _Sent,
        outcome: Outcome,
        usage: wire.RawUsage | None = None,
    ) -> None:
        metering, role, role_config = call.metering, call.role, call.role_config
        usage = usage or wire.RawUsage(0, 0)
        model = role_config.model
        cost = cost_usd(
            self._config.prices[model], self._config.cache_multipliers(role_config.provider), usage
        )
        month_spend: Decimal | None = None
        unsettled: UnsettledSpend | None = None
        if cost <= 0:
            self._guard.release(call.admission)  # nothing billed: free the reservation
        else:
            try:
                after = self._guard.settle(call.admission, cost)
            except KVUnavailable:
                # The store flapped after a billed call: the sink queues the settlement (outbox)
                # and the worker settles the same reservation later (idempotent; audit W3-10).
                reservation = call.admission.reservation
                unsettled = UnsettledSpend(reservation.id, reservation.month)
                log.warning(
                    "kb.budget.settle_deferred",
                    tenant_id=metering.tenant_id,
                    resource_type="kb_budget_reservation",
                    resource_id=reservation.id,
                )
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
            provider=call.route.transport.name,
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
            unsettled=unsettled,
        )
        (call.span or trace.get_current_span()).set_attributes(
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
