"""Why a gateway call did not reach (or come back from) the model.

Every error is a :class:`app.core.errors.DomainError` with a stable ``code`` and an i18n
``message_key`` for the SSE ``error`` event (docs/06 §5.1), and none carries prompt, completion or
provider message text (invariant 5). ``search_only`` tells the caller to degrade to ranked,
cited snippets instead of failing the question (FR-KB-011, NFR-AVL-004, docs/06 §15).
"""

from __future__ import annotations

from typing import ClassVar

from app.core.errors import BadRequest, DomainError, RateLimited, ServiceUnavailable


class GatewayError(DomainError):
    message_key: ClassVar[str] = "kb.errors.unavailable"
    search_only: ClassVar[bool] = True


class AiDisabled(GatewayError, ServiceUnavailable):
    """``SOS_KB_ENABLED`` is off (kill switch) or the school has AI switched off."""

    code = "ai_disabled"
    title = "AI answers are switched off"
    message_key = "kb.errors.disabled"


class BudgetExhausted(GatewayError, RateLimited):
    """The school's monthly AI budget is used up (docs/09: 429 ``ai_budget_exhausted``)."""

    code = "ai_budget_exhausted"
    title = "This month's AI budget is used up"
    message_key = "kb.errors.budget"


class AiRateLimited(GatewayError, RateLimited):
    """Too many model calls for this school and feature in the current minute."""

    code = "ai_rate_limited"
    title = "Too many AI requests. Wait a minute and try again."
    message_key = "kb.errors.rate_limited"


class ProviderUnavailable(GatewayError, ServiceUnavailable):
    """Timeout, outage, overload after retries, or the circuit breaker is open."""

    code = "ai_unavailable"
    title = "AI answers are temporarily unavailable"
    message_key = "kb.errors.unavailable"


class ProviderRejected(GatewayError, ServiceUnavailable):
    """The provider refused the request (4xx other than 429): a bug or configuration error."""

    code = "ai_request_rejected"
    title = "AI answers are temporarily unavailable"
    message_key = "kb.errors.unavailable"


class InvalidModelOutput(GatewayError, ServiceUnavailable):
    """Structured output did not match the schema, or a tool call named a tool not offered."""

    code = "ai_invalid_output"
    title = "The AI answer could not be used"
    message_key = "kb.errors.unavailable"


class GatewayMisuse(GatewayError, BadRequest):
    """A caller broke the gateway contract (offline role in product traffic, tool outside the
    ADR-0008 whitelist, too much tool-result context). A programming error, not user input."""

    code = "ai_gateway_misuse"
    title = "The AI request is not allowed"
    message_key = "kb.errors.unavailable"
    search_only = False


__all__ = [
    "AiDisabled",
    "AiRateLimited",
    "BudgetExhausted",
    "GatewayError",
    "GatewayMisuse",
    "InvalidModelOutput",
    "ProviderRejected",
    "ProviderUnavailable",
]
