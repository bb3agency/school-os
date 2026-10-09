"""SEC-007 / threat T4 with the application's own database rights (audit 2026-10-05 DP-01).

``test_tamper.py`` models a malicious DBA. These tests model a weaker and likelier attacker: code
running as ``sos_app`` (the role every request and job uses, which holds INSERT on
``audit.events`` and UPDATE on ``audit.chain_heads``), or ``sos_platform`` for the control-plane
chain. Before migration 0046 such a writer could append a correctly hashed event dated in the
past (for example into a day whose signed archive is already locked in S3), advance the head,
and the daily verification still reported the chain intact. It could also rewind the head.

Since 0046 the database itself accepts a new event only as the next link of the chain (seq =
head + 1, prev_hash = head hash) and only with a current timestamp, and the head only moves
forward by one onto the event just written.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit.hashing import chain_hash, platform_event_dict, tenant_event_dict
from app.audit.schemas import VerifyResult
from app.audit.service import record, record_platform, verify_chain, verify_platform_chain
from app.core.db import platform_session, tenant_session

pytestmark = pytest.mark.db

N = 3
_EVENT_COLUMNS = (
    "id, tenant_id, seq, occurred_at, actor_type, actor_id, action, resource_type, "
    "resource_id, summary, request_id, ip_hash, prev_hash, hash"
)


def _verify(tenant: uuid.UUID) -> VerifyResult:
    with tenant_session(tenant) as s:
        return verify_chain(s, tenant)


@pytest.fixture
def chain(tenant: uuid.UUID, record_events: Any) -> uuid.UUID:
    record_events(tenant, N)
    assert _verify(tenant) == VerifyResult(True, N)
    return tenant


def _forge(
    s: Session,
    tenant: uuid.UUID,
    *,
    occurred_at: datetime,
    seq_offset: int = 1,
    advance_head: bool = True,
) -> None:
    """Append a correctly hashed event the way a compromised app role could, bypassing record()."""
    head = s.execute(
        text("SELECT last_seq, last_hash FROM audit.chain_heads WHERE tenant_id = :t"),
        {"t": tenant},
    ).one()
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": tenant,
        "seq": int(head.last_seq) + seq_offset,
        "occurred_at": occurred_at,
        "actor_type": "user",
        "actor_id": uuid.uuid4(),
        "action": "tenant.export.downloaded",
        "resource_type": "tenant_export",
        "resource_id": uuid.uuid4(),
        "summary": {"format": "zip"},
        "request_id": "req-forged",
        "ip_hash": None,
        "prev_hash": bytes(head.last_hash),
    }
    row["hash"] = chain_hash(row["prev_hash"], tenant_event_dict(row))
    s.execute(
        text(
            f"INSERT INTO audit.events ({_EVENT_COLUMNS}) VALUES (:id, :tenant_id, :seq, "
            ":occurred_at, :actor_type, :actor_id, :action, :resource_type, :resource_id, "
            ":summary, :request_id, :ip_hash, :prev_hash, :hash)"
        ),
        {**row, "summary": json.dumps(row["summary"])},
    )
    if advance_head:
        s.execute(
            text("UPDATE audit.chain_heads SET last_seq = :s, last_hash = :h WHERE tenant_id = :t"),
            {"s": row["seq"], "h": row["hash"], "t": tenant},
        )


def test_SEC_007_app_role_cannot_append_a_backdated_event(chain: uuid.UUID) -> None:
    """Before 0046 this committed and ``verify_chain`` still returned ``ok`` with N + 1 events:
    a fabricated "export downloaded" three days ago, outside the locked archive of that day."""
    three_days_ago = datetime.now(UTC) - timedelta(days=3)
    with pytest.raises(DBAPIError, match="audit event"), tenant_session(chain) as s:
        _forge(s, chain, occurred_at=three_days_ago)
    assert _verify(chain) == VerifyResult(True, N)


def test_SEC_007_app_role_cannot_append_a_future_dated_event(chain: uuid.UUID) -> None:
    with pytest.raises(DBAPIError, match="audit event"), tenant_session(chain) as s:
        _forge(s, chain, occurred_at=datetime.now(UTC) + timedelta(days=1))
    assert _verify(chain) == VerifyResult(True, N)


@pytest.mark.parametrize("seq_offset", [0, 2])
def test_SEC_007_app_role_cannot_insert_an_event_that_is_not_the_next_link(
    chain: uuid.UUID, seq_offset: int
) -> None:
    """A duplicate seq (a fork) or a gap is refused when it is written, not only when verified."""
    with pytest.raises(DBAPIError, match="audit event"), tenant_session(chain) as s:
        _forge(s, chain, occurred_at=datetime.now(UTC), seq_offset=seq_offset, advance_head=False)
    assert _verify(chain) == VerifyResult(True, N)


def test_SEC_007_app_role_cannot_rewind_or_skip_the_chain_head(chain: uuid.UUID) -> None:
    for seq in (1, N + 5):
        with pytest.raises(DBAPIError, match="audit chain head"), tenant_session(chain) as s:
            s.execute(
                text("UPDATE audit.chain_heads SET last_seq = :s WHERE tenant_id = :t"),
                {"s": seq, "t": chain},
            )
    with pytest.raises(DBAPIError, match="audit chain head"), tenant_session(chain) as s:
        s.execute(
            text("UPDATE audit.chain_heads SET last_hash = :h WHERE tenant_id = :t"),
            {"h": b"\x01" * 32, "t": chain},
        )
    assert _verify(chain) == VerifyResult(True, N)


def test_SEC_007_record_still_appends_after_the_guard(chain: uuid.UUID) -> None:
    with tenant_session(chain) as s:
        record(s, action="student.value.recorded", resource_type="student", summary={"n": 1})
    assert _verify(chain) == VerifyResult(True, N + 1)


def _forge_platform(s: Session) -> None:
    """Append a backdated control-plane event as sos_platform could, bypassing record_platform()."""
    head = s.execute(text("SELECT last_seq, last_hash FROM platform.audit_chain_head")).one()
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "seq": int(head.last_seq) + 1,
        "occurred_at": datetime.now(UTC) - timedelta(days=3),
        "actor_type": "operator",
        "actor_id": uuid.uuid4(),
        "action": "platform.tenant.suspended",
        "resource_type": "tenant",
        "resource_id": uuid.uuid4(),
        "subject_tenant_id": uuid.uuid4(),
        "summary": {"reason_code": "non_payment"},
        "request_id": None,
        "ip_hash": None,
        "prev_hash": bytes(head.last_hash),
    }
    row["hash"] = chain_hash(row["prev_hash"], platform_event_dict(row))
    s.execute(
        text(
            "INSERT INTO platform.audit_events (id, seq, occurred_at, actor_type, actor_id, "
            "action, resource_type, resource_id, subject_tenant_id, summary, request_id, "
            "ip_hash, prev_hash, hash) VALUES (:id, :seq, :occurred_at, :actor_type, "
            ":actor_id, :action, :resource_type, :resource_id, :subject_tenant_id, :summary, "
            ":request_id, :ip_hash, :prev_hash, :hash)"
        ),
        {**row, "summary": json.dumps(row["summary"])},
    )
    s.execute(
        text("UPDATE platform.audit_chain_head SET last_seq = :s, last_hash = :h"),
        {"s": row["seq"], "h": row["hash"]},
    )


def test_SEC_007_platform_role_cannot_append_a_backdated_platform_event(
    platform_engine: Any,
) -> None:
    with pytest.raises(DBAPIError, match="audit event"), platform_session() as s:
        _forge_platform(s)
    with platform_session() as s:
        assert verify_platform_chain(s).ok


def test_SEC_007_platform_role_cannot_rewind_the_platform_head(platform_engine: Any) -> None:
    with platform_session() as s:
        record_platform(
            s,
            action="platform.tenant.suspended",
            resource_type="tenant",
            resource_id=uuid.uuid4(),
            summary={"reason_code": "non_payment"},
            actor_id=uuid.uuid4(),
        )
    with pytest.raises(DBAPIError, match="audit chain head"), platform_session() as s:
        s.execute(text("UPDATE platform.audit_chain_head SET last_seq = 0"))
    with platform_session() as s:
        assert verify_platform_chain(s).ok
