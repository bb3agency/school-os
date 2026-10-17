"""Closing open payment links when their dues change (FR-PAY-006; internal to ``app.fees``).

Any receipt on instalments that an open payment link asks for closes that link at once in the
same transaction (status ``cancelled``), so a parent cannot pay a second time for what the
counter already took; the worker then cancels the link at the provider
(``fees.cancel_payment_links``, outbox event :data:`CANCEL_EVENT`). A payment that still arrives
for a closed link becomes a reconciliation exception, never a second receipt (FR-PAY-009).

This module depends only on the online repository and the outbox, so the counter's receipt code
can call it without importing the online payments service.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from typing import Final

from sqlalchemy.orm import Session

from app.fees import online_repository as orepo
from app.ops import service as ops

CANCEL_EVENT: Final = "fees.payment_links.cancel"
CANCEL_TASK: Final = "fees.cancel_payment_links"

ops.register_outbox_route(CANCEL_EVENT, CANCEL_TASK)


def close_overlapping(
    session: Session,
    account_id: uuid.UUID,
    instalments: Collection[int],
    *,
    keep: uuid.UUID | None = None,
    cancelled_by: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Close every open link of ``account_id`` that asks for any of ``instalments`` (except
    ``keep``) and queue their cancellation at the provider. Returns the closed intent ids."""
    wanted = set(instalments)
    if not wanted:
        return []
    now = orepo.now(session)
    closed: list[uuid.UUID] = []
    for intent in orepo.open_intents(session, account_id, lock=True):
        if intent.id == keep or not wanted.intersection(intent.instalments):
            continue
        orepo.update_intent(
            session,
            intent.id,
            {
                "status": "cancelled",
                "cancelled_at": now,
                "cancelled_by": cancelled_by,
                "closed_at": now,
            },
        )
        closed.append(intent.id)
    if closed:
        ops.enqueue_event(session, CANCEL_EVENT, {"intent_ids": [str(i) for i in closed]})
    return closed


__all__ = ["CANCEL_EVENT", "CANCEL_TASK", "close_overlapping"]
