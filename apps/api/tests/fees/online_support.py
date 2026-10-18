"""Online payment test objects (R2-2D; synthetic data only, the fake provider, no network).

Loaded by path from ``tests/fees`` and the security suites. Builds on ``support.py`` (schools with
fees on, accounts, counter receipts).

- :func:`connect` connects a school with synthetic manual keys through the real service (the
  pilot mode is switched on for the suite by ``tests/conftest.py``) and returns what the owner
  sees once: the webhook route, URL and secret.
- :func:`deliver` posts a Razorpay-shaped webhook body (from the recorded synthetic fixtures in
  ``app/fees/providers/fixtures``) to the route with a valid signature unless told otherwise.
- :func:`pay` makes a parent pay a link at the fake provider and delivers ``payment_link.paid``.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import itertools
import sys
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.fees import online
from app.fees.online_schemas import ManualKeysIn, PaymentIntentCreate
from app.fees.providers.base import Payment, PaymentLink
from app.fees.providers.fake import STATE, webhook_body
from app.fees.providers.razorpay import signature

F: Any = sys.modules["sos_test_fees_support"]
WEBHOOK = "/api/v1/webhooks/razorpay/"
_events = itertools.count(1)
_keys = itertools.count(1)


@dataclasses.dataclass
class Connected:
    connection_id: uuid.UUID
    route: str
    url: str
    secret: str
    key_id: str


def _letters(n: int) -> str:
    out = ""
    for _ in range(6):
        n, rest = divmod(n, 26)
        out = chr(ord("A") + rest) + out
    return out


def key_id() -> str:
    return f"rzp_test_SYNTH{_letters(next(_keys) + uuid.uuid4().int % 10_000)}"


def disconnect_if_connected(school: Any) -> None:
    with tenant_session(school.tenant_id) as s:
        found = s.execute(
            text("SELECT id FROM fin.payment_connections WHERE status <> 'disconnected'")
        ).first()
    if found is not None:
        F.call(school, "owner", online.disconnect)


SYNTHETIC_KEY_SECRET = "synthetic-key-secret-0001"


def connect(school: Any, *, secret: str = SYNTHETIC_KEY_SECRET) -> Connected:
    """A fresh manual-keys connection of ``school`` (any previous one is disconnected)."""
    disconnect_if_connected(school)
    kid = key_id()
    out = F.call(
        school,
        "owner",
        online.connect_manual,
        ManualKeysIn.model_validate({"key_id": kid, "key_secret": secret}),
    )
    url = out.webhook.url
    return Connected(
        connection_id=out.connection.id,
        route=url.rsplit("/", 1)[1],
        url=url,
        secret=out.webhook.secret,
        key_id=kid,
    )


def event_id() -> str:
    return f"evt_SYNTH{_letters(next(_events))}{uuid.uuid4().hex[:8]}"


def deliver(
    api: Any,
    conn: Connected,
    body: bytes,
    *,
    secret: str | None = None,
    event: str | None = None,
    sig: str | None = None,
    route: str | None = None,
) -> Any:
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Event-Id": event or event_id(),
        "X-Razorpay-Signature": (
            sig if sig is not None else signature((secret or conn.secret).encode(), body)
        ),
    }
    return api.client.post(WEBHOOK + (route or conn.route), content=body, headers=headers)


def link(
    school: Any, account_id: uuid.UUID, instalments: list[int] | None = None, **kw: Any
) -> Any:
    return F.call(
        school,
        kw.pop("role", "office_staff"),
        online.create_intent_for_staff,
        account_id,
        PaymentIntentCreate(instalments=instalments or [1], **kw),
    )


def fake_link(intent: Any) -> PaymentLink:
    found = STATE.link_for_reference(uuid.UUID(str(intent.id)).hex)
    assert found is not None
    return found


def pay(
    school: Any,
    api: Any,
    conn: Connected,
    intent: Any,
    *,
    amount: Decimal | None = None,
    deliver_event: bool = True,
    event: str | None = None,
) -> tuple[Payment, Any]:
    """The parent pays ``intent``'s link at the fake provider; ``payment_link.paid`` is
    delivered (unless told not to) and the inbox is processed."""
    plink = fake_link(intent)
    payment = STATE.pay(plink.id, amount=amount)
    res = None
    if deliver_event:
        body = webhook_body("payment_link.paid", link=STATE.links[plink.id], payment=payment)
        res = deliver(api, conn, body, event=event)
        process(school)
    return payment, res


def process(school: Any) -> dict[str, int]:
    out: dict[str, int] = online.process_events(school.tenant_id)
    return out


def scalar(admin: Engine, sql: str, **params: Any) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql), params).scalar()


def rows(admin: Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [dict(r) for r in c.execute(text(sql), params).mappings().all()]


def receipts_for_payment(admin: Engine, tenant_id: uuid.UUID, payment_id: str) -> int:
    return int(
        scalar(
            admin,
            "SELECT count(*) FROM fin.receipts WHERE tenant_id = :t AND provider_payment_id = :p",
            t=tenant_id,
            p=payment_id,
        )
    )


def items(admin: Engine, tenant_id: uuid.UUID, **filters: str) -> list[dict[str, Any]]:
    where = " AND ".join(f"{k} = :{k}" for k in filters)
    sql = "SELECT * FROM fin.reconciliation_items WHERE tenant_id = :t"
    if where:
        sql += " AND " + where
    return rows(admin, sql, t=tenant_id, **filters)


def age_previous_secret(admin: Engine, tenant_id: uuid.UUID) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "UPDATE fin.payment_connections SET previous_until = now() - interval '1 minute' "
                "WHERE tenant_id = :t AND previous_until IS NOT NULL"
            ),
            {"t": tenant_id},
        )


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
