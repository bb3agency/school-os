"""Live transport: the Anthropic Messages API through the official SDK (ADR-0005).

The only module in the repository that imports ``anthropic`` (CLAUDE.md §11; semgrep
``sos-llm-sdk-outside-gateway``; in-suite AST test). The client is built with:

- the organization API key from ``Settings.anthropic_api_key`` passed explicitly, so the SDK never
  falls back to ``ANTHROPIC_*`` environment variables, profiles or a personal login
  (invariant 10);
- ``base_url`` from ``models.yaml`` (``ANTHROPIC_BASE_URL`` cannot redirect traffic);
- ``max_retries=0``: the gateway retries itself, so attempts are counted, logged, metered and
  seen by the circuit breaker;
- the SDK's own loggers capped at WARNING (at DEBUG they would print request bodies).

Zero Data Retention is an arrangement on the production API organization, not a request flag
(invariant 10; docs/08 §8): request it for the organization whose key is configured.

SDK failures become :class:`TransportError` kinds; provider error messages are dropped (they
can echo input).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from typing import Any

import anthropic

from app.knowledge.gateway.transport import FailureKind, MessagesRequest, TransportError

_SDK_LOGGERS = ("anthropic", "httpx", "httpcore")


def _retry_after(exc: anthropic.APIStatusError) -> float | None:
    raw = exc.response.headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def classify(exc: Exception) -> TransportError:
    """Map an SDK exception to a failure kind (no message text is kept)."""
    if isinstance(exc, anthropic.APITimeoutError):
        return TransportError("timeout")
    if isinstance(exc, anthropic.APIConnectionError):
        return TransportError("connection")
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        kind: FailureKind
        if status == 429:
            kind = "rate_limited"
        elif status == 529 or exc.type == "overloaded_error":
            kind = "overloaded"
        elif status >= 500:
            kind = "server"
        else:
            kind = "rejected"
        return TransportError(kind, status=status, retry_after_s=_retry_after(exc))
    return TransportError("rejected")


class AnthropicTransport:
    name = "anthropic"

    def __init__(self, *, api_key: str, base_url: str, http_client: Any | None = None) -> None:
        if not api_key.strip():
            raise ValueError("an organization API key is required for live AI")
        for name in _SDK_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)
        self._client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            http_client=http_client,
        )

    def send(self, request: MessagesRequest) -> Mapping[str, Any]:
        try:
            message = self._client.messages.create(**dict(request.body), timeout=request.timeout_s)
        except anthropic.AnthropicError as exc:
            raise classify(exc) from None
        data: Mapping[str, Any] = message.to_dict()
        return data

    def stream(self, request: MessagesRequest) -> Iterator[Mapping[str, Any]]:
        """Server-sent events of one call as dicts (``stream=True``). An ``error`` event or a
        broken connection mid-stream raises :class:`TransportError`; closing the iterator
        closes the HTTP response, so the provider stops generating."""
        try:
            stream = self._client.messages.create(
                **dict(request.body), stream=True, timeout=request.timeout_s
            )
        except anthropic.AnthropicError as exc:
            raise classify(exc) from None
        return _events(stream)


def _events(stream: Any) -> Iterator[Mapping[str, Any]]:
    try:
        for event in stream:
            data: Mapping[str, Any] = event.to_dict()
            if data.get("type") == "error":
                error = data.get("error")
                kind = error.get("type") if isinstance(error, Mapping) else None
                raise TransportError("overloaded" if kind == "overloaded_error" else "server")
            yield data
    except anthropic.AnthropicError as exc:
        raise classify(exc) from None
    finally:
        stream.close()


__all__ = ["AnthropicTransport", "classify"]
