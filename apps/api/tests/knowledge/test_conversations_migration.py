"""0038_ask_conversations: conversations, answer details, summaries and per-user memory.

FR-KB-009, FR-KB-012 (ADR-0033); CLAUDE.md invariants 1 and 12 (RLS on the new tables; the
migration round-trips on a populated database, the query log itself survives the downgrade).
Synthetic data only; ciphertext bytes are placeholders.
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
REVISION = "0038_ask_conversations"
PREVIOUS = "0037_notice_drafting"
DB = "schoolos_ask_conversations_migration"
NEW_TABLES = ("kb.conversations", "kb.user_memories", "kb.user_memory_settings")


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


def _exec(admin: Engine, sql: str, **params: Any) -> None:
    with admin.begin() as c:
        c.execute(text(sql), params)


def _scalar(admin: Engine, sql: str, **params: Any) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def _school(admin: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    _exec(
        admin,
        "INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'S', 'active')",
        i=tid,
        c=f"ac-{tid.hex[:10]}",
    )
    _exec(
        admin,
        "INSERT INTO core.users (id, idp_subject, display_name) VALUES (:u, :s, 'Synthetic')",
        u=uid,
        s=f"sub-{uid.hex}",
    )
    _exec(
        admin,
        "INSERT INTO core.memberships (id, tenant_id, user_id, status) "
        "VALUES (:m, :t, :u, 'active')",
        m=uuid.uuid4(),
        t=tid,
        u=uid,
    )
    return tid, uid


def _conversation(admin: Engine, tid: uuid.UUID, uid: uuid.UUID) -> uuid.UUID:
    cid = uuid.uuid4()
    _exec(
        admin,
        "INSERT INTO kb.conversations (id, tenant_id, user_id, title_ciphertext, key_version) "
        "VALUES (:i, :t, :u, :c, 1)",
        i=cid,
        t=tid,
        u=uid,
        c=b"\x01\x00\x01synthetic-title",
    )
    return cid


def _query(
    admin: Engine,
    tid: uuid.UUID,
    uid: uuid.UUID,
    session: uuid.UUID,
    *,
    conversation: uuid.UUID | None = None,
    route: str | None = None,
) -> uuid.UUID:
    qid = uuid.uuid4()
    # Without a conversation the statement names only 0037 columns (it also runs downgraded).
    extra, values = ("", "") if conversation is None else (", conversation_id", ", :c")
    _exec(
        admin,
        "INSERT INTO kb.queries (id, tenant_id, session_id, user_id, question_ciphertext, "
        f"question_hmac, key_version, mode, status, route{extra}) VALUES "
        f"(:i, :t, :s, :u, :q, :h, 1, 'full', 'answered', :r{values})",
        i=qid,
        t=tid,
        s=session,
        u=uid,
        q=b"synthetic-ciphertext",
        h=hashlib.sha256(qid.bytes).digest(),
        c=conversation,
        r=route,
    )
    return qid


def test_invariant_1_new_tables_force_rls_with_tenant_isolation(
    fresh: tuple[Config, Engine],
) -> None:
    _cfg, admin = fresh
    for table in NEW_TABLES:
        forced = _scalar(
            admin,
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid = "
            "CAST(:t AS regclass)",
            t=table,
        )
        assert forced, table
        policies = _scalar(
            admin,
            "SELECT array_agg(polname::text ORDER BY polname) FROM pg_policy "
            "WHERE polrelid = CAST(:t AS regclass)",
            t=table,
        )
        assert policies == ["offboarding_purge", "tenant_isolation"], table
    for table in ("kb.user_memories", "kb.user_memory_settings"):
        assert not _scalar(
            admin,
            "SELECT has_table_privilege('sos_readonly', CAST(:t AS regclass), 'SELECT')",
            t=table,
        )
    for table in NEW_TABLES:
        assert not _scalar(
            admin,
            "SELECT has_table_privilege('sos_platform', CAST(:t AS regclass), 'SELECT')",
            t=table,
        )


def test_FR_KB_012_conversation_checks(fresh: tuple[Config, Engine]) -> None:
    _cfg, admin = fresh
    tid, uid = _school(admin)
    cid = _conversation(admin, tid, uid)
    # A question of the conversation carries its id as the session id.
    _query(admin, tid, uid, cid, conversation=cid)
    with pytest.raises(IntegrityError):
        _query(admin, tid, uid, uuid.uuid4(), conversation=cid)
    # A deleted conversation keeps neither its title nor its summary.
    with pytest.raises(IntegrityError):
        _exec(admin, "UPDATE kb.conversations SET deleted_at = now() WHERE id = :i", i=cid)
    _exec(
        admin,
        "UPDATE kb.conversations SET deleted_at = now(), title_ciphertext = NULL WHERE id = :i",
        i=cid,
    )
    # A summary says what it covers.
    other = _conversation(admin, tid, uid)
    with pytest.raises(IntegrityError):
        _exec(
            admin,
            "UPDATE kb.conversations SET summary_ciphertext = :c WHERE id = :i",
            i=other,
            c=b"\x01\x00\x01summary",
        )
    # Another school's conversation cannot be referenced (composite FK).
    tid_b, uid_b = _school(admin)
    with pytest.raises(IntegrityError):
        _query(admin, tid_b, uid_b, other, conversation=other)


def test_ADR_0033_memory_checks(fresh: tuple[Config, Engine]) -> None:
    _cfg, admin = fresh
    tid, uid = _school(admin)

    def memory(**values: Any) -> None:
        row = {
            "i": uuid.uuid4(),
            "t": tid,
            "u": uid,
            "c": b"\x01\x00\x01memory",
            "s": "explicit",
            "st": "active",
            "x": None,
        } | values
        _exec(
            admin,
            "INSERT INTO kb.user_memories (id, tenant_id, user_id, text_ciphertext, key_version, "
            "source, status, expires_at) VALUES (:i, :t, :u, :c, 1, :s, :st, :x)",
            **row,
        )

    memory()
    memory(s="suggested", st="pending", x="2026-10-01T00:00:00Z")
    with pytest.raises(IntegrityError):  # an explicit item is never pending
        memory(s="explicit", st="pending", x="2026-10-01T00:00:00Z")
    with pytest.raises(IntegrityError):  # a pending item always expires
        memory(s="suggested", st="pending")
    with pytest.raises(IntegrityError):  # only members of the school have memories
        memory(u=uuid.uuid4())
    _exec(
        admin,
        "INSERT INTO kb.user_memory_settings (tenant_id, user_id, enabled) VALUES (:t, :u, false)",
        t=tid,
        u=uid,
    )
    # Leaving the school (the membership row goes) takes the memory with it.
    _exec(admin, "DELETE FROM core.memberships WHERE tenant_id = :t AND user_id = :u", t=tid, u=uid)
    assert _scalar(admin, "SELECT count(*) FROM kb.user_memories WHERE tenant_id = :t", t=tid) == 0
    assert (
        _scalar(admin, "SELECT count(*) FROM kb.user_memory_settings WHERE tenant_id = :t", t=tid)
        == 0
    )


def test_invariant_12_0038_round_trips_on_a_populated_database(
    fresh: tuple[Config, Engine],
) -> None:
    cfg, admin = fresh
    tid, uid = _school(admin)
    legacy = _query(admin, tid, uid, uuid.uuid4())
    cid = _conversation(admin, tid, uid)
    first = _query(admin, tid, uid, cid, conversation=cid)
    second = _query(admin, tid, uid, cid, conversation=cid, route="memory")
    _exec(
        admin,
        "UPDATE kb.queries SET superseded_by = :s, citations_ciphertext = :c WHERE id = :i",
        s=second,
        i=first,
        c=b"\x01\x00\x01citations",
    )
    _exec(
        admin,
        "UPDATE kb.queries SET revises = :f, revision = 'regenerate' WHERE id = :i",
        f=first,
        i=second,
    )
    _exec(
        admin,
        "INSERT INTO kb.llm_calls (id, tenant_id, feature, role, provider, model, outcome, "
        "attempts, latency_ms, input_tokens, output_tokens, cost_usd) VALUES "
        "(:i, :t, 'ask', 'followups', 'fake', 'claude-haiku-4-5-20251001', 'ok', 1, 5, 10, 2, 0)",
        i=uuid.uuid4(),
        t=tid,
    )
    _exec(
        admin,
        "INSERT INTO kb.user_memories (id, tenant_id, user_id, text_ciphertext, key_version, "
        "source, status, conversation_id, query_id) VALUES "
        "(:i, :t, :u, :c, 1, 'explicit', 'active', :cv, :q)",
        i=uuid.uuid4(),
        t=tid,
        u=uid,
        c=b"\x01\x00\x01memory",
        cv=cid,
        q=second,
    )

    command.downgrade(cfg, PREVIOUS)
    # The query log survives; rows written with a new route are kept.
    assert _scalar(admin, "SELECT count(*) FROM kb.queries WHERE tenant_id = :t", t=tid) == 3
    assert _scalar(admin, "SELECT route FROM kb.queries WHERE id = :i", i=second) == "memory"
    assert _scalar(admin, "SELECT to_regclass('kb.conversations') IS NULL")
    assert _scalar(admin, "SELECT to_regclass('kb.user_memories') IS NULL")
    with pytest.raises(IntegrityError):  # new rows must satisfy the old checks again
        _query(admin, tid, uid, uuid.uuid4(), route="memory")
    assert legacy

    command.upgrade(cfg, REVISION)
    cid = _conversation(admin, tid, uid)
    _query(admin, tid, uid, cid, conversation=cid, route="memory")
    with pytest.raises(IntegrityError):
        _query(admin, tid, uid, uuid.uuid4(), route="thinking")
