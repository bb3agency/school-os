"""The seam between the gateway's controls and a provider: one Messages API call.

The gateway builds a redacted Messages API request (:mod:`.wire`), hands it to a
:class:`Transport` and parses the Messages API response dict it gets back. Retries, the circuit
breaker, budgets and metering sit above this seam, so the live transport
(:mod:`.anthropic_transport`), the offline fake (:mod:`.fake`) and test doubles behave the same
way to callers. A transport never logs the request or the response (invariant 5).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

FailureKind = Literal["rate_limited", "overloaded", "server", "timeout", "connection", "rejected"]
RETRYABLE: frozenset[FailureKind] = frozenset({"rate_limited", "overloaded", "server"})
"""Retried with backoff (429, 529 overloaded, other 5xx). Timeouts and connection errors are
not retried (a 60 s timeout retried twice would blow the FR-KB-008 budget) but count towards
the circuit breaker, like every failure except ``rejected``."""


@dataclass(frozen=True, slots=True)
class MessagesRequest:
    """Body of ``POST /v1/messages`` (already redacted) plus the per-call timeout."""

    body: Mapping[str, Any]
    timeout_s: float


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
        """``anthropic`` or ``fake``: recorded with every metering event."""
        ...

    def send(self, request: MessagesRequest) -> Mapping[str, Any]:
        """One attempt. Returns the Messages API response as a plain dict (``content``,
        ``stop_reason``, ``usage``, ``model``); raises :class:`TransportError`."""
        ...


@runtime_checkable
class StreamingTransport(Transport, Protocol):
    """A transport that can also stream one Messages API call (docs/06 §5.1; FR-KB-008)."""

    def stream(self, request: MessagesRequest) -> Iterator[Mapping[str, Any]]:
        """One streamed attempt: the Messages API stream events as plain dicts
        (``message_start``, ``content_block_start``, ``content_block_delta``,
        ``content_block_stop``, ``message_delta``, ``message_stop``; ``ping`` may be skipped).
        Raises :class:`TransportError` when the call fails, before or after the first event
        (an ``error`` event mid-stream is raised as ``overloaded``/``server``). Closing the
        iterator early closes the connection (the provider stops generating)."""
        ...


__all__ = [
    "RETRYABLE",
    "FailureKind",
    "MessagesRequest",
    "StreamingTransport",
    "Transport",
    "TransportError",
]
