"""Conversations and memory under key rotation and retention (SEC-012, FR-KB-009; ADR-0033).

- DEK rotation re-encrypts conversation titles and summaries, memory items and the new
  per-question ciphertext (citations, follow-ups), with the same associated data; the census
  counts them; conversations keep their activity time and ETag version.
- The 180-day query purge takes conversations with their questions: a conversation left with
  no question is deleted, a summary covering a purged question is forgotten.
- Questions asked before conversations existed get one (``adopt_conversations``), never joined
  to another person's history.
Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.crypto import ciphertext_key_version, generate_tenant_keys
from app.core.db import tenant_session
from app.knowledge import conversations, keys, memory, service, tasks
from app.knowledge import repository as repo
from app.knowledge.keys import QUESTION_PURPOSE, question_key
from app.students import crypto, rotation

pytestmark = pytest.mark.db


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


K = _load("sos_test_ask_support", Path(__file__).with_name("ask_support.py"))
W = K.W
world = W.world

NOW = dt.datetime(2026, 9, 30, 6, 0, tzinfo=dt.UTC)


def _conversation(
    s: Any, user_id: uuid.UUID, *, summary: str | None = None, **kw: Any
) -> uuid.UUID:
    cid = uuid.uuid4()
    title, version = conversations.seal(
        s, "Synthetic title", table="kb.conversations", column="title_ciphertext", row_id=cid
    )
    values: dict[str, Any] = {
        "id": cid,
        "user_id": user_id,
        "title_ciphertext": title,
        "key_version": version,
    } | kw
    if summary is not None:
        blob, _ = conversations.seal(
            s, summary, table="kb.conversations", column="summary_ciphertext", row_id=cid
        )
        values |= {
            "summary_ciphertext": blob,
            "summary_oldest_at": kw.get("created_at", NOW),
            "summary_through": kw.get("created_at", NOW),
        }
    repo.insert_conversation(s, values)
    return cid


def _question(
    s: Any,
    user_id: uuid.UUID,
    question: str,
    *,
    session_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    asked_at: dt.datetime = NOW,
    details: bool = False,
) -> uuid.UUID:
    qid = uuid.uuid4()
    q, version = crypto.encrypt_value(
        s, question, table="kb.queries", column="question_ciphertext", row_id=qid
    )
    h, _ = crypto.blind_index(
        s, question_key(question), purpose=QUESTION_PURPOSE, key_version=version
    )
    values: dict[str, Any] = {
        "id": qid,
        "session_id": conversation_id or session_id or uuid.uuid4(),
        "conversation_id": conversation_id,
        "user_id": user_id,
        "question_ciphertext": q,
        "question_hmac": h,
        "key_version": version,
        "mode": "full",
        "status": "answered",
        "created_at": asked_at,
    }
    if details:
        cites, _ = conversations.seal(
            s,
            json.dumps([{"index": 1, "source": "sos://doc/x", "title": "T", "snippet": "S"}]),
            table="kb.queries",
            column="citations_ciphertext",
            row_id=qid,
        )
        follow, _ = conversations.seal(
            s, json.dumps(["Next?"]), table="kb.queries", column="followups_ciphertext", row_id=qid
        )
        values |= {"citations_ciphertext": cites, "followups_ciphertext": follow}
    repo.insert_query(s, values)
    return qid


def _new_key(admin: Engine, tenant: uuid.UUID) -> None:
    dek, mac = generate_tenant_keys(tenant, W.wrapper())
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.tenant_keys (tenant_id, key_version, wrapped_dek, wrapped_hmac, "
                "kms_key_arn) VALUES (:t, 2, :d, :h, 'local-dev:test')"
            ),
            {"t": tenant, "d": dek, "h": mac},
        )
    crypto.get_keyring().clear()


def test_SEC_012_conversations_memory_and_answer_details_are_registered_and_counted() -> None:
    names = rotation.reencryptor_names()
    assert {"kb_queries", "kb_conversations", "kb_memories"} <= set(names)
    assert {
        ("kb.queries", "citations_ciphertext"),
        ("kb.queries", "followups_ciphertext"),
        ("kb.conversations", "title_ciphertext"),
        ("kb.conversations", "summary_ciphertext"),
        ("kb.user_memories", "text_ciphertext"),
    } <= set(rotation.CIPHERTEXT_COLUMNS)


def test_SEC_012_rotation_reencrypts_titles_summaries_memory_and_answer_details(
    world: Any, admin_engine: Engine
) -> None:
    K.SW.configure_keyring()
    tenant = W.provision_school()
    person = W.add_member(admin_engine, tenant, ["office_staff"])
    with tenant_session(tenant) as s:
        cid = _conversation(s, person.user_id, summary="Synthetic summary")
        qid = _question(s, person.user_id, "Synthetic question?", conversation_id=cid, details=True)
        item = memory.insert(
            s,
            user_id=person.user_id,
            text="Keep answers short",
            source="explicit",
            status="active",
            now=NOW,
        )
    with admin_engine.connect() as c:
        before: Any = c.execute(
            text("SELECT updated_at, version FROM kb.conversations WHERE id = :i"), {"i": cid}
        ).one()
    with tenant_session(tenant) as s:
        assert rotation.census(s) == {1: 6}
    _new_key(admin_engine, tenant)
    for _ in range(2):  # the second pass has nothing left
        with tenant_session(tenant) as s:
            rotation.reencrypt_batch(s, batch_size=50)
    with tenant_session(tenant) as s:
        assert rotation.census(s) == {2: 6}
        conversation = repo.get_conversation(s, cid, person.user_id)
        assert conversation is not None
        assert conversations.title_of(s, conversation) == "Synthetic title"
        assert (
            conversations.unseal(
                s,
                conversation.summary_ciphertext,
                table="kb.conversations",
                column="summary_ciphertext",
                row_id=cid,
            )
            == "Synthetic summary"
        )
        row = repo.query_by_id(s, qid)
        assert row is not None
        assert conversations.stored_followups(s, row) == ("Next?",)
        assert conversations.stored_citations(s, row)[0].title == "T"
        assert memory.text_of(s, item) == "Keep answers short"
        stored = repo.get_memory(s, item.id, person.user_id, NOW)
        assert stored is not None
        assert ciphertext_key_version(bytes(stored.text_ciphertext)) == 2
        assert keys.reencrypt_conversations_batch(s, crypto.get_keyring(), 2, 10) == 0
        assert keys.reencrypt_memories_batch(s, crypto.get_keyring(), 2, 10) == 0
    with admin_engine.connect() as c:
        after: Any = c.execute(
            text("SELECT updated_at, version FROM kb.conversations WHERE id = :i"), {"i": cid}
        ).one()
    assert tuple(after) == tuple(before)  # re-encryption is not activity


def test_FR_KB_009_purge_takes_conversations_with_their_questions(
    world: Any, admin_engine: Engine
) -> None:
    K.SW.configure_keyring()
    tenant = W.provision_school()
    person = W.add_member(admin_engine, tenant, ["office_staff"])
    old = NOW - dt.timedelta(days=181)
    with tenant_session(tenant) as s:
        gone = _conversation(s, person.user_id, created_at=old)
        _question(s, person.user_id, "Old question?", conversation_id=gone, asked_at=old)
        kept = _conversation(s, person.user_id, summary="Covers the old one", created_at=old)
        _question(s, person.user_id, "Old too?", conversation_id=kept, asked_at=old)
        _question(s, person.user_id, "Recent?", conversation_id=kept, asked_at=NOW)
        assert service.purge_old_queries(s, now=NOW) == 2
    with admin_engine.connect() as c:
        rows = {
            r.id: r
            for r in c.execute(
                text("SELECT id, summary_ciphertext FROM kb.conversations WHERE tenant_id = :t"),
                {"t": tenant},
            )
        }
    assert gone not in rows
    assert rows[kept].summary_ciphertext is None


def test_FR_KB_012_questions_asked_before_conversations_are_adopted_per_person(
    world: Any, admin_engine: Engine
) -> None:
    K.SW.configure_keyring()
    tenant = W.provision_school()
    first, second = (W.add_member(admin_engine, tenant, ["office_staff"]) for _ in range(2))
    shared = uuid.uuid4()
    with tenant_session(tenant) as s:
        early = _question(
            s,
            first.user_id,
            "When is sports day?",
            session_id=shared,
            asked_at=NOW - dt.timedelta(1),
        )
        later = _question(s, first.user_id, "Where is it?", session_id=shared, asked_at=NOW)
        theirs = _question(s, second.user_id, "Someone else's?", session_id=shared, asked_at=NOW)
        own = _question(s, second.user_id, "Their own session?", asked_at=NOW)
    result = tasks.tidy_conversations_all()
    assert result["conversations_adopted"] >= 2
    with tenant_session(tenant) as s:
        mine = repo.get_conversation(s, shared, first.user_id)
        assert mine is not None
        assert conversations.title_of(s, mine) == "When is sports day?"
        assert [r.id for r in repo.conversation_messages(s, shared, first.user_id)] == [
            early,
            later,
        ]
        # The shared session id is the first person's conversation: the other person's question
        # in it is never joined to it (FR-KB-012) and stays in the query log only.
        assert repo.get_conversation(s, shared, second.user_id) is None
        row = repo.query_by_id(s, theirs)
        assert row is not None
        assert row.conversation_id is None
        own_row = repo.query_by_id(s, own)
        assert own_row is not None
        assert own_row.conversation_id == own_row.session_id
        assert service.adopt_conversations(s) == 0  # idempotent
