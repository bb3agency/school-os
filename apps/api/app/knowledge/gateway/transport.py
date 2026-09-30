"""The seam between the gateway's controls and a provider: one model call.

The gateway builds a redacted request in the transport's wire format (:mod:`.codec`: the
Anthropic Messages API in :mod:`.wire`, Gemini ``generateContent`` in :mod:`.gemini_wire`),
hands it to a :class:`Transport` and parses the response dict it gets back. Retries, the circuit
breaker, budgets and metering sit above this seam, so the live transports
(:mod:`.gemini_transport`, :mod:`.anthropic_transport`), the offline fakes (:mod:`.fake`,
:mod:`.fake_gemini`) and test doubles behave the same way to callers. A transport never logs the
request or the response (invariant 5).

A transport says which wire format it speaks with an optional ``wire`` attribute (``gemini`` or
``anthropic``; absent means ``anthropic``, the Messages API shape every older double speaks).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol, runtime_checkable

FailureKind = Literal["rate_limited", "overloaded", "server", "timeout", "connection", "rejected"]
RETRYABLE: frozenset[FailureKind] = frozenset({"rate_limited", "overloaded", "server"})
"""Retried with backoff (429, 529 overloaded, other 5xx). Timeouts and connection errors are
not retried (a 60 s timeout retried twice would blow the FR-KB-008 budget) but count towards
the circuit breaker, like every failure except ``rejected``."""

Wire = Literal["anthropic", "gemini"]
DEFAULT_WIRE: Final[Wire] = "anthropic"


def wire_of(transport: object) -> Wire:
    """The wire format a transport speaks (``anthropic`` when it does not say)."""
    wire = getattr(transport, "wire", DEFAULT_WIRE)
    if wire not in ("anthropic", "gemini"):
        raise ValueError(f"unknown wire format {wire!r}")
    return "gemini" if wire == "gemini" else "anthropic"


@dataclass(frozen=True, slots=True)
class MessagesRequest:
    """One provider call: the redacted body in the transport's wire format, the per-call
    timeout and, for providers that put them outside the body (Gemini), the model and location.

    ``static_prefix`` names body keys that hold only static, non-personal content (the system
    instruction and tool definitions); a transport MAY move exactly those into a provider-side
    context cache (ADR-0033). Everything else is never cached."""

    body: Mapping[str, Any]
    timeout_s: float
    model: str | None = None
    location: str | None = None
    static_prefix: tuple[str, ...] = ()


ProviderRequest = MessagesRequest
"""Provider-neutral name of the same request type."""


class TransportError(Exception):
    """A classified provider failure. Never carries provider message text (it may echo input)."""

    def __init__(
        self, kind: FailureKind, *, status: int | None = None, retry_after_s: float | None = None
    ) -> None:
        super().__init__(f"provider call failed: {kind} ({status})")
        self.kind: FailureKind = kind
        self.status = status
        self.retry_after_s = retry_after_s

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


@runtime_checkable
class Transport(Protocol):
    @property
    def name(self) -> str:
        """``gemini``, ``anthropic`` or ``fake``: recorded with every metering event."""
        ...

    def send(self, request: MessagesRequest) -> Mapping[str, Any]:
        """One attempt. Returns the provider response as a plain dict (Messages API:
        ``content``, ``stop_reason``, ``usage``, ``model``; Gemini: ``candidates``,
        ``usageMetadata``, ``modelVersion``, ``promptFeedback``); raises
        :class:`TransportError`."""
        ...


@runtime_checkable
class StreamingTransport(Transport, Protocol):
    """A transport that can also stream one call (docs/06 §5.1; FR-KB-008)."""

    def stream(self, request: MessagesRequest) -> Iterator[Mapping[str, Any]]:
        """One streamed attempt as plain dicts: Messages API stream events (``message_start``,
        ``content_block_*``, ``message_delta``, ``message_stop``) or Gemini
        ``streamGenerateContent`` chunks (each a partial ``GenerateContentResponse``).
        Raises :class:`TransportError` when the call fails, before or after the first event
        (an error mid-stream is raised as ``overloaded``/``server``). Closing the iterator
        early closes the connection (the provider stops generating)."""
        ...


__all__ = [
    "DEFAULT_WIRE",
    "RETRYABLE",
    "FailureKind",
    "MessagesRequest",
    "ProviderRequest",
    "StreamingTransport",
    "Transport",
    "TransportError",
    "Wire",
    "wire_of",
]
