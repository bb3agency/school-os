"""Control-plane audit chain (platform.audit_events): append-only, hash-chained, privilege
separated (SEC-007, FR-AUD-002, FR-AUD-003, ADR-0013; FR-PLT-029 builds the viewer on it).

The platform chain is global, so tamper tests run inside a superuser transaction that is rolled
back (verification uses the same session) and never leave the shared chain broken.
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

import pytest
from app.audit.schemas import SummaryError
from app.audit.service import record_platform, verify_platform_chain
from app.core.db import platform_session, tenant_session
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.db


def _record(**kw: object) -> int:
    params: dict[str, object] = {
        "action": "platform.tenant.suspended",
        "resource_type": "tenant",
        "resource_id": uuid.uuid4(),
        "summary": {"reason_code": "non_payment"},
        "actor_id": uuid.uuid4(),
        "subject_tenant_id": uuid.uuid4(),
    }
    params.update(kw)
    with platform_session() as s:
        return record_platform(s, **params).seq  # type: ignore[arg-type]


def test_FR_AUD_003_platform_chain_appends_and_verifies(platform_engine: Engine) -> None:
    first = _record()
    second = _record(actor_type="system", actor_id=None)
    assert second == first + 1
    with platform_session() as s:
        result = verify_platform_chain(s)
        head: int = s.execute(text("SELECT last_seq FROM platform.audit_chain_head")).scalar_one()
    assert result.ok, result
    assert head == second


def test_FR_AUD_003_platform_concurrent_writers_contiguous(platform_engine: Engine) -> None:
    n = 20
    barrier = threading.Barrier(n)
    seqs: list[int] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=30)
            seqs.append(_record())
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert errors == []
    assert sorted(seqs) == list(range(min(seqs), min(seqs) + n))
    with platform_session() as s:
        assert verify_platform_chain(s).ok


def test_FR_AUD_001_platform_summary_is_validated(platform_engine: Engine) -> None:
    with pytest.raises((ValidationError, SummaryError), match="personal data"):
        _record(summary={"email": "x"})
    with pytest.raises(ValidationError):
        _record(actor_type="user")


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE platform.audit_events SET action = 'x.y'",
        "DELETE FROM platform.audit_events",
        "TRUNCATE platform.audit_events",
        "DELETE FROM platform.audit_chain_head",
        "INSERT INTO platform.audit_chain_head (id, last_seq, last_hash) VALUES (true, 0, '')",
    ],
)
def test_FR_AUD_002_platform_role_cannot_mutate(platform_engine: Engine, sql: str) -> None:
    _record()
    with pytest.raises(ProgrammingError, match="permission denied"), platform_session() as s:
        s.execute(text(sql))


def test_ADR_0013_app_role_cannot_read_platform_audit(app_engine: Engine) -> None:
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(uuid.uuid4()) as s,
    ):
        s.execute(text("SELECT count(*) FROM platform.audit_events"))


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE platform.audit_events SET action = 'x.y'",
        "DELETE FROM platform.audit_events",
        "TRUNCATE platform.audit_events",
    ],
)
def test_FR_AUD_002_platform_trigger_blocks_owner(
    platform_engine: Engine, owner_conn: Any, sql: str
) -> None:
    _record()
    with owner_conn() as conn, pytest.raises(DBAPIError, match="append-only"):
        conn.execute(text(sql))


def test_SEC_007_platform_tamper_detected(platform_engine: Engine, admin_engine: Engine) -> None:
    seq = _record()
    with Session(admin_engine) as s:
        s.begin()
        try:
            s.execute(text("SET LOCAL session_replication_role = replica"))
            assert verify_platform_chain(s).ok
            s.execute(
                text("UPDATE platform.audit_events SET summary = :s WHERE seq = :q"),
                {"s": json.dumps({"reason_code": "requested"}), "q": seq},
            )
            result = verify_platform_chain(s)
            assert (result.ok, result.first_bad_seq, result.reason) == (
                False,
                seq,
                "hash_mismatch",
            )
        finally:
            s.rollback()
    with platform_session() as s:
        assert verify_platform_chain(s).ok
