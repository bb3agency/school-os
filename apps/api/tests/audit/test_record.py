"""audit.record: same-transaction writes (FR-AUD-001), per-tenant hash chain (FR-AUD-003),
tenant isolation of audit rows (SEC-001, FR-TEN-002)."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator

import pytest
from app.audit.hashing import chain_hash, tenant_event_dict
from app.audit.schemas import ZERO_HASH, AuditEvent, SummaryError
from app.audit.service import AuditContextError, record, verify_chain
from app.core.db import context_free_session, tenant_session
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

pytestmark = pytest.mark.db

SCHEMA = "sos_test_audit_tx"


@pytest.fixture(scope="module")
def changes_table(admin_engine: Engine, app_engine: Engine) -> Iterator[str]:
    """A throwaway tenant table standing in for an audited business table."""
    with admin_engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA {SCHEMA} TO sos_app"))
        conn.execute(text(f"CREATE TABLE {SCHEMA}.things (id uuid PRIMARY KEY, tenant_id uuid)"))
        conn.execute(text(f"ALTER TABLE {SCHEMA}.things ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {SCHEMA}.things FORCE ROW LEVEL SECURITY"))
        conn.execute(
            text(
                f"CREATE POLICY tenant_isolation ON {SCHEMA}.things "
                "USING (tenant_id = core.current_tenant()) "
                "WITH CHECK (tenant_id = core.current_tenant())"
            )
        )
        conn.execute(text(f"GRANT SELECT, INSERT ON {SCHEMA}.things TO sos_app"))
    yield f"{SCHEMA}.things"
    with admin_engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))


def _record(s: object, **kw: object) -> AuditEvent:
    params: dict[str, object] = {
        "action": "student.created",
        "resource_type": "student",
        "resource_id": uuid.uuid4(),
        "summary": {"fields": ["dob", "gender"], "source": "manual_entry"},
    }
    params.update(kw)
    return record(s, **params)  # type: ignore[arg-type]


def _head(tenant: uuid.UUID) -> tuple[int, bytes] | None:
    with tenant_session(tenant) as s:
        row = s.execute(
            text("SELECT last_seq, last_hash FROM audit.chain_heads WHERE tenant_id = :t"),
            {"t": tenant},
        ).one_or_none()
    return None if row is None else (int(row[0]), bytes(row[1]))


def _seqs(tenant: uuid.UUID) -> list[int]:
    with tenant_session(tenant) as s:
        seqs: list[int] = list(
            s.execute(text("SELECT seq FROM audit.events ORDER BY seq")).scalars()
        )
    return seqs


# ---- FR-AUD-001: same transaction ------------------------------------------------------------


def test_FR_AUD_001_event_commits_with_the_change(
    tenant: uuid.UUID, changes_table: str, app_engine: Engine
) -> None:
    thing = uuid.uuid4()
    with tenant_session(tenant) as s:
        s.execute(text(f"INSERT INTO {changes_table} VALUES (:i, :t)"), {"i": thing, "t": tenant})
        event = _record(s, resource_id=thing)
    with tenant_session(tenant) as s:
        rows = s.execute(text("SELECT resource_id, seq FROM audit.events")).all()
        assert rows == [(thing, 1)]
        assert s.execute(text(f"SELECT count(*) FROM {changes_table}")).scalar_one() == 1
    assert event.seq == 1
    assert event.prev_hash == ZERO_HASH
    assert _head(tenant) == (1, event.hash)


def test_FR_AUD_001_rollback_removes_event_and_keeps_head(
    tenant: uuid.UUID, changes_table: str, app_engine: Engine
) -> None:
    with tenant_session(tenant) as s:
        first = _record(s)
    # The whole audited unit of work is the point of this test (PT012 is intentional).
    with pytest.raises(RuntimeError, match="business failure"), tenant_session(tenant) as s:  # noqa: PT012
        s.execute(
            text(f"INSERT INTO {changes_table} VALUES (:i, :t)"), {"i": uuid.uuid4(), "t": tenant}
        )
        _record(s)
        raise RuntimeError("business failure")
    assert _seqs(tenant) == [1]
    assert _head(tenant) == (1, first.hash)
    with tenant_session(tenant) as s:
        assert s.execute(text(f"SELECT count(*) FROM {changes_table}")).scalar_one() == 0
        second = _record(s)
    assert second.seq == 2, "a rolled-back event must not leave a gap"
    assert second.prev_hash == first.hash


def test_FR_AUD_001_record_requires_tenant_context(app_engine: Engine) -> None:
    with pytest.raises(AuditContextError), context_free_session() as s:
        _record(s)


def test_FR_AUD_001_user_actor_defaults_to_session_user(
    tenant: uuid.UUID, app_engine: Engine
) -> None:
    user = uuid.uuid4()
    with tenant_session(tenant, user) as s:
        event = _record(s)
        system = _record(s, actor_type="system")
    assert event.actor_id == user
    assert event.actor_type == "user"
    assert system.actor_id is None


# ---- FR-AUD-003: hash chain ---------------------------------------------------------------------


def test_FR_AUD_003_stored_event_matches_its_hash(tenant: uuid.UUID, record_events: object) -> None:
    events: list[AuditEvent] = record_events(tenant, 3)  # type: ignore[operator]
    with tenant_session(tenant) as s:
        rows = s.execute(text("SELECT * FROM audit.events ORDER BY seq")).mappings().all()
    prev = ZERO_HASH
    for row, event in zip(rows, events, strict=True):
        assert bytes(row["prev_hash"]) == prev
        assert chain_hash(prev, tenant_event_dict(row)) == bytes(row["hash"]) == event.hash
        assert row["occurred_at"] == event.occurred_at, "timestamp must round-trip exactly"
        prev = bytes(row["hash"])


def test_FR_AUD_003_concurrent_writers_get_contiguous_seq(
    tenant: uuid.UUID, app_engine: Engine
) -> None:
    n = 50
    barrier = threading.Barrier(n)
    seqs: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            barrier.wait(timeout=30)
            with tenant_session(tenant) as s:
                ev = _record(s)
            with lock:
                seqs.append(ev.seq)
        except BaseException as exc:  # collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert errors == []
    assert sorted(seqs) == list(range(1, n + 1))
    assert _seqs(tenant) == list(range(1, n + 1))
    with tenant_session(tenant) as s:
        result = verify_chain(s, tenant)
    assert result.ok, result
    assert result.checked == n


def test_FR_AUD_003_tenants_have_independent_chains(
    tenant_ids: tuple[uuid.UUID, uuid.UUID], app_engine: Engine
) -> None:
    a, b = tenant_ids
    for _ in range(3):
        for t in (a, b):
            with tenant_session(t) as s:
                _record(s)
    for t in (a, b):
        assert _seqs(t) == [1, 2, 3]
        with tenant_session(t) as s:
            first: bytes = s.execute(
                text("SELECT prev_hash FROM audit.events WHERE seq = 1")
            ).scalar_one()
            assert bytes(first) == ZERO_HASH
            assert verify_chain(s, t).ok


# ---- isolation -----------------------------------------------------------------------------------


def test_SEC_001_tenant_cannot_read_other_tenant_audit(
    tenant_ids: tuple[uuid.UUID, uuid.UUID], app_engine: Engine
) -> None:
    a, b = tenant_ids
    with tenant_session(a) as s:
        _record(s)
    with tenant_session(b) as s:
        for table in ("audit.events", "audit.chain_heads"):
            n = s.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id = :a"), {"a": a})
            assert n.scalar_one() == 0
        with pytest.raises(AuditContextError):
            verify_chain(s, a)


def test_SEC_001_no_context_sees_no_audit_rows(tenant: uuid.UUID, app_engine: Engine) -> None:
    with tenant_session(tenant) as s:
        _record(s)
    with context_free_session() as s:
        assert s.execute(text("SELECT count(*) FROM audit.events")).scalar_one() == 0
        assert s.execute(text("SELECT count(*) FROM audit.chain_heads")).scalar_one() == 0


def test_SEC_001_cannot_write_audit_for_another_tenant(
    tenant_ids: tuple[uuid.UUID, uuid.UUID], app_engine: Engine
) -> None:
    a, b = tenant_ids
    with pytest.raises(ProgrammingError, match="row-level security"), tenant_session(b) as s:
        s.execute(
            text(
                "INSERT INTO audit.events (id, tenant_id, seq, actor_type, action, "
                "resource_type, summary, prev_hash, hash) VALUES (:i, :a, 1, 'system', "
                "'x.y', 'student', '{}', :h, :h)"
            ),
            {"i": uuid.uuid4(), "a": a, "h": ZERO_HASH},
        )


# ---- input validation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"summary": {"name": "Synthetic Student"}},
        {"summary": {"guardian_phone": "masked"}},
        {"summary": {"note": "x" * 201}},
        {"summary": {"ref": "call 9876543210"}},
        {"action": "created"},
        {"action": "Student.Created"},
        {"resource_type": "Student"},
        {"request_id": "bad id with spaces"},
        {"actor_type": "robot"},
    ],
)
def test_FR_AUD_001_invalid_input_is_rejected_before_writing(
    tenant: uuid.UUID, app_engine: Engine, kwargs: dict[str, object]
) -> None:
    with pytest.raises((ValidationError, SummaryError)), tenant_session(tenant) as s:
        _record(s, **kwargs)
    assert _head(tenant) is None
