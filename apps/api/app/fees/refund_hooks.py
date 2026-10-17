"""Online refunds from the maker-checker flow (FR-PAY-011; internal to ``app.fees``).

An approved ``original_method`` refund is sent to the provider by the worker (outbox event
:data:`SEND_EVENT`, task ``fees.send_online_refund``), never inside the approver's request; the
voucher and the ``refund`` ledger entries are written when the provider confirms the refund
(webhook ``refund.processed`` or a re-fetch). This module depends only on the online repository
and the outbox, so the approvals code can call it without importing the payments service.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy.orm import Session

from app.fees import online_repository as orepo
from app.ops import service as ops

SEND_EVENT: Final = "fees.refund.send"
SEND_TASK: Final = "fees.send_online_refund"

ops.register_outbox_route(SEND_EVENT, SEND_TASK)


def can_refund_online(session: Session) -> bool:
    """Whether the school has an active provider connection to refund through."""
    connection = orepo.live_connection(session)
    return connection is not None and connection.status == "active"


def queue_online_refund(session: Session, refund_id: uuid.UUID) -> None:
    ops.enqueue_event(session, SEND_EVENT, {"refund_id": str(refund_id)})


__all__ = ["SEND_EVENT", "SEND_TASK", "can_refund_online", "queue_online_refund"]
