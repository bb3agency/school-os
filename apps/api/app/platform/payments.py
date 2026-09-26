"""Payment providers (ADR-0016, Proposed; FR-PLT-018).

M0 has exactly one provider, ``manual``: a billing admin records a bank transfer, UPI payment,
cheque or other payment against an issued invoice. No provider SDK, no card data, no webhooks.
An online adapter (Razorpay candidate) needs ADR-0016 accepted plus a privacy review first.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.platform import models as m
from app.platform import repository as repo
from app.platform.schemas import PaymentIn


class PaymentProvider(Protocol):
    name: str

    def record(
        self,
        session: Session,
        *,
        invoice: RowMapping,
        data: PaymentIn,
        recorded_by: uuid.UUID,
    ) -> RowMapping:
        """Persist one payment against ``invoice`` (caller holds the invoice row lock)."""
        ...


class ManualProvider:
    """Payments received outside SchoolOS and recorded by an operator."""

    name = "manual"

    def record(
        self,
        session: Session,
        *,
        invoice: RowMapping,
        data: PaymentIn,
        recorded_by: uuid.UUID,
    ) -> RowMapping:
        return repo.insert_row(
            session,
            m.payments,
            {
                "id": new_id(),
                "invoice_id": invoice["id"],
                "tenant_id": invoice["tenant_id"],
                "provider": self.name,
                "method": data.method,
                "amount_inr": data.amount_inr,
                "tds_inr": data.tds_inr,
                "received_on": data.received_on,
                "reference": data.reference,
                "status": "recorded",
                "notes": data.notes,
                "recorded_by": recorded_by,
            },
        )


PROVIDERS: dict[str, PaymentProvider] = {"manual": ManualProvider()}


def get_provider(name: str = "manual") -> PaymentProvider:
    return PROVIDERS[name]
