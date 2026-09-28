"""0029_kb_v2: streamed/cancelled question states, the cancelled ledger outcome, the session index.

FR-KB-008, FR-KB-009, FR-KB-012; CLAUDE.md invariant 12 (expand-only; the downgrade restores the
old checks as NOT VALID, keeping rows already in a new state, and refuses new ones).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.db
REVISION = "0029_kb_v2"
PREVIOUS = "0028_profile_scope"
DB = "schoolos_kb_v2_migration"


@pytest.fixture
def fresh(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> Iterator[tuple[Config, Engine]]:
    url = test_database.create_fresh(DB)
    cfg = make_alembic_config(url)
    command.upgrade(cfg, "head")
    admin = create_engine(
        make_url(test_database.admin_url).set(database=DB).render_as_string(hide_password=False)
    )
    yield cfg, admin
    admin.dispose()


def _tenant(admin: Engine) -> uuid.UUID:
    tid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'S', 'active')"
            ),
            {"i": tid, "c": f"kv2-{tid.hex[:10]}"},
        )
    return tid


def _query(admin: Engine, tid: uuid.UUID, status: str) -> uuid.UUID:
    qid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.queries (id, tenant_id, session_id, user_id, question_ciphertext, "
                "question_hmac, key_version, mode, status) VALUES (:i, :t, :s, :u, :q, :h, 1, "
                "'full', :st)"
            ),
            {
                "i": qid,
                "t": tid,
                "s": uuid.uuid4(),
                "u": uuid.uuid4(),
                "q": b"synthetic-ciphertext",
                "h": hashlib.sha256(qid.bytes).digest(),
                "st": status,
            },
        )
    return qid


def _call(admin: Engine, tid: uuid.UUID, outcome: str) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.llm_calls (id, tenant_id, feature, role, provider, model, outcome, "
                "attempts, latency_ms, input_tokens, output_tokens, cost_usd) VALUES "
                "(:i, :t, 'ask', 'answer', 'fake', 'claude-sonnet-5', :o, 1, 5, 10, 2, 0.0001)"
            ),
            {"i": uuid.uuid4(), "t": tid, "o": outcome},
        )


def _scalar(admin: Engine, sql: str, **params: Any) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def test_invariant_12_0029_round_trips_on_a_populated_database(
    fresh: tuple[Config, Engine],
) -> None:
    cfg, admin = fresh
    tid = _tenant(admin)
    streaming = _query(admin, tid, "streaming")
    cancelled = _query(admin, tid, "cancelled")
    answered = _query(admin, tid, "answered")
    _call(admin, tid, "cancelled")
    _call(admin, tid, "ok")
    assert _scalar(admin, "SELECT count(*) FROM pg_indexes WHERE indexname = 'queries_session'")

    command.downgrade(cfg, PREVIOUS)
    statuses = {
        qid: _scalar(admin, "SELECT status FROM kb.queries WHERE id = :i", i=qid)
        for qid in (streaming, cancelled, answered)
    }
    assert statuses == {streaming: "streaming", cancelled: "cancelled", answered: "answered"}
    assert _scalar(admin, "SELECT count(*) FROM kb.llm_calls WHERE outcome = 'cancelled'") == 1
    with pytest.raises(IntegrityError):
        _query(admin, tid, "streaming")
    with pytest.raises(IntegrityError):
        _call(admin, tid, "cancelled")
    assert not _scalar(admin, "SELECT count(*) FROM pg_indexes WHERE indexname = 'queries_session'")

    command.upgrade(cfg, REVISION)
    _query(admin, tid, "streaming")
    _call(admin, tid, "cancelled")
    with pytest.raises(IntegrityError):
        _query(admin, tid, "thinking")
