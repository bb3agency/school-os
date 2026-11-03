"""FR-PUB-006 with online payments: an expired parent dues handle that a payment intent still
references (intents are append-only, the reference has no ON DELETE action, 0059) is kept, and
the school's other expired handles are still purged."""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.fees import service as fees
from app.public import tasks as public_tasks

pytestmark = pytest.mark.db
F: Any = sys.modules["sos_test_fees_support"]


def _expired_handle(admin: Engine, tenant_id: uuid.UUID) -> uuid.UUID:
    handle_id = uuid.uuid4()
    now = dt.datetime.now(dt.UTC)
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.public_handles (id, tenant_id, kind, key_hash, target_type, "
                "target_id, created_at, expires_at) VALUES (:i, :t, 'parent_dues', :h, "
                "'fee_account', :ti, :c, :e)"
            ),
            {
                "i": handle_id,
                "t": tenant_id,
                "h": hashlib.sha256(secrets.token_bytes(16)).digest(),
                "ti": uuid.uuid4(),
                "c": now - dt.timedelta(days=60),
                "e": now - dt.timedelta(days=31),
            },
        )
    return handle_id


def test_FR_PUB_006_purge_keeps_handles_of_payment_intents_and_deletes_the_rest(
    school: Any, admin_engine: Engine
) -> None:
    online = sys.modules["sos_test_fees_online_support"]
    sid = F.student(school)
    F.account(school, sid)
    online.connect(school)
    paid_from = _expired_handle(admin_engine, school.tenant_id)
    unused = _expired_handle(admin_engine, school.tenant_id)
    with tenant_session(school.tenant_id) as s:
        account_id = fees.account_summary(s, [sid])[sid].account_id
        fees.create_payment_intent(
            s, account_id, [1], created_via="parent_page", public_handle_id=paid_from
        )
    with tenant_session(school.tenant_id) as s:
        assert paid_from in fees.public_handle_ids_in_use(s)
    totals = public_tasks.purge_all()
    assert totals["failed"] == 0
    with admin_engine.connect() as c:
        left: set[uuid.UUID] = set(
            c.execute(
                text("SELECT id FROM core.public_handles WHERE id = ANY(:i)"),
                {"i": [paid_from, unused]},
            ).scalars()
        )
    assert left == {paid_from}
