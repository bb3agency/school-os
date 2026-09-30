"""What every provider call cost, per tenant and feature (FR-KB-009, NFR-CST-001; docs/06 §14).

A :class:`MeteringEvent` carries ids, model, token counts, cost, latency and outcome, never
prompt or completion text (invariant 5). The gateway hands each event to a
:class:`MeteringSink` (the composition root persists it with the ``kb.queries`` row and feeds
the usage meters; tests record it) and sets the same values on the ``llm.call`` trace span.

Cost is at list price from ``models.yaml``: uncached input and output at the model's prices,
prompt-cache writes and reads at ``cache_price_multipliers`` times the input price.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from app.knowledge.config.llm import CachePriceMultipliers, ModelPrice
from app.knowledge.domain import Feature, ModelRole
from app.knowledge.gateway.wire import RawUsage

_MTOK = Decimal(1_000_000)

Outcome = Literal[
    "ok",
    "refused",
    "max_tokens",
    "invalid_output",
    "unavailable",
    "rejected",
    "cancelled",
]
"""``cancelled``: a streamed call closed early because the client went away (0029_kb_v2)."""


def cost_usd(price: ModelPrice, cache: CachePriceMultipliers, usage: RawUsage) -> Decimal:
    inp, out = price.input_usd_per_mtok, price.output_usd_per_mtok
    total = (
        usage.input_tokens * inp
        + usage.cache_write_tokens * inp * cache.write
        + usage.cache_read_tokens * inp * cache.read
        + usage.output_tokens * out
    ) / _MTOK
    return total.quantize(Decimal("0.000001"))


@dataclass(frozen=True, slots=True)
class MeteringEvent:
    tenant_id: uuid.UUID
    feature: Feature
    role: ModelRole
    query_id: uuid.UUID | None
    provider: str
    model: str
    outcome: Outcome
    attempts: int
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    cost_usd: Decimal
    month_spend_usd: Decimal | None
    """The tenant's IST-month spend after this call (None when the call cost nothing)."""
    document_id: uuid.UUID | None = None
    """The document the call served (contextual chunk headers, docs/06 §4.11), else None."""


class MeteringSink(Protocol):
    def record(self, event: MeteringEvent) -> None: ...


class RecordingSink:
    """Keeps events in memory (tests, local runs, the offline eval)."""

    def __init__(self) -> None:
        self.events: list[MeteringEvent] = []

    def record(self, event: MeteringEvent) -> None:
        self.events.append(event)


__all__ = ["MeteringEvent", "MeteringSink", "Outcome", "RecordingSink", "cost_usd"]
